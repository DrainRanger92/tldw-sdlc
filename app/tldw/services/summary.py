"""Сервис конспекта: видео → проверка порога → транскрипт → LLM → заметка.

Модуль связывает адаптеры YouTube и LLM по ADR-002: сам не импортирует
``httpx``, ``youtube-transcript-api`` или ``yt-dlp`` и не ходит в сеть,
поэтому тесты подменяют источник видео и суммаризатор фейками.

Порядок шагов фиксирован и важен для поведения при ошибках:

1. :meth:`SummaryService.process` получает видео у источника (``fetch(url)``);
2. сразу проверяет длительность по порогу из конфига (``Settings.
   max_video_duration_seconds``, по умолчанию 2 часа) и при превышении
   поднимает :class:`VideoTooLongError` — **до** вызова LLM и **до** записи
   файла, поэтому частичной заметки не остаётся;
3. склеивает транскрипт с таймкодами (:func:`join_transcript`); пустой или
   вырожденный материал отвергается :class:`EmptyTranscriptError` до LLM —
   конспект по одним метаданным пользователю не нужен;
4. вызывает LLM, собирает :class:`~tldw.services.note.Note` из метаданных
   видео и ответа (:func:`build_note`);
5. рендерит ``.md`` по шаблону T-002 и при заданном ``data_dir`` атомарно
   сохраняет его через :func:`tldw.storage.notes.save_note`.

Активный шаблон пользователя (T-005, ``specs/002-user-note-templates``).
:meth:`SummaryService.process` принимает необязательный снимок
:class:`~tldw.services.template.ActiveTemplate`:

* ``template=None`` — прежний встроенный путь: тот же промпт, та же заметка,
  тот же файл, а суммаризатор вызывается без ``sections``/``tone``, поэтому
  фейки с прежней сигнатурой продолжают работать;
* шаблон задан — его разделы и тон уходят в суммаризатор одним вызовом LLM,
  а заметка собирается из провалидированного ответа по T-004 в порядке
  шаблона (:func:`build_note` с динамическими разделами).

Снимок снимается **один раз в начале обработки**: разделы, тон и
происхождение копируются в неизменяемое значение до первого тяжёлого вызова,
поэтому смена сохранённого шаблона во время генерации не меняет результат.
В лог попадают только происхождение шаблона и число разделов — без текста
заголовков и тона.

Граница порога: видео с ``duration_seconds == max_duration_seconds``
допускается, отказ начинается только со следующей секунды. Неизвестная
длительность (``None``) не блокирует обработку — адаптер YouTube считает
метаданные необязательными.

Усечение транскрипта. Промпт и заметка не должны раздуваться бесконечно,
поэтому текст с таймкодами ограничен :data:`DEFAULT_MAX_TRANSCRIPT_CHARS`
(примерно 25–30 тыс. токенов). Если сегменты не влезают:

- усечение происходит **по границам сегментов**, а не внутри строки, так что
  каждая оставшаяся строка сохраняет таймкод ``[MM:SS]`` / ``[HH:MM:SS]``;
  если даже первый сегмент не влезает, сохраняется его таймкод, а текст
  обрезается с многоточием (вырожденный случай очень маленького лимита);
- последней строкой добавляется маркер
  :data:`TRANSCRIPT_TRUNCATION_MARKER` с числом использованных и всего
  сегментов — это документирует усечение для LLM и для логов;
- маркер и оставшиеся строки вместе не длиннее лимита, а JSON-схема ответа
  LLM не меняется, поэтому усечение не ломает разбор ответа.

Сервис синхронный, как и адаптеры: Telegram-слой уводит его в отдельный
поток, чтобы не блокировать event loop.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn, Protocol

from tldw.config import DEFAULT_MAX_VIDEO_DURATION_SECONDS, Settings
from tldw.errors import UserFacingError
from tldw.services.note import (
    Note,
    NoteSection,
    Quote,
    TimelineEntry,
    format_timecode,
    note_filename,
    render_note,
)
from tldw.services.template import (
    BUILTIN_SECTIONS,
    ActiveTemplate,
    TemplateOrigin,
)
from tldw.storage.notes import save_note
from tldw.text import one_line

if TYPE_CHECKING:
    from tldw.adapters.llm import VideoSummary
    from tldw.adapters.youtube import Transcript, Video

log = logging.getLogger(__name__)

#: Предел текста транскрипта в символах: ~25–30 тыс. токенов для русского текста.
DEFAULT_MAX_TRANSCRIPT_CHARS: Final = 80_000
#: Маркер усечения: последняя строка материала, который получает LLM.
TRANSCRIPT_TRUNCATION_MARKER: Final = "[транскрипт усечён: {used} из {total} сегментов]"
#: Сообщение для пользователя (T-008 берёт текст отсюда).
MESSAGE_VIDEO_TOO_LONG: Final = (
    "Видео слишком длинное: {duration} при допустимом максимуме {limit}."
)
#: Транскрипт пуст: ни одного непустого сегмента, конспектировать нечего.
MESSAGE_EMPTY_TRANSCRIPT: Final = (
    "У этого видео нет субтитров, поэтому конспект сделать не получится."
)
#: Подписи происхождения шаблона для лога (FR-12): без текста шаблона и тона.
TEMPLATE_ORIGIN_LABELS: Final[Mapping[TemplateOrigin, str]] = {
    TemplateOrigin.BUILTIN: "встроенный",
    TemplateOrigin.USER: "пользовательский",
}


class VideoTooLongError(UserFacingError):
    """Видео длиннее порога из конфига; заметка не создаётся.

    Наследуется от :class:`tldw.errors.UserFacingError`: текст сообщения
    («видео слишком длинное» с длительностью и лимитом) предназначен
    пользователю. Атрибуты с длительностями позволяют собрать сообщение, не
    разбирая текст исключения.
    """

    def __init__(self, duration_seconds: int, max_duration_seconds: int) -> None:
        self.duration_seconds = duration_seconds
        self.max_duration_seconds = max_duration_seconds
        super().__init__(
            MESSAGE_VIDEO_TOO_LONG.format(
                duration=format_timecode(duration_seconds),
                limit=format_timecode(max_duration_seconds),
            )
        )


class EmptyTranscriptError(UserFacingError):
    """В транскрипте нет ни одного непустого сегмента; LLM не вызывается.

    Защита живёт в сервисе, а не только в YouTube-адаптере: через
    ``YouTubeAdapter`` пустой транскрипт даёт ``NoTranscriptError``, но любой
    другой источник или вырожденный лимит усечения могут вернуть пустой текст.
    Сообщение совместимо по смыслу с «нет субтитров», поэтому пользователь
    видит понятный текст, а не общий сбой.
    """

    def __init__(self, message: str = MESSAGE_EMPTY_TRANSCRIPT) -> None:
        super().__init__(message)


class VideoSource(Protocol):
    """Источник видео; в приложении — ``YouTubeAdapter``."""

    def fetch(self, url: str) -> Video:
        """Вернуть видео с метаданными и транскриптом или поднять ошибку."""


class SummaryProvider(Protocol):
    """Суммаризатор; в приложении — ``LLMClient``.

    ``sections`` и ``tone`` — необязательные параметры пользовательского
    шаблона (T-004). Сервис передаёт их только когда ему дан
    ``ActiveTemplate``; на встроенном пути вызов остаётся прежним
    (``summarize(transcript, *, title, channel)``), поэтому фейки со старой
    сигнатурой продолжают работать.
    """

    def summarize(
        self,
        transcript: str,
        *,
        title: str = "",
        channel: str = "",
        sections: Sequence[str] | None = None,
        tone: str = "",
    ) -> VideoSummary:
        """Вернуть провалидированный конспект или поднять ``LLMError``."""


@dataclass(frozen=True, slots=True)
class TranscriptText:
    """Материал для промпта: строки с таймкодами и признаки усечения."""

    text: str = ""
    truncated: bool = False
    used_segments: int = 0
    total_segments: int = 0

    @property
    def is_empty(self) -> bool:
        """В транскрипте не было ни одного непустого сегмента."""
        return not self.text


@dataclass(frozen=True, slots=True)
class SummaryResult:
    """Результат конспекта: модель заметки, готовый Markdown и файл на диске."""

    note: Note
    markdown: str
    filename: str
    transcript: TranscriptText
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class _TemplateSnapshot:
    """Разделы, тон и происхождение шаблона, снятые один раз на обработку.

    Значение неизменяемое и самодостаточное: промпт, заметка и запись в лог
    используют один и тот же снимок, поэтому смена сохранённого шаблона во
    время генерации не меняет результат (FR-7). Для ``template=None`` снимок
    описывает встроенный шаблон — он нужен только для лога, сам вызов LLM
    остаётся прежним.
    """

    sections: tuple[str, ...]
    tone: str
    origin: TemplateOrigin


def _template_snapshot(template: ActiveTemplate | None) -> _TemplateSnapshot:
    """Снять разделы, тон и происхождение шаблона до первого тяжёлого вызова.

    :raises TypeError: если передан не ``ActiveTemplate`` и не ``None``
        (ошибка вызывающего кода, а не пользовательских данных).
    """
    if template is None:
        return _TemplateSnapshot(BUILTIN_SECTIONS, "", TemplateOrigin.BUILTIN)
    if not isinstance(template, ActiveTemplate):
        raise TypeError("template должен быть ActiveTemplate или None")
    return _TemplateSnapshot(template.sections, template.tone, template.origin)


class SummaryService:
    """Сценарий «ссылка на видео → готовая заметка».

    :param video_source: источник видео (``YouTubeAdapter.fetch(url)``).
    :param summarizer: суммаризатор (``LLMClient.summarize``).
    :param max_duration_seconds: порог длительности из конфига; видео длиннее
        отвергается, ровно на пороге — допускается.
    :param max_transcript_chars: лимит текста транскрипта в символах.
    :param data_dir: каталог данных для записи ``.md``; ``None`` — не писать
        файл (нужно для предпросмотра и тестов).
    """

    def __init__(
        self,
        video_source: VideoSource,
        summarizer: SummaryProvider,
        *,
        max_duration_seconds: int = DEFAULT_MAX_VIDEO_DURATION_SECONDS,
        max_transcript_chars: int = DEFAULT_MAX_TRANSCRIPT_CHARS,
        data_dir: Path | None = None,
    ) -> None:
        self._video_source = video_source
        self._summarizer = summarizer
        self._max_duration_seconds = _positive_int(
            max_duration_seconds,
            "Порог длительности видео",
        )
        self._max_transcript_chars = _positive_int(
            max_transcript_chars,
            "Лимит текста транскрипта",
        )
        self._data_dir = None if data_dir is None else Path(data_dir)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        video_source: VideoSource,
        summarizer: SummaryProvider,
        *,
        max_transcript_chars: int = DEFAULT_MAX_TRANSCRIPT_CHARS,
    ) -> SummaryService:
        """Собрать сервис из настроек: порог и каталог данных — из конфига."""
        return cls(
            video_source,
            summarizer,
            max_duration_seconds=settings.max_video_duration_seconds,
            max_transcript_chars=max_transcript_chars,
            data_dir=settings.data_dir,
        )

    @property
    def max_duration_seconds(self) -> int:
        """Порог длительности, действующий для этого сервиса."""
        return self._max_duration_seconds

    def process(
        self,
        url: str,
        *,
        template: ActiveTemplate | None = None,
    ) -> SummaryResult:
        """Обработать ссылку и вернуть заметку с готовым ``.md``.

        :param template: снимок активного шаблона пользователя; ``None``
            (по умолчанию) — прежний встроенный путь. Снимок снимается один
            раз здесь, до обращения к источнику видео, поэтому смена
            сохранённого шаблона во время генерации не меняет ни промпт, ни
            заметку, ни файл.
        :raises VideoTooLongError: видео длиннее порога из конфига; ни LLM,
            ни файл не вызываются.
        :raises EmptyTranscriptError: транскрипт пуст (или вырожденный лимит
            усечения не оставил текста); LLM и запись файла не вызываются.
        :raises tldw.adapters.youtube.YouTubeAdapterError: не YouTube, нет
            субтитров, видео недоступно или сетевая ошибка.
        :raises tldw.adapters.llm.LLMError: LLM не ответил, ответ неполный или
            не совпал с шаблоном; частичной заметки не остаётся.
        :raises TypeError: если ``template`` — не ``ActiveTemplate`` и не
            ``None``.
        """
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url должен быть непустой строкой")

        # Ссылка очищается один раз: и запрос к источнику видео, и frontmatter
        # получают одно и то же значение, иначе пробелы по краям давали бы
        # разные `source` и URL запроса (P2 T-005).
        clean_url = url.strip()
        snapshot = _template_snapshot(template)

        video = self._video_source.fetch(clean_url)
        # Одна запись на запрос: безопасный video_id позволяет сопоставить
        # инцидент с видео, не записывая полный URL с query (P3 T-008/T-016).
        log.info(
            "Конспект: видео %s, шаблон %s, разделов: %s",
            video.metadata.video_id or "—",
            TEMPLATE_ORIGIN_LABELS[snapshot.origin],
            len(snapshot.sections),
        )
        self._ensure_duration(video)
        material = join_transcript(video.transcript, max_chars=self._max_transcript_chars)
        if material.is_empty:
            # Пустой или вырожденный (лимит меньше маркера) транскрипт не уходит
            # в промпт: LLM ответил бы по одним метаданным, а пользователь ждёт
            # конспект по субтитрам. Ни LLM, ни файл не вызываются.
            log.warning("Конспект: пустой транскрипт — LLM не вызывается")
            raise EmptyTranscriptError()
        if material.truncated:
            log.warning(
                "Конспект: транскрипт усечён до %s из %s сегментов (лимит %s символов)",
                material.used_segments,
                material.total_segments,
                self._max_transcript_chars,
            )

        note_sections: tuple[NoteSection, ...] = ()
        if template is None:
            # Прежний встроенный путь: без sections/tone, чтобы старые фейки
            # суммаризатора и прежний промпт LLM оставались рабочими.
            summary = self._summarizer.summarize(
                material.text,
                title=video.metadata.title,
                channel=video.metadata.channel,
            )
        else:
            summary = self._summarizer.summarize(
                material.text,
                title=video.metadata.title,
                channel=video.metadata.channel,
                sections=snapshot.sections,
                tone=snapshot.tone,
            )
            note_sections = _note_sections(snapshot.sections, summary)

        note = build_note(video, summary, source=clean_url, sections=note_sections)
        markdown = render_note(note)
        filename = note_filename(note)
        path = save_note(self._data_dir, filename, markdown) if self._data_dir is not None else None
        log.info(
            "Конспект готов: %s, video_id %s (%s символов, %s сегментов)",
            filename,
            video.metadata.video_id or "—",
            len(markdown),
            material.used_segments,
        )
        return SummaryResult(
            note=note,
            markdown=markdown,
            filename=filename,
            transcript=material,
            path=path,
        )

    def _ensure_duration(self, video: Video) -> None:
        duration = video.metadata.duration_seconds
        if duration is not None and duration > self._max_duration_seconds:
            log.warning(
                "Конспект: видео длиной %s с превышает порог %s с",
                duration,
                self._max_duration_seconds,
            )
            raise VideoTooLongError(duration, self._max_duration_seconds)


def join_transcript(
    transcript: Transcript,
    *,
    max_chars: int = DEFAULT_MAX_TRANSCRIPT_CHARS,
) -> TranscriptText:
    """Склеить сегменты транскрипта в строки с таймкодами.

    Каждая строка — ``[MM:SS] текст`` или ``[HH:MM:SS] текст``; порядок
    сегментов сохраняется. Секунды округляются тем же правилом, что и
    длительность видео в адаптере (``round``), поэтому ``15.9`` даёт
    ``[00:16]``, а не ``[00:15]``. Если суммарная длина больше ``max_chars``,
    остаются только целые сегменты, помещающиеся в лимит (кроме случая, когда
    даже первый сегмент не влезает — тогда его текст обрезается с сохранением
    таймкода), а последней строкой добавляется
    :data:`TRANSCRIPT_TRUNCATION_MARKER`. Маркер входит в лимит, поэтому
    ``len(result.text) <= max_chars``.

    :param max_chars: жёсткий лимит длины материала в символах (положительное
        целое число). Если лимита не хватает на маркер усечения и хотя бы один
        сегмент, возвращается пустой текст (``is_empty``): обрезанный маркер —
        это мусор в промпте, а сервис отвергает пустой материал до LLM.
    :raises ValueError: если лимит не положительное целое число.
    """
    limit = _positive_int(max_chars, "Лимит текста транскрипта")
    lines = [
        f"[{format_timecode(round(segment.start_seconds))}] {segment.text}"
        for segment in transcript.segments
        if segment.text
    ]
    total = len(lines)
    if total == 0:
        return TranscriptText()

    full_text = "\n".join(lines)
    if len(full_text) <= limit:
        return TranscriptText(
            text=full_text,
            truncated=False,
            used_segments=total,
            total_segments=total,
        )

    # Резервируем место под маркер с максимальным числом цифр (used == total).
    reserved_marker = TRANSCRIPT_TRUNCATION_MARKER.format(used=total, total=total)
    if len(reserved_marker) + 1 >= limit:
        # Лимит меньше маркера или равен «маркер + 1» (budget == 0): места под
        # целый сегмент нет. Пустой текст не даёт обрезанному маркеру и пустой
        # строке (used_segments=1) уйти в промпт.
        return TranscriptText(
            text="",
            truncated=True,
            used_segments=0,
            total_segments=total,
        )
    budget = limit - len(reserved_marker) - 1
    kept: list[str] = []
    length = 0
    for line in lines:
        addition = len(line) + (1 if kept else 0)
        if length + addition > budget:
            break
        kept.append(line)
        length += addition
    if not kept:
        # Даже первый сегмент не влез: сохраняем таймкод и обрезаем текст.
        kept = [_truncate_line(lines[0], budget)]

    marker = TRANSCRIPT_TRUNCATION_MARKER.format(used=len(kept), total=total)
    return TranscriptText(
        text="\n".join([*kept, marker]),
        truncated=True,
        used_segments=len(kept),
        total_segments=total,
    )


def build_note(
    video: Video,
    summary: VideoSummary,
    *,
    source: str | None = None,
    sections: Sequence[NoteSection] = (),
) -> Note:
    """Собрать заметку из метаданных видео и провалидированного ответа LLM.

    ``source`` по умолчанию — ссылка из видео; сервис передаёт сюда исходную
    ссылку пользователя, чтобы frontmatter не зависел от нормализации адаптера.
    ``title``/``channel``/``duration`` — из метаданных видео. Строка вместо
    кортежа в ответе трактуется как один элемент, а не разбирается по символам:
    это защищает заметку от тихой порчи на границе LLM → модель.

    ``sections`` — динамические разделы в порядке применения (T-003): если
    переданы, заметка рендерится по ним, а поля ``tldr``…``follow_ups``
    остаются заполненными для совместимости, но в ``.md`` не попадают.
    Пустой ``sections`` (по умолчанию) даёт прежнюю заметку встроенного
    шаблона; frontmatter из семи полей собирается одинаково в обоих случаях.
    """
    metadata = video.metadata
    return Note(
        title=metadata.title,
        source=video.url if source is None else source,
        channel=metadata.channel,
        duration_seconds=metadata.duration_seconds,
        tldr=_string_items(summary.tldr, "tldr"),
        key_ideas=_string_items(summary.key_ideas, "key_ideas"),
        timeline=tuple(
            TimelineEntry(timecode=entry.timecode, title=entry.title)
            for entry in summary.timeline
        ),
        quotes=tuple(
            Quote(text=quote.text, timecode=quote.timecode) for quote in summary.quotes
        ),
        follow_ups=_string_items(summary.follow_ups, "follow_ups"),
        sections=tuple(sections),
    )


def _note_sections(
    headings: Sequence[str],
    summary: VideoSummary,
) -> tuple[NoteSection, ...]:
    """Собрать динамические разделы заметки из ответа LLM в порядке шаблона.

    Ответ LLM уже провалидирован адаптером по T-004, но сервис — то место, где
    ответ сопоставляется с шаблоном, поэтому здесь повторяется только
    структурная проверка пары «шаблон ↔ ответ»: разделов столько же, заголовки
    те же и в том же порядке, каждый раздел непустой. Лимиты количества
    пунктов остаются в адаптере (единый источник, T-004) и здесь не
    дублируются.

    Порядок и написание заголовков берутся из шаблона: даже если LLM вернул
    заголовок в другом регистре, в заметку попадёт название пользователя.

    :raises tldw.adapters.llm.LLMError: если раздела не хватает, он лишний,
        перепутан порядок или раздел пуст — частичная заметка не создаётся.
    """
    raw_sections = summary.sections
    if isinstance(raw_sections, (str, bytes)) or not isinstance(raw_sections, Sequence):
        _fail_section_mismatch("ответ LLM не содержит разделов")
    if len(raw_sections) != len(headings):
        _fail_section_mismatch(f"разделов {len(raw_sections)} вместо {len(headings)}")
    result: list[NoteSection] = []
    for index, (heading, section) in enumerate(zip(headings, raw_sections, strict=True), start=1):
        actual = getattr(section, "heading", None)
        if not isinstance(actual, str) or one_line(actual).casefold() != heading.casefold():
            _fail_section_mismatch(f"раздел {index}: заголовок не совпал с шаблоном")
        result.append(NoteSection(heading=heading, items=_section_items(section, index)))
    return tuple(result)


def _section_items(section: object, index: int) -> tuple[str, ...]:
    """Проверить пункты раздела ответа: непустые строки в исходном порядке."""
    raw = getattr(section, "items", None)
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        _fail_section_mismatch(f"раздел {index}: пункты не список")
    items = tuple(one_line(item) for item in raw if isinstance(item, str))
    if len(items) != len(raw):
        _fail_section_mismatch(f"раздел {index}: пункт не является текстом")
    if not items:
        _fail_section_mismatch(f"раздел {index}: пустой раздел")
    if not all(items):
        _fail_section_mismatch(f"раздел {index}: пустой пункт")
    return items


def _fail_section_mismatch(reason: str) -> NoReturn:
    """Записать причину и поднять ``LLMError`` контракта LLM-адаптера.

    Импорт локальный: сервис общается с LLM через протокол
    :class:`SummaryProvider`, поэтому ``httpx`` не должен попадать в граф
    импорта сервиса (ADR-002). Ветка выполняется только на ошибочном ответе,
    который и так приходит из адаптера. Текст шаблона и тона в лог не попадает.
    """
    from tldw.adapters.llm import MESSAGE_LLM_BAD_RESPONSE, LLMError

    log.warning("Конспект: ответ LLM не совпал с шаблоном — %s", reason)
    raise LLMError(MESSAGE_LLM_BAD_RESPONSE)


def _positive_int(value: object, name: str) -> int:
    """Проверить положительное целое число и назвать его по-русски.

    ``name`` — человекочитаемое имя лимита («Порог длительности видео»), а не
    английское имя поля: текст ошибки не должен показывать
    ``max_duration_seconds`` (косметика T-005, T-015).
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} должен быть положительным целым числом")
    return value


def _truncate_line(line: str, budget: int) -> str:
    """Обрезать одну слишком длинную строку, сохранив таймкод и лимит."""
    if budget <= 1:
        return line[: max(budget, 0)]
    return f"{line[: budget - 1].rstrip()}…"


def _string_items(value: object, field_name: str) -> tuple[str, ...]:
    """Привести раздел ответа LLM к кортежу строк без разбора строки по символам.

    Ответ LLM уже провалидирован адаптером, но сервис — последняя граница
    перед заметкой, поэтому здесь проверяется форма: строка трактуется как
    один элемент, а любой нестроковый элемент (в том числе ``dict``/``list``)
    — ошибка, а не Python-repr внутри ``.md`` (P2 T-005).
    """
    if isinstance(value, str):
        return (value,)
    try:
        items = tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise TypeError(f"{field_name} должен быть последовательностью строк") from None
    if not all(isinstance(item, str) for item in items):
        raise TypeError(f"{field_name} должен содержать только строки")
    return items


__all__ = [
    "DEFAULT_MAX_TRANSCRIPT_CHARS",
    "EmptyTranscriptError",
    "MESSAGE_EMPTY_TRANSCRIPT",
    "MESSAGE_VIDEO_TOO_LONG",
    "SummaryProvider",
    "SummaryResult",
    "SummaryService",
    "TEMPLATE_ORIGIN_LABELS",
    "TRANSCRIPT_TRUNCATION_MARKER",
    "TranscriptText",
    "VideoSource",
    "VideoTooLongError",
    "build_note",
    "join_transcript",
]

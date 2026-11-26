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
3. склеивает транскрипт с таймкодами (:func:`join_transcript`);
4. вызывает LLM, собирает :class:`~tldw.services.note.Note` из метаданных
   видео и ответа (:func:`build_note`);
5. рендерит ``.md`` по шаблону T-002 и при заданном ``data_dir`` атомарно
   сохраняет его через :func:`tldw.storage.notes.save_note`.

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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from tldw.config import DEFAULT_MAX_VIDEO_DURATION_SECONDS, Settings
from tldw.services.note import (
    Note,
    Quote,
    TimelineEntry,
    format_timecode,
    note_filename,
    render_note,
)
from tldw.storage.notes import save_note

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


class VideoTooLongError(Exception):
    """Видео длиннее порога из конфига; заметка не создаётся.

    Атрибуты с длительностями позволяют обработчику (T-008) собрать понятное
    сообщение, не разбирая текст исключения.
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


class VideoSource(Protocol):
    """Источник видео; в приложении — ``YouTubeAdapter``."""

    def fetch(self, url: str) -> Video:
        """Вернуть видео с метаданными и транскриптом или поднять ошибку."""


class SummaryProvider(Protocol):
    """Суммаризатор; в приложении — ``LLMClient``."""

    def summarize(
        self,
        transcript: str,
        *,
        title: str = "",
        channel: str = "",
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
        self._max_duration_seconds = _positive_int(max_duration_seconds, "max_duration_seconds")
        self._max_transcript_chars = _transcript_limit(max_transcript_chars)
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

    def process(self, url: str) -> SummaryResult:
        """Обработать ссылку и вернуть заметку с готовым ``.md``.

        :raises VideoTooLongError: видео длиннее порога из конфига; ни LLM,
            ни файл не вызываются.
        :raises tldw.adapters.youtube.YouTubeAdapterError: не YouTube, нет
            субтитров, видео недоступно или сетевая ошибка.
        :raises tldw.adapters.llm.LLMError: LLM не ответил или ответ неполный;
            частичной заметки не остаётся.
        """
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url должен быть непустой строкой")

        video = self._video_source.fetch(url)
        self._ensure_duration(video)
        material = join_transcript(video.transcript, max_chars=self._max_transcript_chars)
        if material.truncated:
            log.warning(
                "Конспект: транскрипт усечён до %s из %s сегментов (лимит %s символов)",
                material.used_segments,
                material.total_segments,
                self._max_transcript_chars,
            )

        summary = self._summarizer.summarize(
            material.text,
            title=video.metadata.title,
            channel=video.metadata.channel,
        )
        note = build_note(video, summary, source=url.strip())
        markdown = render_note(note)
        filename = note_filename(note)
        path = save_note(self._data_dir, filename, markdown) if self._data_dir is not None else None
        log.info(
            "Конспект готов: %s (%s символов, %s сегментов)",
            filename,
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
    сегментов сохраняется. Если суммарная длина больше ``max_chars``, остаются
    только целые сегменты, помещающиеся в лимит (кроме случая, когда даже
    первый сегмент не влезает — тогда его текст обрезается с сохранением
    таймкода), а последней строкой добавляется
    :data:`TRANSCRIPT_TRUNCATION_MARKER`. Маркер входит в лимит, поэтому
    ``len(result.text) <= max_chars``.

    :param max_chars: жёсткий лимит длины материала в символах (положительное
        целое число). Если лимит меньше самого маркера усечения, в материал
        попадает только обрезанный маркер — это вырожденный, но не падающий
        случай.
    :raises ValueError: если лимит не положительное целое число.
    """
    limit = _transcript_limit(max_chars)
    lines = [
        f"[{format_timecode(int(segment.start_seconds))}] {segment.text}"
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
    if len(reserved_marker) + 1 > limit:
        return TranscriptText(
            text=reserved_marker[:limit],
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


def build_note(video: Video, summary: VideoSummary, *, source: str | None = None) -> Note:
    """Собрать заметку из метаданных видео и провалидированного ответа LLM.

    ``source`` по умолчанию — ссылка из видео; сервис передаёт сюда исходную
    ссылку пользователя, чтобы frontmatter не зависел от нормализации адаптера.
    ``title``/``channel``/``duration`` — из метаданных видео. Строка вместо
    кортежа в ответе трактуется как один элемент, а не разбирается по символам:
    это защищает заметку от тихой порчи на границе LLM → модель.
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
    )


def _transcript_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Лимит транскрипта должен быть положительным целым числом")
    return value


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} должен быть положительным целым числом")
    return value


def _truncate_line(line: str, budget: int) -> str:
    """Обрезать одну слишком длинную строку, сохранив таймкод и лимит."""
    if budget <= 1:
        return line[: max(budget, 0)]
    return f"{line[: budget - 1].rstrip()}…"


def _string_items(value: object, field_name: str) -> tuple[str, ...]:
    """Привести раздел ответа LLM к кортежу строк без разбора строки по символам."""
    if isinstance(value, str):
        return (value,)
    try:
        items = tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise TypeError(f"{field_name} должен быть последовательностью строк") from None
    return tuple("" if item is None else str(item) for item in items)


__all__ = [
    "DEFAULT_MAX_TRANSCRIPT_CHARS",
    "MESSAGE_VIDEO_TOO_LONG",
    "SummaryProvider",
    "SummaryResult",
    "SummaryService",
    "TRANSCRIPT_TRUNCATION_MARKER",
    "TranscriptText",
    "VideoSource",
    "VideoTooLongError",
    "build_note",
    "join_transcript",
]

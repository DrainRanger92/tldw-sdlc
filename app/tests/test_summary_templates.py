"""Тесты применения активного шаблона в сервисе конспекта (T-005).

Все проверки офлайновые: источник видео и суммаризатор — фейки из
``tests/support.py`` (или ``httpx.MockTransport`` для настоящего ``LLMClient``),
каталог данных — ``tmp_path``. Покрыты:

* пользовательский шаблон: разделы и тон уходят в суммаризатор одним вызовом,
  заметка собирается в порядке шаблона, frontmatter и имя файла прежние;
* регрессия встроенного пути: без шаблона прежний промпт, прежняя заметка и
  старый фейк суммаризатора без новых аргументов;
* снимок шаблона: смена сохранённого шаблона во время генерации не меняет
  результат, шаблон читается один раз;
* ``LLMError`` без частичного ``.md`` для отсутствующего, лишнего, пустого
  раздела, нарушения порядка и ошибки провайдера;
* лог: происхождение шаблона и число разделов без текста шаблона и тона.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from support import (
    FAKE_ENV_KEY,
    VIDEO_CHANNEL,
    VIDEO_DURATION_SECONDS,
    VIDEO_TITLE,
    VIDEO_URL,
    FakeSummarizer,
    FakeVideoSource,
    make_summary,
    make_video,
)

from tldw.adapters.llm import (
    LLMClient,
    LLMError,
    SummarySection,
    VideoSummary,
    build_system_prompt,
)
from tldw.adapters.youtube import Video
from tldw.services.note import (
    FRONTMATTER_KEYS,
    SECTION_HEADINGS,
    SectionKind,
    note_filename,
    render_note,
)
from tldw.services.summary import (
    TEMPLATE_ORIGIN_LABELS,
    SummaryResult,
    SummaryService,
    build_note,
)
from tldw.services.template import (
    BUILTIN_SECTIONS,
    ActiveTemplate,
    NoteTemplate,
    TemplateOrigin,
    TemplateService,
)

MODEL = "test-model"
BASE_URL = "https://llm.test/v1"
USER_ID = 424_242
SECTIONS = ("Коротко", "Примеры кода", "Что попробовать")
TONE = "короче, с акцентом на примеры кода"
SUMMARY_LOGGER = "tldw.services.summary"


# --- Помощники -------------------------------------------------------------


def make_template(
    sections: tuple[str, ...] = SECTIONS,
    tone: str = TONE,
) -> ActiveTemplate:
    """Пользовательский активный шаблон с заданными разделами и тоном."""
    return ActiveTemplate(NoteTemplate(sections=sections, tone=tone), TemplateOrigin.USER)


def custom_summary(
    headings: tuple[str, ...] = SECTIONS,
    *,
    items: int = 2,
) -> VideoSummary:
    """Ответ LLM по динамической схеме: по ``items`` пунктов в каждом разделе."""
    return VideoSummary(
        sections=tuple(
            SummarySection(
                heading,
                tuple(f"Пункт {index} раздела {heading}" for index in range(1, items + 1)),
            )
            for heading in headings
        )
    )


def builtin_content() -> str:
    """Прежний JSON-ответ LLM по пяти полям — из общего ``make_summary``."""
    summary = make_summary()
    return json.dumps(
        {
            "tldr": list(summary.tldr),
            "key_ideas": list(summary.key_ideas),
            "timeline": [
                {"timecode": entry.timecode, "title": entry.title} for entry in summary.timeline
            ],
            "quotes": [
                {"timecode": quote.timecode, "text": quote.text} for quote in summary.quotes
            ],
            "follow_ups": list(summary.follow_ups),
        },
        ensure_ascii=False,
    )


def custom_content(headings: tuple[str, ...] = SECTIONS) -> str:
    """JSON-ответ по динамической схеме с заданными разделами."""
    return json.dumps(
        {
            "sections": [
                {
                    "heading": heading,
                    "items": [f"Пункт {index} раздела {heading}" for index in range(1, 3)],
                }
                for heading in headings
            ]
        },
        ensure_ascii=False,
    )


def recording_client(content: str) -> tuple[list[httpx.Request], LLMClient]:
    """Настоящий ``LLMClient`` на ``MockTransport``, записывающий запросы."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = LLMClient(
        api_key=FAKE_ENV_KEY,
        model=MODEL,
        base_url=BASE_URL,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    return requests, client


def system_prompt_of(request: httpx.Request) -> str:
    body = json.loads(request.content)
    assert body["messages"][0]["role"] == "system"
    return body["messages"][0]["content"]


def frontmatter_lines(markdown: str) -> list[str]:
    lines = markdown.splitlines()
    assert lines[0] == "---", "заметка должна начинаться с YAML frontmatter"
    end = lines.index("---", 1)
    return lines[1:end]


def frontmatter(markdown: str) -> dict[str, Any]:
    return yaml.safe_load("\n".join(frontmatter_lines(markdown)))


def section_headings(markdown: str) -> list[str]:
    return [line for line in markdown.splitlines() if line.startswith("## ")]


def section_body(markdown: str, heading: str) -> str:
    start = markdown.index(heading) + len(heading)
    end = markdown.find("\n## ", start)
    return markdown[start:] if end == -1 else markdown[start:end]


@dataclass
class LegacySummarizer:
    """Суммаризатор с прежней сигнатурой — как фейки до T-005.

    Доказывает, что на встроенном пути сервис не передаёт ``sections``/``tone``:
    иначе вызов упал бы с ``TypeError``.
    """

    summary: VideoSummary
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    def summarize(
        self,
        transcript: str,
        *,
        title: str = "",
        channel: str = "",
    ) -> VideoSummary:
        self.calls.append((transcript, title, channel))
        return self.summary


@dataclass
class MutableRepository:
    """Хранилище шаблона, которое можно изменить во время генерации."""

    record: tuple[tuple[str, ...], str] | None = None
    loads: list[int] = field(default_factory=list)

    def load(self, user_id: int) -> tuple[tuple[str, ...], str] | None:
        self.loads.append(user_id)
        return self.record

    def save(self, user_id: int, record: tuple[tuple[str, ...], str]) -> None:
        self.record = record

    def delete(self, user_id: int) -> None:
        self.record = None


class ChangingVideoSource:
    """Источник видео, который меняет сохранённый шаблон во время генерации."""

    def __init__(
        self,
        repository: MutableRepository,
        record: tuple[tuple[str, ...], str],
        video: Video,
    ) -> None:
        self._repository = repository
        self._record = record
        self._video = video
        self.calls: list[str] = []

    def fetch(self, url: str) -> Video:
        self.calls.append(url)
        # Пользователь сохранил другой шаблон, пока шла генерация заметки.
        self._repository.save(USER_ID, self._record)
        return self._video


@dataclass(frozen=True, slots=True)
class DriftingTemplate(ActiveTemplate):
    """Шаблон, который меняет содержимое между чтениями.

    Моделирует смену сохранённого шаблона во время генерации: если сервис
    перечитает разделы или тон после первого тяжёлого вызова, заметка и промпт
    соберутся по другому шаблону. Счётчики фиксируют, что снимок снят один раз.
    """

    section_reads: list[int] = field(default_factory=list)
    tone_reads: list[int] = field(default_factory=list)

    @property
    def sections(self) -> tuple[str, ...]:
        self.section_reads.append(1)
        return SECTIONS if len(self.section_reads) == 1 else ("Другая",)

    @property
    def tone(self) -> str:
        self.tone_reads.append(1)
        return TONE if len(self.tone_reads) == 1 else "другой тон"


# --- Фикстуры --------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def video_source() -> FakeVideoSource:
    return FakeVideoSource(video=make_video())


@pytest.fixture
def summarizer() -> FakeSummarizer:
    return FakeSummarizer(summary=make_summary())


@pytest.fixture
def service(
    video_source: FakeVideoSource,
    summarizer: FakeSummarizer,
    data_dir: Path,
) -> SummaryService:
    return SummaryService(video_source, summarizer, data_dir=data_dir)


# --- Пользовательский шаблон ----------------------------------------------


def test_custom_template_sections_and_tone_reach_summarizer(
    service: SummaryService,
    summarizer: FakeSummarizer,
    data_dir: Path,
) -> None:
    summarizer.summary = custom_summary()

    result = service.process(VIDEO_URL, template=make_template())

    assert len(summarizer.calls) == 1, "на заметку приходится один вызов LLM"
    assert summarizer.templates == [(SECTIONS, TONE)]
    assert [section.heading for section in result.note.sections] == list(SECTIONS)
    assert result.path is not None and result.path.is_file()
    assert result.path == data_dir / result.filename


def test_dynamic_note_follows_template_order_and_keeps_frontmatter(
    service: SummaryService,
    summarizer: FakeSummarizer,
    data_dir: Path,
) -> None:
    summarizer.summary = custom_summary()

    result = service.process(VIDEO_URL, template=make_template())

    # Разделы в порядке шаблона, а не в порядке ответа LLM.
    assert section_headings(result.markdown) == [f"## {section}" for section in SECTIONS]
    assert result.markdown == render_note(result.note)
    assert result.note.sections[0].items == (
        "Пункт 1 раздела Коротко",
        "Пункт 2 раздела Коротко",
    )

    # Frontmatter из семи полей в прежнем порядке и с прежними значениями.
    keys = [
        line.split(":", 1)[0].strip()
        for line in frontmatter_lines(result.markdown)
        if line and not line.startswith("  ")
    ]
    assert tuple(keys) == FRONTMATTER_KEYS
    assert frontmatter(result.markdown) == {
        "title": VIDEO_TITLE,
        "source": VIDEO_URL,
        "channel": VIDEO_CHANNEL,
        "duration": VIDEO_DURATION_SECONDS,
        "date": date.today().isoformat(),
        "tags": [],
        "status": "processed",
    }
    assert f'source: "{VIDEO_URL}"' in result.markdown

    # Имя файла прежнее: slug заголовка видео, как у встроенного пути.
    previous_filename = note_filename(build_note(make_video(), make_summary()))
    assert result.filename == previous_filename
    assert result.path == data_dir / previous_filename
    assert result.path is not None and result.path.read_text(encoding="utf-8") == result.markdown
    assert list(data_dir.iterdir()) == [result.path], "в каталоге нет временных файлов"


def test_custom_template_with_builtin_heading_keeps_previous_format(
    service: SummaryService,
    summarizer: FakeSummarizer,
) -> None:
    headings = ("TL;DR", "Примеры кода")
    summarizer.summary = custom_summary(headings, items=3)

    result = service.process(VIDEO_URL, template=make_template(headings, tone=""))

    assert [section.kind for section in result.note.sections] == [
        SectionKind.PARAGRAPH,
        SectionKind.CUSTOM,
    ]
    body = section_body(result.markdown, "## TL;DR")
    assert "Пункт 1 раздела TL;DR Пункт 2 раздела TL;DR Пункт 3 раздела TL;DR" in body
    assert "- Пункт 1 раздела Примеры кода" in section_body(result.markdown, "## Примеры кода")


def test_dynamic_builtin_timeline_and_quotes_keep_native_format(
    tmp_path: Path,
) -> None:
    """Ответ LLM → сервис → ``render_note``: «Таймкоды» и «Цитаты» без регресса.

    Пункты встроенных разделов приходят от LLM строками, поэтому проверяется
    весь путь, а не типизированный ``NoteSection``: таймкод должен получить
    привычные скобки, а цитата без таймкода — не добавлять пустое ``> —``.
    """
    headings = ("TL;DR", "Таймкоды", "Цитаты")
    content = json.dumps(
        {
            "sections": [
                {
                    "heading": "TL;DR",
                    "items": ["Вывод один.", "Вывод два.", "Вывод три."],
                },
                {
                    "heading": "Таймкоды",
                    "items": ["01:23 — Начало", "02:05 Практика"],
                },
                {
                    "heading": "Цитаты",
                    "items": [
                        "Учиться — значит меняться.",
                        "Практика важнее теории.",
                    ],
                },
            ]
        },
        ensure_ascii=False,
    )
    requests, client = recording_client(content)
    data_dir = tmp_path / "data"
    service = SummaryService(FakeVideoSource(video=make_video()), client, data_dir=data_dir)

    result = service.process(VIDEO_URL, template=make_template(headings, tone=""))

    assert len(requests) == 1
    timeline = section_body(result.markdown, "## Таймкоды")
    assert "- [01:23] Начало" in timeline
    assert "- [02:05] Практика" in timeline
    assert "- 01:23 Начало" not in timeline, "квадратные скобки не теряются"

    quotes = section_body(result.markdown, "## Цитаты")
    assert "> Учиться — значит меняться." in quotes
    assert "> Практика важнее теории." in quotes
    assert "> —" not in quotes, "пустой атрибут без таймкода не нужен"

    # Frontmatter и встроенный формат остальных разделов не изменились.
    assert frontmatter(result.markdown)["source"] == VIDEO_URL
    assert section_headings(result.markdown) == [
        "## TL;DR",
        "## Таймкоды",
        "## Цитаты",
    ]
    assert result.path is not None and result.path.is_file()


def test_custom_template_with_real_client_sends_sections_and_tone_once(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    requests, client = recording_client(custom_content())
    service = SummaryService(FakeVideoSource(video=make_video()), client, data_dir=data_dir)

    result = service.process(VIDEO_URL, template=make_template())

    assert len(requests) == 1, "один вызов LLM на заметку"
    prompt = system_prompt_of(requests[0])
    positions = [prompt.index(section) for section in SECTIONS]
    assert positions == sorted(positions), "разделы в промпте идут в порядке шаблона"
    assert TONE in prompt
    assert [section.heading for section in result.note.sections] == list(SECTIONS)
    assert result.path is not None and result.path.is_file()
    assert list(data_dir.rglob("*.tmp")) == []


# --- Регрессия встроенного пути -------------------------------------------


def test_default_path_works_with_legacy_summarizer_and_old_note(
    tmp_path: Path,
) -> None:
    summarizer = LegacySummarizer(summary=make_summary())
    service = SummaryService(
        FakeVideoSource(video=make_video()),
        summarizer,
        data_dir=tmp_path / "data",
    )

    result = service.process(VIDEO_URL)

    assert len(summarizer.calls) == 1
    assert result.note.sections == ()
    assert result.note.tldr == make_summary().tldr
    assert result.markdown == render_note(result.note)
    assert section_headings(result.markdown) == list(SECTION_HEADINGS)
    assert result.filename == note_filename(build_note(make_video(), make_summary()))
    assert result.path is not None and result.path.is_file()


def test_default_path_does_not_pass_template_arguments(
    service: SummaryService,
    summarizer: FakeSummarizer,
) -> None:
    service.process(VIDEO_URL)

    assert summarizer.templates == [(None, "")], "встроенный путь: без sections/tone"


def test_default_path_sends_previous_prompt(tmp_path: Path) -> None:
    requests, client = recording_client(builtin_content())
    service = SummaryService(
        FakeVideoSource(video=make_video()),
        client,
        data_dir=tmp_path / "data",
    )

    result = service.process(VIDEO_URL)

    assert len(requests) == 1
    prompt = system_prompt_of(requests[0])
    assert prompt == build_system_prompt(), "промпт встроенного пути не менялся"
    assert "{{" not in prompt
    assert '"sections"' not in prompt
    assert result.note.sections == ()
    assert result.note.tldr == make_summary().tldr
    assert result.path is not None and result.path.is_file()


# --- Снимок шаблона --------------------------------------------------------


def test_snapshot_survives_template_change_during_generation(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    repository = MutableRepository(record=(SECTIONS, TONE))
    template_service = TemplateService(repository)
    snapshot = template_service.get_active(USER_ID)
    assert repository.loads == [USER_ID], "снимок — одно чтение шаблона"

    summarizer = FakeSummarizer(summary=custom_summary())
    source = ChangingVideoSource(
        repository,
        (("Другая",), "длиннее и подробнее"),
        make_video(),
    )
    service = SummaryService(source, summarizer, data_dir=data_dir)

    result: SummaryResult = service.process(VIDEO_URL, template=snapshot)

    assert source.calls == [VIDEO_URL]
    assert repository.loads == [USER_ID], "во время генерации шаблон повторно не читается"
    assert summarizer.templates == [(SECTIONS, TONE)], "промпт собран по снимку"
    assert [section.heading for section in result.note.sections] == list(SECTIONS)
    assert section_headings(result.markdown) == [f"## {section}" for section in SECTIONS]
    assert "## Другая" not in result.markdown

    # Изменение всё-таки сохранилось и применяется к следующей обработке.
    assert template_service.get_active(USER_ID).sections == ("Другая",)


def test_snapshot_reads_template_once_before_generation(data_dir: Path) -> None:
    """Сервис копирует разделы и тон до генерации, а не перечитывает их потом."""
    template = DriftingTemplate(NoteTemplate(sections=SECTIONS, tone=TONE), TemplateOrigin.USER)
    summarizer = FakeSummarizer(summary=custom_summary())
    service = SummaryService(FakeVideoSource(video=make_video()), summarizer, data_dir=data_dir)

    result = service.process(VIDEO_URL, template=template)

    assert len(template.section_reads) == 1, "разделы сняты ровно один раз"
    assert len(template.tone_reads) == 1, "тон снят ровно один раз"
    assert summarizer.templates == [(SECTIONS, TONE)]
    assert [section.heading for section in result.note.sections] == list(SECTIONS)
    assert "## Другая" not in result.markdown


# --- Ошибки LLM без частичной заметки --------------------------------------


def _mismatched_summaries() -> list[Any]:
    two_sections = custom_summary(SECTIONS[1:])
    return [
        pytest.param(custom_summary(SECTIONS[:2]), id="missing-section"),
        pytest.param(custom_summary((*SECTIONS, "Лишний")), id="extra-section"),
        pytest.param(
            custom_summary(("Примеры кода", "Коротко", "Что попробовать")),
            id="wrong-order",
        ),
        pytest.param(VideoSummary(), id="no-sections"),
        pytest.param(
            VideoSummary(sections=(SummarySection("Коротко", ()), *two_sections.sections)),
            id="empty-section",
        ),
        pytest.param(
            VideoSummary(
                sections=(SummarySection("Коротко", ("Пункт", "   ")), *two_sections.sections)
            ),
            id="empty-item",
        ),
        pytest.param(
            VideoSummary(
                sections=(SummarySection("Короткая", ("Пункт",)), *two_sections.sections)
            ),
            id="renamed-heading",
        ),
    ]


@pytest.mark.parametrize("summary", _mismatched_summaries())
def test_template_response_mismatch_raises_llm_error_without_note(
    summary: VideoSummary,
    data_dir: Path,
) -> None:
    summarizer = FakeSummarizer(summary=summary)
    service = SummaryService(FakeVideoSource(video=make_video()), summarizer, data_dir=data_dir)

    with pytest.raises(LLMError):
        service.process(VIDEO_URL, template=make_template())

    assert len(summarizer.calls) == 1
    assert not data_dir.exists(), "ни .md, ни временных файлов после ошибки"


def test_llm_error_from_provider_leaves_no_partial_note(data_dir: Path) -> None:
    summarizer = FakeSummarizer(error=LLMError())
    service = SummaryService(FakeVideoSource(video=make_video()), summarizer, data_dir=data_dir)

    with pytest.raises(LLMError):
        service.process(VIDEO_URL, template=make_template())

    assert len(summarizer.calls) == 1
    assert not data_dir.exists()


def test_real_adapter_schema_violation_leaves_no_partial_note(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    requests, client = recording_client(custom_content(SECTIONS[:2]))
    service = SummaryService(FakeVideoSource(video=make_video()), client, data_dir=data_dir)

    with pytest.raises(LLMError):
        service.process(VIDEO_URL, template=make_template())

    assert len(requests) == 1, "повтор при некорректном ответе не тратит вызовы"
    assert not data_dir.exists()


def test_wrong_template_type_is_a_caller_error(
    service: SummaryService,
    video_source: FakeVideoSource,
) -> None:
    with pytest.raises(TypeError):
        service.process(VIDEO_URL, template=NoteTemplate(sections=SECTIONS))  # type: ignore[arg-type]

    assert video_source.calls == [], "ошибка аргумента не должна тратить вызовы"


# --- Лог -------------------------------------------------------------------


def _summary_log_messages(caplog: pytest.LogCaptureFixture) -> str:
    return " | ".join(
        record.getMessage() for record in caplog.records if record.name == SUMMARY_LOGGER
    )


def test_log_reports_user_origin_and_section_count_without_template_text(
    caplog: pytest.LogCaptureFixture,
    service: SummaryService,
    summarizer: FakeSummarizer,
) -> None:
    summarizer.summary = custom_summary()

    with caplog.at_level(logging.INFO, logger=SUMMARY_LOGGER):
        service.process(VIDEO_URL, template=make_template())

    messages = _summary_log_messages(caplog)
    assert TEMPLATE_ORIGIN_LABELS[TemplateOrigin.USER] in messages
    assert TEMPLATE_ORIGIN_LABELS[TemplateOrigin.BUILTIN] not in messages
    assert f"разделов: {len(SECTIONS)}" in messages
    for section in SECTIONS:
        assert section not in messages, "текст шаблона в лог не попадает"
    assert TONE not in messages, "тон в лог не попадает"


def test_log_reports_builtin_origin_for_default_path(
    caplog: pytest.LogCaptureFixture,
    service: SummaryService,
) -> None:
    with caplog.at_level(logging.INFO, logger=SUMMARY_LOGGER):
        service.process(VIDEO_URL)

    messages = _summary_log_messages(caplog)
    assert TEMPLATE_ORIGIN_LABELS[TemplateOrigin.BUILTIN] in messages
    assert TEMPLATE_ORIGIN_LABELS[TemplateOrigin.USER] not in messages
    assert f"разделов: {len(BUILTIN_SECTIONS)}" in messages

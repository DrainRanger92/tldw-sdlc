"""Тесты динамических разделов заметки (T-003).

Проверки офлайновые: заметки собираются в памяти, YAML разбирается
``yaml.safe_load``, файлы не пишутся, сеть и токены не нужны. Покрыты порядок
разделов, привычные форматы встроенных заголовков, пользовательский заголовок,
неизменный frontmatter и файл, заканчивающийся одним переводом строки.
"""

from __future__ import annotations

import re
from datetime import date

import pytest
import yaml

from tldw.adapters.llm import SummaryQuote, SummaryTimelineEntry, VideoSummary
from tldw.adapters.youtube import Transcript, Video, VideoMetadata
from tldw.services.note import (
    BUILTIN_SECTION_KINDS,
    FRONTMATTER_KEYS,
    SECTION_HEADINGS,
    Note,
    NoteSection,
    Quote,
    SectionKind,
    TimelineEntry,
    note_filename,
    render_frontmatter,
    render_note,
)
from tldw.services.summary import build_note
from tldw.services.template import BUILTIN_SECTIONS, TemplateValidationError

SECTION_HEADING_RE = re.compile(r"^## .+$", re.MULTILINE)

VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO_TITLE = "Как учиться быстрее: 5 правил"
VIDEO_CHANNEL = "Полезный канал"
VIDEO_DURATION_SECONDS = 3725


def _frontmatter_lines(markdown: str) -> list[str]:
    lines = markdown.splitlines()
    assert lines[0] == "---", "заметка должна начинаться с YAML frontmatter"
    end = lines.index("---", 1)
    return lines[1:end]


def _frontmatter(markdown: str) -> dict:
    return yaml.safe_load("\n".join(_frontmatter_lines(markdown)))


def _section_body(markdown: str, heading: str) -> str:
    start = markdown.index(heading) + len(heading)
    end = markdown.find("\n## ", start)
    return markdown[start:] if end == -1 else markdown[start:end]


def _heading_lines(markdown: str) -> list[str]:
    return [line for line in markdown.splitlines() if line.startswith("#")]


def _video() -> Video:
    return Video(
        url=VIDEO_URL,
        metadata=VideoMetadata(
            video_id="dQw4w9WgXcQ",
            title=VIDEO_TITLE,
            channel=VIDEO_CHANNEL,
            duration_seconds=VIDEO_DURATION_SECONDS,
        ),
        transcript=Transcript(),
    )


def _summary() -> VideoSummary:
    return VideoSummary(
        tldr=("Автор объясняет, как устроена память.",),
        key_ideas=("Учиться маленькими порциями",),
        timeline=(SummaryTimelineEntry("00:12:30", "Как работает память"),),
        quotes=(SummaryQuote("Учиться — значит меняться.", "00:12:30"),),
        follow_ups=("Прочитать книгу «Make It Stick»",),
    )


def _legacy_note(**overrides: object) -> Note:
    """Заметка прежнего пути по пяти полям."""
    fields: dict[str, object] = {
        "title": "Как учиться",
        "source": VIDEO_URL,
        "channel": VIDEO_CHANNEL,
        "duration_seconds": 754,
        "date": date(2026, 11, 26),
        "tags": ("обучение",),
        "tldr": ("Первый вывод.", "Второй вывод."),
        "key_ideas": ("Идея один", "Идея два"),
        "timeline": (TimelineEntry("00:10", "Начало"),),
        "quotes": (Quote("Цитата", "00:05"),),
        "follow_ups": ("Проверить",),
    }
    fields.update(overrides)
    return Note(**fields)  # type: ignore[arg-type]


# --- Порядок разделов -------------------------------------------------------


def test_sections_render_in_given_order() -> None:
    note = _legacy_note(
        sections=(
            NoteSection("Первый раздел", items=("один",)),
            NoteSection("второй раздел", items=("два",)),
            NoteSection("Третий раздел", items=("три",)),
        )
    )

    markdown = render_note(note)

    assert SECTION_HEADING_RE.findall(markdown) == [
        "## Первый раздел",
        "## второй раздел",
        "## Третий раздел",
    ]
    positions = [markdown.index(heading) for heading in SECTION_HEADING_RE.findall(markdown)]
    assert positions == sorted(positions)


def test_builtin_sections_follow_user_order_not_template_order() -> None:
    note = _legacy_note(
        sections=(
            NoteSection("Что проверить", items=("итог",)),
            NoteSection("tl;dr", items=("абзац",)),
            NoteSection("Мой раздел", items=("пункт",)),
        )
    )

    assert SECTION_HEADING_RE.findall(render_note(note)) == [
        "## Что проверить",
        "## TL;DR",
        "## Мой раздел",
    ]


def test_sections_replace_legacy_fields() -> None:
    note = _legacy_note(sections=(NoteSection("Мой раздел", items=("пункт",)),))

    markdown = render_note(note)

    assert SECTION_HEADING_RE.findall(markdown) == ["## Мой раздел"]
    for legacy_heading in SECTION_HEADINGS:
        assert legacy_heading not in markdown
    assert "Первый вывод." not in markdown


def test_empty_sections_keep_legacy_five_section_rendering() -> None:
    note = _legacy_note()
    explicit = _legacy_note(sections=())

    assert note.sections == ()
    assert render_note(note) == render_note(explicit)
    assert SECTION_HEADING_RE.findall(render_note(note)) == list(SECTION_HEADINGS)


def test_empty_sections_render_matches_builtin_golden() -> None:
    """Пустое ``sections`` — байт в байт прежний шаблон из пяти полей."""
    note = Note(
        title="Заметка",
        source="https://youtu.be/x",
        channel="Канал",
        duration_seconds=42,
        date=date(2026, 1, 2),
        tags=("тег",),
        tldr=("Вывод.",),
        key_ideas=("Идея",),
        timeline=(TimelineEntry("00:10", "Начало"),),
        quotes=(Quote("Цитата", "00:05"),),
        follow_ups=("Проверить",),
    )
    expected = (
        "---\n"
        'title: "Заметка"\n'
        'source: "https://youtu.be/x"\n'
        'channel: "Канал"\n'
        "duration: 42\n"
        'date: "2026-01-02"\n'
        "tags:\n"
        '  - "тег"\n'
        "status: processed\n"
        "---\n"
        "\n"
        "## TL;DR\n"
        "\n"
        "Вывод.\n"
        "\n"
        "## Ключевые идеи\n"
        "\n"
        "- Идея\n"
        "\n"
        "## Таймкоды\n"
        "\n"
        "- [00:10] Начало\n"
        "\n"
        "## Цитаты\n"
        "\n"
        "> Цитата\n"
        "> — [00:05]\n"
        "\n"
        "## Что проверить\n"
        "\n"
        "- [ ] Проверить\n"
    )

    assert render_note(note) == expected
    assert note.sections == ()


def test_dynamic_sections_are_separated_by_one_blank_line() -> None:
    note = _legacy_note(
        sections=(
            NoteSection("Первый раздел", items=("один",)),
            NoteSection("Пустой раздел"),
            NoteSection("Что проверить", items=("пункт",)),
        )
    )

    markdown = render_note(note)

    assert "\n\n\n" not in markdown
    assert markdown.endswith("\n")
    assert not markdown.endswith("\n\n")


# --- Привычные форматы встроенных заголовков --------------------------------


def test_builtin_paragraph_heading_keeps_paragraph_format() -> None:
    section = NoteSection("  tl;dr  ", items=("Первый вывод.", "Второй вывод."))
    note = _legacy_note(sections=(section,))

    assert section.heading == "TL;DR"
    assert section.kind is SectionKind.PARAGRAPH
    markdown = render_note(note)
    assert SECTION_HEADING_RE.findall(markdown) == ["## TL;DR"]
    assert _section_body(markdown, "## TL;DR").strip() == "Первый вывод. Второй вывод."


def test_builtin_bullets_heading_keeps_bullet_format() -> None:
    section = NoteSection("КЛЮЧЕВЫЕ ИДЕИ", items=("Идея один", " Идея два "))
    note = _legacy_note(sections=(section,))

    assert section.heading == "Ключевые идеи"
    assert section.kind is SectionKind.BULLETS
    body = _section_body(render_note(note), "## Ключевые идеи")
    assert [line for line in body.splitlines() if line] == ["- Идея один", "- Идея два"]


def test_builtin_timeline_heading_keeps_timecode_format() -> None:
    section = NoteSection(
        " таймкоды ",
        items=(TimelineEntry("00:10", "Начало"), TimelineEntry(75, "Середина")),
    )
    note = _legacy_note(sections=(section,))

    assert section.heading == "Таймкоды"
    assert section.kind is SectionKind.TIMELINE
    body = _section_body(render_note(note), "## Таймкоды")
    assert "- [00:10] Начало" in body
    assert "- [01:15] Середина" in body


def test_builtin_quotes_heading_keeps_quote_format() -> None:
    section = NoteSection("цитаты", items=(Quote("Учиться — значит меняться.", "00:12:30"),))
    note = _legacy_note(sections=(section,))

    assert section.heading == "Цитаты"
    assert section.kind is SectionKind.QUOTES
    body = _section_body(render_note(note), "## Цитаты")
    assert "> Учиться — значит меняться.\n> — [00:12:30]" in body


def test_builtin_checklist_heading_keeps_checklist_format() -> None:
    section = NoteSection("Что проверить", items=("Прочитать книгу",))
    note = _legacy_note(sections=(section,))

    assert section.heading == "Что проверить"
    assert section.kind is SectionKind.CHECKLIST
    body = _section_body(render_note(note), "## Что проверить")
    assert [line for line in body.splitlines() if line] == ["- [ ] Прочитать книгу"]


def test_builtin_heading_overrides_explicit_custom_kind() -> None:
    section = NoteSection(
        "Таймкоды",
        kind=SectionKind.CUSTOM,
        items=(TimelineEntry("00:10", "Начало"),),
    )

    assert section.kind is SectionKind.TIMELINE
    markdown = render_note(_legacy_note(sections=(section,)))
    assert "- [00:10] Начало" in markdown
    assert "- Начало" not in markdown


def test_builtin_section_kinds_cover_template_sections() -> None:
    assert [heading for heading, _ in BUILTIN_SECTION_KINDS] == list(BUILTIN_SECTIONS)
    assert [kind for _, kind in BUILTIN_SECTION_KINDS] == [
        SectionKind.PARAGRAPH,
        SectionKind.BULLETS,
        SectionKind.TIMELINE,
        SectionKind.QUOTES,
        SectionKind.CHECKLIST,
    ]


# --- Пользовательский раздел и безопасность заголовка -----------------------


def test_custom_section_renders_heading_and_bullet_list() -> None:
    note = _legacy_note(sections=(NoteSection("Мой раздел", items=("пункт", "второй")),))

    markdown = render_note(note)

    assert SECTION_HEADING_RE.findall(markdown) == ["## Мой раздел"]
    assert _heading_lines(markdown) == ["## Мой раздел"]
    assert _section_body(markdown, "## Мой раздел").strip() == "- пункт\n- второй"


@pytest.mark.parametrize("raw", ["## Служебный", "# Служебный", "#### Служебный", "## # Служебный"])
def test_custom_heading_cannot_add_markdown_heading(raw: str) -> None:
    section = NoteSection(raw, items=("пункт",))

    assert section.heading == "Служебный"
    markdown = render_note(_legacy_note(sections=(section,)))
    assert _heading_lines(markdown) == ["## Служебный"]
    assert "###" not in markdown


@pytest.mark.parametrize("raw", ["---", "моё --- название", "---\ntitle: подмена\n---"])
def test_custom_heading_cannot_add_frontmatter(raw: str) -> None:
    with pytest.raises(TemplateValidationError):
        NoteSection(raw, items=("пункт",))


@pytest.mark.parametrize("raw", ["", "   ", "###", "\n\n"])
def test_custom_heading_cannot_be_empty(raw: str) -> None:
    with pytest.raises(TemplateValidationError):
        NoteSection(raw, items=("пункт",))


def test_custom_heading_cannot_add_second_line() -> None:
    section = NoteSection("Раздел\n\nпродолжение", items=("пункт",))

    assert section.heading == "Раздел продолжение"
    markdown = render_note(_legacy_note(sections=(section,)))
    assert _heading_lines(markdown) == ["## Раздел продолжение"]
    assert "\n\n\n" not in markdown


def test_custom_heading_rejects_control_characters() -> None:
    with pytest.raises(TemplateValidationError):
        NoteSection("Раздел\x00с байтом", items=("пункт",))


def test_section_items_cannot_add_headings() -> None:
    note = _legacy_note(
        sections=(
            NoteSection("Мой раздел", items=("## подзаголовок", "- пункт", "> цитата")),
        )
    )

    markdown = render_note(note)

    assert _heading_lines(markdown) == ["## Мой раздел"]
    assert "- ## подзаголовок" in markdown


def test_custom_heading_cannot_break_frontmatter() -> None:
    note = _legacy_note(
        sections=(
            NoteSection("status: processed", items=("status: processed",)),
            NoteSection("Мой раздел", items=("---",)),
        )
    )

    markdown = render_note(note)
    data = _frontmatter(markdown)

    assert list(data) == list(FRONTMATTER_KEYS)
    assert data["status"] == "processed"
    assert [line for line in markdown.splitlines() if line == "---"] == ["---", "---"]


# --- Frontmatter и имя файла ------------------------------------------------


def test_dynamic_note_frontmatter_has_seven_fields_in_order() -> None:
    markdown = render_note(
        _legacy_note(sections=(NoteSection("Мой раздел", items=("пункт",)),))
    )

    raw_keys = [
        line.split(":", 1)[0]
        for line in _frontmatter_lines(markdown)
        if line and not line.startswith((" ", "-"))
    ]

    assert raw_keys == list(FRONTMATTER_KEYS)
    assert list(_frontmatter(markdown)) == list(FRONTMATTER_KEYS)


def test_frontmatter_is_identical_for_legacy_and_dynamic_note() -> None:
    legacy = _legacy_note()
    dynamic = _legacy_note(sections=(NoteSection("Мой раздел", items=("пункт",)),))

    assert render_frontmatter(dynamic) == render_frontmatter(legacy)
    assert _frontmatter(render_note(dynamic)) == _frontmatter(render_note(legacy))


def test_dynamic_note_yaml_roundtrips_special_characters_and_unicode() -> None:
    title = 'Он сказал: "Привет, мир" — и ушёл\nво тьму'
    channel = "Канал «N»: про всё"
    note = _legacy_note(
        title=title,
        channel=channel,
        tags=("python", 'тег: "в кавычках"', "кириллица", "日本語", "🎉"),
        sections=(NoteSection("Мой раздел", items=("пункт",)),),
    )

    data = _frontmatter(render_note(note))

    assert data["title"] == title
    assert data["channel"] == channel
    assert data["tags"] == ["python", 'тег: "в кавычках"', "кириллица", "日本語", "🎉"]
    assert data["duration"] == 754
    assert data["date"] == "2026-11-26"
    assert data["status"] == "processed"


def test_note_filename_is_unchanged_for_dynamic_note() -> None:
    legacy = _legacy_note()
    dynamic = _legacy_note(sections=(NoteSection("Мой раздел", items=("пункт",)),))

    assert note_filename(dynamic) == note_filename(legacy) == "как-учиться.md"
    assert note_filename(dynamic).endswith(".md")


# --- Пустые пункты и конец файла -------------------------------------------


def test_blank_items_are_not_rendered() -> None:
    note = _legacy_note(
        sections=(
            NoteSection("TL;DR", items=(" ", "Единственный вывод.", "")),
            NoteSection("Ключевые идеи", items=("", "Одна идея")),
            NoteSection("Что проверить", items=("   ",)),
            NoteSection("Мой раздел", items=("", " пункт ", "\t")),
        )
    )

    markdown = render_note(note)

    assert _section_body(markdown, "## TL;DR").strip() == "Единственный вывод."
    body = _section_body(markdown, "## Ключевые идеи")
    assert [line for line in body.splitlines() if line] == ["- Одна идея"]
    assert _section_body(markdown, "## Что проверить").strip() == ""
    body = _section_body(markdown, "## Мой раздел")
    assert [line for line in body.splitlines() if line] == ["- пункт"]


def test_empty_section_renders_heading_only() -> None:
    note = _legacy_note(sections=(NoteSection("Пустой раздел"),))

    markdown = render_note(note)

    assert SECTION_HEADING_RE.findall(markdown) == ["## Пустой раздел"]
    assert _section_body(markdown, "## Пустой раздел").strip() == ""


def test_dynamic_markdown_ends_with_single_newline() -> None:
    note = _legacy_note(
        sections=(
            NoteSection("Первый раздел", items=("пункт",)),
            NoteSection("Пустой раздел"),
        )
    )

    markdown = render_note(note)

    assert markdown.startswith("---\n")
    assert markdown[-1] == "\n"
    assert markdown[-2] != "\n"
    assert markdown.splitlines()[-1] == "## Пустой раздел"


# --- Нормализация NoteSection ----------------------------------------------


def test_section_kind_values() -> None:
    assert {kind.value for kind in SectionKind} == {
        "paragraph",
        "bullets",
        "timeline",
        "quotes",
        "checklist",
        "custom",
    }
    assert SectionKind("timeline") is SectionKind.TIMELINE
    assert str(SectionKind.CUSTOM) == "custom"


def test_custom_section_is_default_kind() -> None:
    section = NoteSection("Мой раздел", items=("пункт",))

    assert section.kind is SectionKind.CUSTOM
    assert section.items == ("пункт",)


def test_section_items_accept_entries_and_strings() -> None:
    timeline = NoteSection(
        "Таймкоды",
        items=(TimelineEntry("00:10", "Начало"), "01:15 Пара"),
    )
    quotes = NoteSection("Цитаты", items=(Quote("Цитата", "00:05"), "без таймкода"))

    assert timeline.items == (
        TimelineEntry("00:10", "Начало"),
        TimelineEntry("01:15", "Пара"),
    )
    assert quotes.items == (Quote("Цитата", "00:05"), Quote("без таймкода"))
    markdown = render_note(_legacy_note(sections=(timeline,)))
    assert "- [00:10] Начало" in markdown
    assert "- [01:15] Пара" in markdown
    assert "- 01:15 Пара" not in markdown


@pytest.mark.parametrize(
    ("item", "timecode", "title"),
    [
        ("01:15 Пара", "01:15", "Пара"),
        ("[01:15] Пара", "01:15", "Пара"),
        ("01:15 — Пара", "01:15", "Пара"),
        ("01:15 – Пара", "01:15", "Пара"),
        ("1:02:03 Час", "01:02:03", "Час"),
        ("99:99 неверный", "", "99:99 неверный"),
        ("Без таймкода", "", "Без таймкода"),
    ],
)
def test_timeline_string_items_split_leading_timecode(
    item: str,
    timecode: str,
    title: str,
) -> None:
    """Строка пункта «Таймкодов» сохраняет привычный формат таймкода."""
    section = NoteSection("Таймкоды", items=(item,))

    assert section.items == (TimelineEntry(timecode, title),)
    body = _section_body(render_note(_legacy_note(sections=(section,))), "## Таймкоды")
    expected = f"- [{timecode}] {title}" if timecode else f"- {title}"
    assert expected in body


def test_quotes_without_timecode_have_no_empty_attribution() -> None:
    """Цитата без таймкода рендерится без пустого ``> —`` (динамический путь)."""
    section = NoteSection("Цитаты", items=("Учиться — значит меняться.",))
    body = _section_body(render_note(_legacy_note(sections=(section,))), "## Цитаты")

    assert body.strip() == "> Учиться — значит меняться."
    assert "> —" not in body


def test_section_items_are_collapsed_to_one_line() -> None:
    section = NoteSection("Мой раздел", items=("первая\nстрока", "  вторая\tстрока  "))

    assert section.items == ("первая строка", "вторая строка")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"items": "строка вместо пунктов"},
        {"items": (1, 2)},
        {"items": (TimelineEntry("00:10", "Начало"),)},
        {"items": None},
    ],
)
def test_section_items_reject_wrong_types(kwargs: dict[str, object]) -> None:
    with pytest.raises(TypeError):
        NoteSection("Мой раздел", **kwargs)  # type: ignore[arg-type]


def test_section_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        NoteSection("Мой раздел", kind="unknown")  # type: ignore[arg-type]


def test_section_kind_accepts_string_value() -> None:
    section = NoteSection("Таймкоды", kind="timeline")  # type: ignore[arg-type]

    assert section.kind is SectionKind.TIMELINE


def test_note_sections_reject_foreign_items() -> None:
    with pytest.raises(TypeError):
        Note(title="Заметка", sections=("не раздел",))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Note(title="Заметка", sections="не последовательность")  # type: ignore[arg-type]


def test_note_normalizes_sections_to_tuple() -> None:
    section = NoteSection("Мой раздел", items=("пункт",))

    note = Note(title="Заметка", sections=[section])  # type: ignore[arg-type]

    assert note.sections == (section,)


# --- build_note из динамических разделов -----------------------------------


def test_build_note_with_dynamic_sections_keeps_metadata() -> None:
    sections = (
        NoteSection("TL;DR", items=("Мой краткий вывод.",)),
        NoteSection("Мой раздел", items=("пункт",)),
    )

    note = build_note(_video(), _summary(), sections=sections)
    markdown = render_note(note)
    data = _frontmatter(markdown)

    assert note.sections == sections
    assert note.title == VIDEO_TITLE
    assert note.source == VIDEO_URL
    assert note.channel == VIDEO_CHANNEL
    assert note.duration_seconds == VIDEO_DURATION_SECONDS
    assert note.date == date.today()
    assert note.tags == ()
    assert data == {
        "title": VIDEO_TITLE,
        "source": VIDEO_URL,
        "channel": VIDEO_CHANNEL,
        "duration": VIDEO_DURATION_SECONDS,
        "date": date.today().isoformat(),
        "tags": [],
        "status": "processed",
    }
    assert list(data) == list(FRONTMATTER_KEYS)
    assert SECTION_HEADING_RE.findall(markdown) == ["## TL;DR", "## Мой раздел"]
    assert note_filename(note) == "как-учиться-быстрее-5-правил.md"


def test_build_note_without_sections_keeps_legacy_note() -> None:
    note = build_note(_video(), _summary())

    assert note.sections == ()
    markdown = render_note(note)
    assert SECTION_HEADING_RE.findall(markdown) == list(SECTION_HEADINGS)
    assert note.tldr == ("Автор объясняет, как устроена память.",)
    assert note.timeline == (TimelineEntry("00:12:30", "Как работает память"),)
    assert note.quotes == (Quote("Учиться — значит меняться.", "00:12:30"),)


def test_build_note_dynamic_frontmatter_matches_legacy() -> None:
    legacy = build_note(_video(), _summary())
    dynamic = build_note(
        _video(),
        _summary(),
        sections=(NoteSection("Мой раздел", items=("пункт",)),),
    )

    assert render_frontmatter(dynamic) == render_frontmatter(legacy)
    assert note_filename(dynamic) == note_filename(legacy)


def test_build_note_dynamic_keeps_source_override() -> None:
    note = build_note(
        _video(),
        _summary(),
        source="https://youtu.be/dQw4w9WgXcQ",
        sections=(NoteSection("Мой раздел", items=("пункт",)),),
    )

    assert note.source == "https://youtu.be/dQw4w9WgXcQ"
    assert _frontmatter(render_note(note))["source"] == "https://youtu.be/dQw4w9WgXcQ"

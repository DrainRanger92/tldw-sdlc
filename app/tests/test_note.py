"""Тесты модели заметки и рендера Obsidian-Markdown.

Проверки полностью офлайновые: заметка собирается в памяти, YAML разбирается
``yaml.safe_load``, файлы не пишутся. Покрыты полный шаблон из ``specs/product.md``,
спецсимволы в YAML, пустые значения и формирование имени файла.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from tldw.services.note import (
    FALLBACK_SLUG,
    FALLBACK_TITLE,
    FRONTMATTER_KEYS,
    MAX_SLUG_LENGTH,
    NOTE_STATUS,
    SECTION_FOLLOW_UPS,
    SECTION_HEADINGS,
    SECTION_KEY_IDEAS,
    SECTION_QUOTES,
    SECTION_TIMELINE,
    SECTION_TLDR,
    Note,
    Quote,
    TimelineEntry,
    format_timecode,
    note_filename,
    render_frontmatter,
    render_note,
)

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INVALID_FILENAME_CHARS = set('\\/:*?"<>|')
SECTION_HEADING_RE = re.compile(r"^## .+$", re.MULTILINE)


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


@pytest.fixture
def full_note() -> Note:
    """Заметка с заполненными секциями — эталон полного шаблона."""
    return Note(
        title="Как учиться быстрее: 5 правил",
        source="https://youtu.be/dQw4w9WgXcQ",
        channel="Полезный канал",
        duration_seconds=3725,
        date=date(2026, 11, 26),
        tags=("обучение", "продуктивность", "python"),
        tldr=(
            "Автор объясняет, как устроена память.",
            "Главный приём — интервальные повторения.",
            "Практика важнее пассивного просмотра.",
        ),
        key_ideas=(
            "Учиться нужно маленькими порциями",
            "Повторять материал через растущие интервалы",
            "Объяснять тему вслух",
            "Смешивать разные типы задач",
            "Отдых — часть обучения",
        ),
        timeline=(
            TimelineEntry("00:00", "Введение"),
            TimelineEntry("00:12:30", "Как работает память"),
            TimelineEntry("01:02:03", "Итоги"),
        ),
        quotes=(
            Quote("Учиться — значит меняться.", "00:12:30"),
            Quote("Практика важнее теории.", "01:00:00"),
        ),
        follow_ups=("Прочитать книгу «Make It Stick»", "Попробовать метод на этой неделе"),
    )


def test_full_template_renders_frontmatter_and_sections(full_note: Note) -> None:
    markdown = render_note(full_note)

    assert markdown.startswith("---\n")
    assert _frontmatter(markdown) == {
        "title": "Как учиться быстрее: 5 правил",
        "source": "https://youtu.be/dQw4w9WgXcQ",
        "channel": "Полезный канал",
        "duration": 3725,
        "date": "2026-11-26",
        "tags": ["обучение", "продуктивность", "python"],
        "status": "processed",
    }
    assert ISO_DATE_RE.match(_frontmatter(markdown)["date"])


def test_frontmatter_has_exactly_template_fields_in_order(full_note: Note) -> None:
    markdown = render_note(full_note)

    raw_keys = [
        line.split(":", 1)[0]
        for line in _frontmatter_lines(markdown)
        if line and not line.startswith((" ", "-"))
    ]

    assert raw_keys == list(FRONTMATTER_KEYS)
    assert list(_frontmatter(markdown)) == list(FRONTMATTER_KEYS)
    assert _frontmatter(markdown)["status"] == NOTE_STATUS == "processed"


def test_all_five_sections_present_in_template_order(full_note: Note) -> None:
    markdown = render_note(full_note)

    assert SECTION_HEADING_RE.findall(markdown) == [
        SECTION_TLDR,
        SECTION_KEY_IDEAS,
        SECTION_TIMELINE,
        SECTION_QUOTES,
        SECTION_FOLLOW_UPS,
    ]
    assert list(SECTION_HEADINGS) == [
        "## TL;DR",
        "## Ключевые идеи",
        "## Таймкоды",
        "## Цитаты",
        "## Что проверить",
    ]
    positions = [markdown.index(heading) for heading in SECTION_HEADINGS]
    assert positions == sorted(positions)


def test_full_template_renders_expected_section_content(full_note: Note) -> None:
    markdown = render_note(full_note)

    assert _section_body(markdown, SECTION_TLDR).strip() == " ".join(full_note.tldr)
    assert [line for line in _section_body(markdown, SECTION_KEY_IDEAS).splitlines() if line] == [
        f"- {idea}" for idea in full_note.key_ideas
    ]
    assert "- [00:00] Введение" in _section_body(markdown, SECTION_TIMELINE)
    assert "- [01:02:03] Итоги" in _section_body(markdown, SECTION_TIMELINE)
    quotes_body = _section_body(markdown, SECTION_QUOTES)
    assert "> Учиться — значит меняться.\n> — [00:12:30]" in quotes_body
    assert "> Практика важнее теории.\n> — [01:00:00]" in quotes_body
    assert "- [ ] Прочитать книгу «Make It Stick»" in _section_body(markdown, SECTION_FOLLOW_UPS)
    assert markdown.endswith("\n")
    assert not markdown.endswith("\n\n")


def test_render_frontmatter_is_fenced_yaml_block() -> None:
    block = render_frontmatter(Note(title="Заголовок", date=date(2026, 1, 2)))

    assert block.startswith("---\n")
    assert block.endswith("\n---")
    assert yaml.safe_load(block.strip("-").strip())


@pytest.mark.parametrize(
    "title",
    [
        'Он сказал: "Привет, мир" — и ушёл',
        "true",
        "no",
        "null",
        "123",
        "2026-11-26",
        "- пункт списка",
        "# не тег",
        "a: b: c",
        "кавычки 'одинарные' и \"двойные\"",
        "строка\nс переводом",
        "юникод: «ёлка», 日本語, 🎉",
        "[a, b]",
        "{a: b}",
        "tag # comment",
        "~",
        "on",
        "@at",
        "%percent",
        "нулевой \0 байт",
    ],
)
def test_yaml_title_roundtrips_for_special_characters(title: str) -> None:
    note = Note(title=title, date=date(2026, 1, 1))

    assert _frontmatter(render_note(note))["title"] == title


def test_yaml_escapes_quotes_colons_unicode_and_control_characters() -> None:
    title = 'Он сказал: "Привет, мир" — и ушёл\nво тьму'
    source = "https://youtu.be/x?a=1&b=2#t=90"
    channel = "Канал «N»: про всё"
    tags = (
        "python",
        'тег: "в кавычках"',
        "кириллица",
        "back\\slash",
        "#с решёткой",
        "tab\there",
    )
    note = Note(
        title=title,
        source=source,
        channel=channel,
        duration_seconds=0,
        date="2024-02-29",
        tags=tags,
    )

    data = _frontmatter(render_note(note))

    assert data["title"] == title
    assert data["source"] == source
    assert data["channel"] == channel
    assert data["duration"] == 0
    assert data["date"] == "2024-02-29"
    assert data["tags"] == [
        "python",
        'тег: "в кавычках"',
        "кириллица",
        "back\\slash",
        "с решёткой",
        "tab here",
    ]
    assert data["status"] == NOTE_STATUS


def test_empty_values_keep_valid_yaml_and_all_sections() -> None:
    note = Note(
        title="",
        source="",
        channel="",
        duration_seconds=None,
        date=date(2026, 1, 1),
        tags=(),
        tldr=(),
        key_ideas=(),
        timeline=(),
        quotes=(),
        follow_ups=(),
    )

    markdown = render_note(note)
    data = _frontmatter(markdown)

    assert data == {
        "title": FALLBACK_TITLE,
        "source": "",
        "channel": "",
        "duration": None,
        "date": "2026-01-01",
        "tags": [],
        "status": NOTE_STATUS,
    }
    assert SECTION_HEADING_RE.findall(markdown) == list(SECTION_HEADINGS)
    for heading in SECTION_HEADINGS:
        assert _section_body(markdown, heading).strip() == ""
    assert markdown.endswith("\n")
    assert not markdown.endswith("\n\n")


def test_blank_items_and_duplicate_tags_are_dropped() -> None:
    note = Note(
        title="Заметка",
        tags=("#python", "python", "  ", ""),
        tldr=(" ", "Единственное предложение."),
        key_ideas=("", "Одна идея"),
        timeline=(TimelineEntry("", ""), TimelineEntry("00:10", "")),
        quotes=(Quote("", ""), Quote("Цитата", "")),
        follow_ups=("",),
    )

    markdown = render_note(note)

    assert _frontmatter(markdown)["tags"] == ["python"]
    assert _section_body(markdown, SECTION_TLDR).strip() == "Единственное предложение."
    assert [line for line in _section_body(markdown, SECTION_KEY_IDEAS).splitlines() if line] == [
        "- Одна идея"
    ]
    assert [line for line in _section_body(markdown, SECTION_TIMELINE).splitlines() if line] == [
        "- [00:10]"
    ]
    assert [line for line in _section_body(markdown, SECTION_QUOTES).splitlines() if line] == [
        "> Цитата"
    ], "без таймкода атрибуция не рисуется пустой строкой «> —»"
    assert _section_body(markdown, SECTION_FOLLOW_UPS).strip() == ""


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Как учиться: 5 «правил»", "как-учиться-5-правил.md"),
        ("../../etc/passwd", "etc-passwd.md"),
        ("  Python / asyncio: гайд  ", "python-asyncio-гайд.md"),
        ("v1.2 — релиз!?", "v1-2-релиз.md"),
        ("Слова через   пробелы", "слова-через-пробелы.md"),
    ],
)
def test_filename_is_slug_with_md_extension(title: str, expected: str) -> None:
    filename = note_filename(Note(title=title))

    assert filename == expected
    assert filename.endswith(".md")
    assert not (set(filename) & INVALID_FILENAME_CHARS)
    stem = filename.removesuffix(".md")
    assert stem
    assert not stem.startswith(".")
    assert "." not in stem


@pytest.mark.parametrize("title", ["", "   ", "!!!", "🎉🎉", "«»", "---", "///"])
def test_filename_falls_back_for_blank_or_symbolic_title(title: str) -> None:
    filename = note_filename(Note(title=title))

    assert filename == f"{FALLBACK_SLUG}.md"
    assert filename != ".md"


def test_filename_is_truncated_to_slug_limit() -> None:
    filename = note_filename(Note(title="Очень длинный заголовок " * 20))

    assert filename.endswith(".md")
    stem = filename.removesuffix(".md")
    assert 0 < len(stem) <= MAX_SLUG_LENGTH
    assert not stem.endswith("-")


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00"),
        (65, "01:05"),
        (3599, "59:59"),
        (3600, "01:00:00"),
        (3725, "01:02:05"),
        (360000, "100:00:00"),
    ],
)
def test_format_timecode(seconds: int, expected: str) -> None:
    assert format_timecode(seconds) == expected


def test_format_timecode_rejects_negative_and_non_integer() -> None:
    with pytest.raises(ValueError):
        format_timecode(-1)
    with pytest.raises(TypeError):
        format_timecode(1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        format_timecode(True)  # type: ignore[arg-type]


def test_timeline_accepts_seconds_and_renders_timecodes() -> None:
    note = Note(
        title="Таймкоды",
        timeline=(TimelineEntry(75, "Начало"), TimelineEntry("01:02:03", "Финал")),
    )

    markdown = render_note(note)

    assert "- [01:15] Начало" in _section_body(markdown, SECTION_TIMELINE)
    assert "- [01:02:03] Финал" in _section_body(markdown, SECTION_TIMELINE)


@pytest.mark.parametrize(
    "value",
    [date(2026, 11, 26), datetime(2026, 11, 26, 12, 30), "2026-11-26", " 2026-11-26 "],
)
def test_date_is_normalized_to_iso(value: object) -> None:
    note = Note(title="Дата", date=value)  # type: ignore[arg-type]

    assert _frontmatter(render_note(note))["date"] == "2026-11-26"


@pytest.mark.parametrize("value", ["26.11.2026", "не дата", 20261126])
def test_invalid_date_is_rejected(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        Note(title="Дата", date=value)  # type: ignore[arg-type]


def test_invalid_duration_is_rejected() -> None:
    with pytest.raises(ValueError):
        Note(title="Длительность", duration_seconds=-1)
    with pytest.raises(TypeError):
        Note(title="Длительность", duration_seconds=1.5)  # type: ignore[arg-type]


def test_no_external_files_are_touched(tmp_path: Path) -> None:
    """Рендер не пишет на диск: сохранение — задача слоя storage."""
    note = Note(title="Без файлов", date=date(2026, 1, 1))

    render_note(note)
    note_filename(note)

    assert list(tmp_path.iterdir()) == []

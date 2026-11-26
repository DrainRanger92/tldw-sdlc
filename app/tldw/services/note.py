"""Модель заметки TLDW и рендер в Obsidian-совместимый Markdown.

Шаблон взят из ``specs/product.md``: YAML frontmatter с полями ``title``,
``source``, ``channel``, ``duration``, ``date``, ``tags``, ``status: processed``
и пять секций в фиксированном порядке — ``## TL;DR``, ``## Ключевые идеи``,
``## Таймкоды``, ``## Цитаты``, ``## Что проверить``.

Модуль не ходит в сеть и ничего не знает про Telegram: сервис конспекта
собирает :class:`Note`, а слой ``storage`` сохраняет готовый текст под именем
из :func:`note_filename`. YAML формируется вручную (без внешней зависимости),
поэтому все строковые значения экранируются как двойные кавычки YAML:
кавычки, двоеточия, переводы строк, управляющие символы и юникод дают
корректный YAML.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final

#: Статус обработанной заметки — фиксирован шаблоном, а не приходит из LLM.
NOTE_STATUS: Final = "processed"
#: Заголовок-фолбэк: заметка всегда должна быть непустой и находимой.
FALLBACK_TITLE: Final = "Без названия"
#: Фолбэк имени файла, если slug заголовка получился пустым.
FALLBACK_SLUG: Final = "tldw-note"
#: Ограничение длины slug: длинные заголовки не должны ломать лимиты файловых систем.
MAX_SLUG_LENGTH: Final = 80

#: Поля frontmatter ровно в порядке шаблона.
FRONTMATTER_KEYS: Final[tuple[str, ...]] = (
    "title",
    "source",
    "channel",
    "duration",
    "date",
    "tags",
    "status",
)

SECTION_TLDR: Final = "## TL;DR"
SECTION_KEY_IDEAS: Final = "## Ключевые идеи"
SECTION_TIMELINE: Final = "## Таймкоды"
SECTION_QUOTES: Final = "## Цитаты"
SECTION_FOLLOW_UPS: Final = "## Что проверить"

#: Заголовки секций ровно в порядке шаблона.
SECTION_HEADINGS: Final[tuple[str, ...]] = (
    SECTION_TLDR,
    SECTION_KEY_IDEAS,
    SECTION_TIMELINE,
    SECTION_QUOTES,
    SECTION_FOLLOW_UPS,
)

_YAML_ESCAPES: Final[dict[str, str]] = {
    "\\": "\\\\",
    '"': '\\"',
    "\0": "\\0",
    "\a": "\\a",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\v": "\\v",
    "\f": "\\f",
    "\r": "\\r",
    "\x1b": "\\e",
    "\u0085": "\\N",
    "\u2028": "\\L",
    "\u2029": "\\P",
}
#: Символы, которые YAML не допускает в UTF-8 или трактует как перевод строки.
_YAML_UNSAFE_EXTRA: Final[frozenset[str]] = frozenset({"\ufeff"})


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """Смысловой блок видео: таймкод и короткое название.

    ``timecode`` принимает готовую строку (``"01:02:03"``) или число секунд —
    целое число приводится к виду ``MM:SS`` / ``HH:MM:SS``.
    """

    timecode: str
    title: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "timecode", _normalize_timecode(self.timecode))
        object.__setattr__(self, "title", _one_line(_text(self.title)))


@dataclass(frozen=True, slots=True)
class Quote:
    """Цитата из видео с необязательным таймкодом."""

    text: str
    timecode: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _one_line(_text(self.text)))
        object.__setattr__(self, "timecode", _normalize_timecode(self.timecode))


@dataclass(frozen=True, slots=True)
class Note:
    """Заметка по видео — данные без привязки к Telegram и Obsidian.

    Поля frontmatter: ``title``, ``source``, ``channel``, ``duration_seconds``,
    ``date``, ``tags``; ``status`` всегда ``processed`` (см. :data:`NOTE_STATUS`).
    ``duration_seconds`` — длительность в секундах или ``None``, если она
    неизвестна; ``date`` — дата обработки, принимается :class:`datetime.date`,
    :class:`datetime.datetime` или ISO-строка ``ГГГГ-ММ-ДД``.

    Значения нормализуются в ``__post_init__``: строки обрезаются по краям,
    списки становятся кортежами без пустых элементов, теги теряют ведущий
    ``#`` и дубликаты, а элементы таймлайна/цитат без содержимого отбрасываются.
    Рендер при этом всегда оставляет все пять секций шаблона.
    """

    title: str
    source: str = ""
    channel: str = ""
    duration_seconds: int | None = None
    date: date = field(default_factory=date.today)
    tags: tuple[str, ...] = ()
    tldr: tuple[str, ...] = ()
    key_ideas: tuple[str, ...] = ()
    timeline: tuple[TimelineEntry, ...] = ()
    quotes: tuple[Quote, ...] = ()
    follow_ups: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _text(self.title))
        object.__setattr__(self, "source", _text(self.source))
        object.__setattr__(self, "channel", _text(self.channel))
        object.__setattr__(self, "duration_seconds", _validate_duration(self.duration_seconds))
        object.__setattr__(self, "date", _coerce_date(self.date))
        object.__setattr__(self, "tags", _normalize_tags(self.tags))
        object.__setattr__(self, "tldr", _normalize_items(self.tldr))
        object.__setattr__(self, "key_ideas", _normalize_items(self.key_ideas))
        object.__setattr__(self, "timeline", _normalize_timeline(self.timeline))
        object.__setattr__(self, "quotes", _normalize_quotes(self.quotes))
        object.__setattr__(self, "follow_ups", _normalize_items(self.follow_ups))


def render_note(note: Note) -> str:
    """Отрендерить заметку в Obsidian-совместимый Markdown.

    Результат всегда содержит YAML frontmatter и все пять секций шаблона
    в порядке :data:`SECTION_HEADINGS`; пустые секции остаются пустыми,
    но заголовки не исчезают. Файл заканчивается одним переводом строки.
    """
    blocks = (
        render_frontmatter(note),
        _render_section(SECTION_TLDR, _render_paragraph(note.tldr)),
        _render_section(SECTION_KEY_IDEAS, _render_bullets(note.key_ideas)),
        _render_section(SECTION_TIMELINE, _render_timeline(note.timeline)),
        _render_section(SECTION_QUOTES, _render_quotes(note.quotes)),
        _render_section(SECTION_FOLLOW_UPS, _render_follow_ups(note.follow_ups)),
    )
    return "\n\n".join(block.rstrip() for block in blocks) + "\n"


def render_frontmatter(note: Note) -> str:
    """Отрендерить YAML frontmatter (включая разделители ``---``).

    Поля идут ровно в порядке :data:`FRONTMATTER_KEYS`; строки всегда
    заключаются в двойные кавычки с YAML-экранированием, дата — ISO
    (``ГГГГ-ММ-ДД``), ``duration`` — целые секунды или ``null``.
    """
    lines = [
        "---",
        f"title: {_yaml_string(_display_title(note))}",
        f"source: {_yaml_string(note.source)}",
        f"channel: {_yaml_string(note.channel)}",
        f"duration: {_yaml_duration(note.duration_seconds)}",
        f"date: {_yaml_string(note.date.isoformat())}",
    ]
    if note.tags:
        lines.append("tags:")
        lines.extend(f"  - {_yaml_string(tag)}" for tag in note.tags)
    else:
        lines.append("tags: []")
    lines.append(f"status: {NOTE_STATUS}")
    lines.append("---")
    return "\n".join(lines)


def note_filename(note: Note) -> str:
    """Имя файла заметки: slug заголовка плюс расширение ``.md``."""
    return f"{slugify_title(note.title)}.md"


def slugify_title(title: str) -> str:
    """Сделать безопасный slug из заголовка.

    Юникодные буквы и цифры сохраняются (Obsidian дружит с кириллицей),
    всё остальное — пробелы, пунктуация, ``/``, ``:``, кавычки, точки —
    схлопывается в ``-``. Slug обрезается до :data:`MAX_SLUG_LENGTH`
    символов; для пустого/непригодного заголовка возвращается
    :data:`FALLBACK_SLUG`, поэтому ``.md`` никогда не остаётся без имени.
    """
    normalized = unicodedata.normalize("NFKC", _text(title)).casefold()
    raw_slug = "".join(char if char.isalnum() else "-" for char in normalized)
    slug = "-".join(part for part in raw_slug.split("-") if part)
    if len(slug) > MAX_SLUG_LENGTH:
        slug = slug[:MAX_SLUG_LENGTH].rstrip("-")
    return slug or FALLBACK_SLUG


def format_timecode(total_seconds: int) -> str:
    """Преобразовать секунды в таймкод ``MM:SS`` или ``HH:MM:SS``.

    :raises TypeError: если передано не целое число секунд.
    :raises ValueError: если секунды отрицательные.
    """
    if isinstance(total_seconds, bool) or not isinstance(total_seconds, int):
        raise TypeError("таймкод задаётся целым числом секунд")
    if total_seconds < 0:
        raise ValueError("таймкод не может быть отрицательным")
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _display_title(note: Note) -> str:
    return note.title or FALLBACK_TITLE


def _render_section(heading: str, body_lines: list[str]) -> str:
    if not body_lines:
        return heading
    return "\n".join([heading, "", *body_lines])


def _render_paragraph(items: tuple[str, ...]) -> list[str]:
    if not items:
        return []
    return [" ".join(items)]


def _render_bullets(items: tuple[str, ...]) -> list[str]:
    return [f"- {item}" for item in items]


def _render_timeline(entries: tuple[TimelineEntry, ...]) -> list[str]:
    lines = []
    for entry in entries:
        if entry.timecode and entry.title:
            lines.append(f"- [{entry.timecode}] {entry.title}")
        elif entry.timecode:
            lines.append(f"- [{entry.timecode}]")
        else:
            lines.append(f"- {entry.title}")
    return lines


def _render_quotes(quotes: tuple[Quote, ...]) -> list[str]:
    lines: list[str] = []
    for quote in quotes:
        if lines:
            lines.append("")
        if quote.text:
            lines.append(f"> {quote.text}")
        lines.append(f"> — [{quote.timecode}]" if quote.timecode else "> —")
    return lines


def _render_follow_ups(items: tuple[str, ...]) -> list[str]:
    return [f"- [ ] {item}" for item in items]


def _yaml_string(value: str) -> str:
    """Строковый YAML-скаляр в двойных кавычках с экранированием.

    Двойные кавычки — самый безопасный стиль: двоеточия, решётки, кавычки,
    юникод и переводы строк не меняют смысл значения. Управляющие символы
    и юникодные разделители строк заменяются escape-последовательностями,
    чтобы файл оставался валидным UTF-8 YAML.
    """
    parts = ['"']
    for char in value:
        if char in _YAML_ESCAPES:
            parts.append(_YAML_ESCAPES[char])
            continue
        code = ord(char)
        if code < 0x20 or 0x7F <= code <= 0x9F or char in _YAML_UNSAFE_EXTRA:
            parts.append(f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}")
            continue
        if 0xD800 <= code <= 0xDFFF:  # одиночный суррогат нельзя записать в UTF-8
            parts.append(f"\\u{code:04x}")
            continue
        parts.append(char)
    parts.append('"')
    return "".join(parts)


def _yaml_duration(seconds: int | None) -> str:
    return "null" if seconds is None else str(seconds)


def _normalize_timecode(value: str | int) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return format_timecode(value)
    return _text(value)


def _normalize_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for tag in tags:
        cleaned = _one_line(_text(tag).lstrip("#"))
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return tuple(normalized)


def _normalize_items(items: tuple[str, ...]) -> tuple[str, ...]:
    normalized = []
    for item in items:
        text = _one_line(_text(item))
        if text:
            normalized.append(text)
    return tuple(normalized)


def _normalize_timeline(entries: tuple[TimelineEntry, ...]) -> tuple[TimelineEntry, ...]:
    return tuple(entry for entry in entries if entry.timecode or entry.title)


def _normalize_quotes(quotes: tuple[Quote, ...]) -> tuple[Quote, ...]:
    return tuple(quote for quote in quotes if quote.text or quote.timecode)


def _validate_duration(seconds: int | None) -> int | None:
    if seconds is None:
        return None
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise TypeError("duration_seconds должен быть целым числом секунд или None")
    if seconds < 0:
        raise ValueError("duration_seconds не может быть отрицательным")
    return seconds


def _coerce_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(_text(value))
        except ValueError:
            raise ValueError("date должен быть датой ISO (ГГГГ-ММ-ДД) или datetime.date") from None
    raise TypeError("date должен быть datetime.date, datetime.datetime или ISO-строкой")


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _one_line(value: str) -> str:
    return " ".join(value.split())


__all__ = [
    "FALLBACK_SLUG",
    "FALLBACK_TITLE",
    "FRONTMATTER_KEYS",
    "MAX_SLUG_LENGTH",
    "NOTE_STATUS",
    "SECTION_FOLLOW_UPS",
    "SECTION_HEADINGS",
    "SECTION_KEY_IDEAS",
    "SECTION_QUOTES",
    "SECTION_TIMELINE",
    "SECTION_TLDR",
    "Note",
    "Quote",
    "TimelineEntry",
    "format_timecode",
    "note_filename",
    "render_frontmatter",
    "render_note",
    "slugify_title",
]

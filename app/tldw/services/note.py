"""Модель заметки TLDW и рендер в Obsidian-совместимый Markdown.

Шаблон взят из ``specs/product.md``: YAML frontmatter с полями ``title``,
``source``, ``channel``, ``duration``, ``date``, ``tags``, ``status: processed``
и пять секций в фиксированном порядке — ``## TL;DR``, ``## Ключевые идеи``,
``## Таймкоды``, ``## Цитаты``, ``## Что проверить``.

Помимо этого встроенного пути модель умеет динамические разделы
(``specs/002-user-note-templates``): :class:`NoteSection` хранит заголовок,
вид содержимого (:class:`SectionKind`) и пункты, а :attr:`Note.sections`
задаёт разделы и их порядок. Для заголовков, совпавших со встроенными без
учёта регистра и крайних пробелов, сохраняется привычный формат раздела;
остальные рендерятся как ``## Название`` и маркированный список. Если
``Note.sections`` пусто, рендер идёт прежним путём по пяти полям, поэтому
встроенный шаблон и его файл не меняются.

Модуль не ходит в сеть и ничего не знает про Telegram: сервис конспекта
собирает :class:`Note`, а слой ``storage`` сохраняет готовый текст под именем
из :func:`note_filename`. YAML формируется вручную (без внешней зависимости),
поэтому все строковые значения экранируются как двойные кавычки YAML:
кавычки, двоеточия, переводы строк, управляющие символы и юникод дают
корректный YAML.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Final, cast

from tldw.services.template import BUILTIN_SECTIONS, normalize_heading
from tldw.text import one_line

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

#: Заголовки секций — производные от встроенного шаблона
#: :data:`tldw.services.template.BUILTIN_SECTIONS`, чтобы имена ``SECTION_*``
#: и пользовательский шаблон не разъезжались.
SECTION_TLDR: Final = f"## {BUILTIN_SECTIONS[0]}"
SECTION_KEY_IDEAS: Final = f"## {BUILTIN_SECTIONS[1]}"
SECTION_TIMELINE: Final = f"## {BUILTIN_SECTIONS[2]}"
SECTION_QUOTES: Final = f"## {BUILTIN_SECTIONS[3]}"
SECTION_FOLLOW_UPS: Final = f"## {BUILTIN_SECTIONS[4]}"

#: Заголовки секций ровно в порядке шаблона.
SECTION_HEADINGS: Final[tuple[str, ...]] = (
    SECTION_TLDR,
    SECTION_KEY_IDEAS,
    SECTION_TIMELINE,
    SECTION_QUOTES,
    SECTION_FOLLOW_UPS,
)


class SectionKind(StrEnum):
    """Вид содержимого динамического раздела — он же формат рендера.

    * ``PARAGRAPH`` — один абзац: пункты склеиваются пробелом;
    * ``BULLETS`` — маркированный список ``- пункт``;
    * ``TIMELINE`` — пункты с таймкодами ``- [MM:SS] название``;
    * ``QUOTES`` — цитаты ``> текст`` и, если таймкод задан,
      ``> — [таймкод]`` (без пустого ``> —``);
    * ``CHECKLIST`` — чеклист ``- [ ] пункт``;
    * ``CUSTOM`` — пользовательский раздел; рендерится как ``BULLETS``,
      но явно показывает, что заголовок пришёл из шаблона пользователя.
    """

    PARAGRAPH = "paragraph"
    BULLETS = "bullets"
    TIMELINE = "timeline"
    QUOTES = "quotes"
    CHECKLIST = "checklist"
    CUSTOM = "custom"


#: Привычный вид рендера для встроенных заголовков: пары
#: ``(заголовок шаблона, вид)`` в порядке :data:`BUILTIN_SECTIONS`.
#: По этому отображению :class:`NoteSection` узнаёт заголовки, для которых
#: нужно сохранить формат встроенного шаблона, а не маркированный список.
BUILTIN_SECTION_KINDS: Final[tuple[tuple[str, SectionKind], ...]] = tuple(
    zip(
        BUILTIN_SECTIONS,
        (
            SectionKind.PARAGRAPH,
            SectionKind.BULLETS,
            SectionKind.TIMELINE,
            SectionKind.QUOTES,
            SectionKind.CHECKLIST,
        ),
        strict=True,
    )
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
#: Ведущий таймкод пункта раздела «Таймкоды»: ``01:23``, ``[01:23]``,
#: ``01:23:45`` плюс необязательный разделитель перед текстом.
_TIMECODE_PREFIX_RE: Final = re.compile(
    r"^\[?(?P<first>\d{1,2}):(?P<second>\d{2})(?::(?P<third>\d{2}))?\]?"
    r"[\s—–-]*(?P<text>.*)$"
)


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
        object.__setattr__(self, "title", one_line(_text(self.title)))


@dataclass(frozen=True, slots=True)
class Quote:
    """Цитата из видео с необязательным таймкодом."""

    text: str
    timecode: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", one_line(_text(self.text)))
        object.__setattr__(self, "timecode", _normalize_timecode(self.timecode))


@dataclass(frozen=True, slots=True)
class NoteSection:
    """Динамический раздел заметки: заголовок, вид содержимого и пункты.

    ``heading`` — название без ``##``: оно нормализуется тем же правилом, что и
    заголовок шаблона (:func:`tldw.services.template.normalize_heading`), —
    ведущие ``#`` снимаются, переводы строк схлопываются, а ``---``, YAML
    frontmatter, управляющие символы и пустое имя отвергаются. Поэтому раздел
    не может разорвать frontmatter или добавить лишний заголовок.

    ``kind`` — формат рендера. Если ``heading`` совпадает со встроенным
    заголовком без учёта регистра и крайних пробелов (``  ## ключевые идеи ``),
    ``kind`` берётся из :data:`BUILTIN_SECTION_KINDS`, а сам заголовок
    приводится к каноническому виду (``Ключевые идеи``): привычный формат
    встроенного шаблона сохраняется. Для остальных заголовков действует
    заданный ``kind`` (``CUSTOM`` по умолчанию) — ``## Название`` и ``- пункт``.

    ``items`` — пункты раздела в его порядке; пустые пункты отбрасываются:

    * для абзаца, списка, чеклиста и пользовательского раздела — строки;
    * для таймкодов — :class:`TimelineEntry` или строка (пункт без таймкода);
    * для цитат — :class:`Quote` или строка (цитата без таймкода).

    :raises tldw.services.template.TemplateValidationError: если заголовок
        пустой, не текст, слишком длинный или содержит служебные символы.
    :raises TypeError: если пункты не последовательность или не того вида.
    :raises ValueError: если ``kind`` — неизвестный вид раздела.
    """

    heading: str
    kind: SectionKind = SectionKind.CUSTOM
    items: tuple[TimelineEntry | Quote | str, ...] = ()

    def __post_init__(self) -> None:
        heading = normalize_heading(self.heading)
        kind = _coerce_section_kind(self.kind)
        for builtin_heading, builtin_kind in BUILTIN_SECTION_KINDS:
            if builtin_heading.casefold() == heading.casefold():
                heading, kind = builtin_heading, builtin_kind
                break
        object.__setattr__(self, "heading", heading)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "items", _normalize_section_items(kind, self.items))


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

    ``sections`` — необязательный динамический путь: разделы в порядке
    применения. Если он заполнен, :func:`render_note` рендерит ровно эти
    разделы, а пять полей ``tldr``…``follow_ups`` не используются. Пустой
    ``sections`` (по умолчанию) включает прежний путь по пяти полям встроенного
    шаблона, поэтому старые заметки рендерятся байт в байт как раньше.
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
    sections: tuple[NoteSection, ...] = ()

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
        object.__setattr__(self, "sections", _normalize_note_sections(self.sections))


def render_note(note: Note) -> str:
    """Отрендерить заметку в Obsidian-совместимый Markdown.

    Результат всегда содержит YAML frontmatter. Если у заметки заполнено
    :attr:`Note.sections`, дальше идут эти разделы в заданном порядке; иначе —
    все пять секций встроенного шаблона в порядке :data:`SECTION_HEADINGS`.
    Пустые разделы остаются пустыми, но заголовки не исчезают. Файл
    заканчивается одним переводом строки.
    """
    if note.sections:
        blocks = (
            render_frontmatter(note),
            *(_render_note_section(section) for section in note.sections),
        )
    else:
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
        # Пустая строка атрибуции без таймкода (``> —``) — мусор в заметке:
        # она появляется, когда цитата пришла строкой без таймкода.
        if quote.timecode:
            lines.append(f"> — [{quote.timecode}]")
    return lines


def _render_follow_ups(items: tuple[str, ...]) -> list[str]:
    return [f"- [ ] {item}" for item in items]


def _render_note_section(section: NoteSection) -> str:
    """Отрендерить один динамический раздел с заголовком ``##``."""
    return _render_section(f"## {section.heading}", _render_section_body(section))


def _render_section_body(section: NoteSection) -> list[str]:
    """Тело динамического раздела по его виду; пункты уже нормализованы."""
    if section.kind is SectionKind.PARAGRAPH:
        return _render_paragraph(cast("tuple[str, ...]", section.items))
    if section.kind is SectionKind.TIMELINE:
        return _render_timeline(cast("tuple[TimelineEntry, ...]", section.items))
    if section.kind is SectionKind.QUOTES:
        return _render_quotes(cast("tuple[Quote, ...]", section.items))
    if section.kind is SectionKind.CHECKLIST:
        return _render_follow_ups(cast("tuple[str, ...]", section.items))
    # BULLETS и CUSTOM рендерятся одинаково — маркированным списком.
    return _render_bullets(cast("tuple[str, ...]", section.items))


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
        cleaned = one_line(_text(tag).lstrip("#"))
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return tuple(normalized)


def _normalize_items(items: tuple[str, ...]) -> tuple[str, ...]:
    normalized = []
    for item in items:
        text = one_line(_text(item))
        if text:
            normalized.append(text)
    return tuple(normalized)


def _normalize_timeline(entries: tuple[TimelineEntry, ...]) -> tuple[TimelineEntry, ...]:
    return tuple(entry for entry in entries if entry.timecode or entry.title)


def _normalize_quotes(quotes: tuple[Quote, ...]) -> tuple[Quote, ...]:
    return tuple(quote for quote in quotes if quote.text or quote.timecode)


def _normalize_note_sections(sections: object) -> tuple[NoteSection, ...]:
    """Проверить, что динамические разделы — последовательность NoteSection."""
    if isinstance(sections, (str, bytes)):
        raise TypeError("sections должен быть последовательностью NoteSection")
    try:
        raw = tuple(sections)  # type: ignore[arg-type]
    except TypeError:
        raise TypeError("sections должен быть последовательностью NoteSection") from None
    if not all(isinstance(section, NoteSection) for section in raw):
        raise TypeError("sections должен состоять из NoteSection")
    return raw


def _coerce_section_kind(value: object) -> SectionKind:
    try:
        return SectionKind(value)
    except ValueError:
        raise ValueError(f"неизвестный вид раздела: {value!r}") from None


def _normalize_section_items(
    kind: SectionKind, items: object
) -> tuple[TimelineEntry | Quote | str, ...]:
    """Нормализовать пункты раздела под его вид и выбросить пустые."""
    raw = _section_items(items)
    if kind is SectionKind.TIMELINE:
        entries = map(_timeline_item, raw)
        return tuple(entry for entry in entries if entry.timecode or entry.title)
    if kind is SectionKind.QUOTES:
        quotes = map(_quote_item, raw)
        return tuple(quote for quote in quotes if quote.text or quote.timecode)
    # Абзац, список, чеклист и пользовательский раздел состоят из строк.
    texts = map(_section_text, raw)
    return tuple(text for text in texts if text)


def _section_items(items: object) -> tuple[object, ...]:
    if isinstance(items, (str, bytes)):
        raise TypeError("пункты раздела задаются последовательностью, а не строкой")
    try:
        return tuple(items)  # type: ignore[arg-type]
    except TypeError:
        raise TypeError("пункты раздела должны быть последовательностью") from None


def _section_text(item: object) -> str:
    if not isinstance(item, str):
        raise TypeError("пункт раздела должен быть строкой")
    return one_line(item)


def _timeline_item(item: object) -> TimelineEntry:
    if isinstance(item, TimelineEntry):
        return item
    if isinstance(item, str):
        timecode, title = _split_timecode(item)
        return TimelineEntry(timecode=timecode, title=title)
    raise TypeError("пункт раздела таймкодов задаётся TimelineEntry или строкой")


def _split_timecode(text: str) -> tuple[str, str]:
    """Отделить ведущий таймкод строки пункта от его текста.

    Динамический путь получает пункты встроенных «Таймкодов» одной строкой от
    LLM (``01:23 — начало``), поэтому таймкод нужно разобрать, иначе рендер
    потеряет привычные квадратные скобки. Принимаются ``MM:SS`` и
    ``HH:MM:SS``, в том числе в скобках, с разделителем «—», «–», «-» или
    пробелом. Строка без таймкода и неверные значения (``99:99``) остаются
    текстом пункта: заметка не должна выдавать чужие числа за таймкод.
    """
    normalized = one_line(text)
    match = _TIMECODE_PREFIX_RE.match(normalized)
    if match is None:
        return "", normalized
    first = int(match.group("first"))
    second = int(match.group("second"))
    third = match.group("third")
    if third is None:
        minutes, seconds = first, second
        timecode = format_timecode(minutes * 60 + seconds)
    else:
        hours, minutes, seconds = first, second, int(third)
        timecode = format_timecode(hours * 3600 + minutes * 60 + seconds)
    if minutes > 59 or seconds > 59:
        return "", normalized
    return timecode, match.group("text").strip()


def _quote_item(item: object) -> Quote:
    if isinstance(item, Quote):
        return item
    if isinstance(item, str):
        return Quote(text=item)
    raise TypeError("пункт раздела цитат задаётся Quote или строкой")


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


__all__ = [
    "BUILTIN_SECTION_KINDS",
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
    "NoteSection",
    "Quote",
    "SectionKind",
    "TimelineEntry",
    "format_timecode",
    "note_filename",
    "render_frontmatter",
    "render_note",
    "slugify_title",
]

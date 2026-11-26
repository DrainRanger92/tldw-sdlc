"""Сервисный слой: сценарии обработки видео.

Сервисы связывают адаптеры и хранилище, не зависят от Telegram и не ходят
в сеть напрямую — внешние вызовы приходят через адаптеры. Модель заметки
и рендер Obsidian-Markdown живут в :mod:`tldw.services.note`.
"""

from tldw.services.note import (
    FALLBACK_SLUG,
    FALLBACK_TITLE,
    FRONTMATTER_KEYS,
    NOTE_STATUS,
    SECTION_HEADINGS,
    Note,
    Quote,
    TimelineEntry,
    format_timecode,
    note_filename,
    render_frontmatter,
    render_note,
    slugify_title,
)

__all__ = [
    "FALLBACK_SLUG",
    "FALLBACK_TITLE",
    "FRONTMATTER_KEYS",
    "NOTE_STATUS",
    "SECTION_HEADINGS",
    "Note",
    "Quote",
    "TimelineEntry",
    "format_timecode",
    "note_filename",
    "render_frontmatter",
    "render_note",
    "slugify_title",
]

"""Сервисный слой: сценарии обработки видео.

Сервисы связывают адаптеры и хранилище, не зависят от Telegram и не ходят
в сеть напрямую — внешние вызовы приходят через адаптеры. Модель заметки
и рендер Obsidian-Markdown живут в :mod:`tldw.services.note`, а сценарий
«видео → конспект → ``.md``» — в :mod:`tldw.services.summary`.
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
from tldw.services.summary import (
    DEFAULT_MAX_TRANSCRIPT_CHARS,
    MESSAGE_VIDEO_TOO_LONG,
    TRANSCRIPT_TRUNCATION_MARKER,
    SummaryProvider,
    SummaryResult,
    SummaryService,
    TranscriptText,
    VideoSource,
    VideoTooLongError,
    build_note,
    join_transcript,
)

__all__ = [
    "DEFAULT_MAX_TRANSCRIPT_CHARS",
    "FALLBACK_SLUG",
    "FALLBACK_TITLE",
    "FRONTMATTER_KEYS",
    "MESSAGE_VIDEO_TOO_LONG",
    "NOTE_STATUS",
    "SECTION_HEADINGS",
    "TRANSCRIPT_TRUNCATION_MARKER",
    "Note",
    "Quote",
    "SummaryProvider",
    "SummaryResult",
    "SummaryService",
    "TimelineEntry",
    "TranscriptText",
    "VideoSource",
    "VideoTooLongError",
    "build_note",
    "format_timecode",
    "join_transcript",
    "note_filename",
    "render_frontmatter",
    "render_note",
    "slugify_title",
]

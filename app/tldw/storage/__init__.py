"""Слой хранения: заметки, шаблоны, кэш транскриптов, каталог данных."""

from tldw.storage.atomic import atomic_write_text
from tldw.storage.notes import note_path, save_note
from tldw.storage.paths import ensure_data_dir
from tldw.storage.templates import (
    MESSAGE_TEMPLATE_DELETE_FAILED,
    MESSAGE_TEMPLATE_STORAGE_FAILED,
    SCHEMA_VERSION,
    TemplateStorageError,
    TemplateStore,
)

__all__ = [
    "MESSAGE_TEMPLATE_DELETE_FAILED",
    "MESSAGE_TEMPLATE_STORAGE_FAILED",
    "SCHEMA_VERSION",
    "TemplateStorageError",
    "TemplateStore",
    "atomic_write_text",
    "ensure_data_dir",
    "note_path",
    "save_note",
]

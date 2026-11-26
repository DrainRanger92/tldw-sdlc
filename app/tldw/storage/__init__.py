"""Слой хранения: файлы заметок, кэш транскриптов, каталог данных."""

from tldw.storage.notes import note_path, save_note
from tldw.storage.paths import ensure_data_dir

__all__ = ["ensure_data_dir", "note_path", "save_note"]

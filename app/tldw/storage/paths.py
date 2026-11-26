"""Работа с каталогом данных приложения (``app/data``).

Каталог содержит готовые заметки, кэш транскриптов и скачанные файлы;
он исключён из git. Все пути приходят из конфига, а не задаются в коде.
"""

from pathlib import Path


def ensure_data_dir(path: Path) -> Path:
    """Создать каталог данных (включая родителей) и вернуть его."""
    path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = ["ensure_data_dir"]

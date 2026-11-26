"""Запись готовых заметок в каталог данных.

Заметка пишется атомарно: сначала во временный файл рядом с целевым, затем
``os.replace``. Если процесс упадёт посреди записи, в каталоге не останется
наполовину записанного ``.md`` — это второй рубеж после проверки порога
длительности в сервисе конспекта (частичной заметки быть не должно).

Имя файла приходит из :func:`tldw.services.note.note_filename`, но слой
хранения всё равно проверяет его: только базовое имя с расширением ``.md``,
без каталогов и ``..``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from tldw.storage.paths import ensure_data_dir


def note_path(data_dir: Path, filename: str) -> Path:
    """Проверить имя заметки и вернуть путь внутри каталога данных.

    :raises ValueError: если имя пустое, содержит каталоги, не заканчивается
        на ``.md`` или равно ``.``/``..``.
    """
    name = filename.strip()
    if not name or name in {".", ".."} or name != Path(name).name:
        raise ValueError("Имя файла заметки должно быть именем файла без каталогов")
    if Path(name).suffix.lower() != ".md":
        raise ValueError("Заметка должна иметь расширение .md")
    return Path(data_dir) / name


def save_note(data_dir: Path, filename: str, markdown: str) -> Path:
    """Атомарно сохранить готовую заметку и вернуть путь к файлу.

    :param data_dir: каталог данных из конфига; создаётся при необходимости.
    :param filename: имя файла с расширением ``.md`` без каталогов.
    :param markdown: полный текст заметки по шаблону T-002.
    :raises ValueError: если имя или текст заметки некорректны.
    :raises OSError: если каталог или файл нельзя создать.
    """
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("Текст заметки не может быть пустым")
    path = note_path(data_dir, filename)
    ensure_data_dir(path.parent)

    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.stem}-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(markdown)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return path


__all__ = ["note_path", "save_note"]

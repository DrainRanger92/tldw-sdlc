"""Атомарная запись текстовых файлов в каталог данных.

Приём один для заметок и шаблонов: сначала временный файл **рядом** с целевым,
затем :func:`os.replace`. Если процесс упадёт посреди записи или запись не
удастся, в каталоге не останется наполовину записанного файла, а прежний файл
не пострадает. Временный файл удаляется и при ошибке.

Помощник не переводит ошибки в доменные типы: вызывающий слой решает, что
показать пользователю (``OSError`` у заметок, ``TemplateStorageError`` у
шаблонов).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from tldw.storage.paths import ensure_data_dir


def atomic_write_text(path: Path, text: str) -> None:
    """Записать ``text`` в ``path`` атомарно, создав каталог при необходимости.

    :param path: целевой файл; временный файл создаётся в его каталоге.
    :param text: полный текст в UTF-8 с переводами строк ``\\n``.
    :raises OSError: если каталог, временный файл или замена недоступны.
    """
    target = Path(path)
    ensure_data_dir(target.parent)

    descriptor, temp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.stem}-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


__all__ = ["atomic_write_text"]

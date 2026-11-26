"""Офлайновые тесты записи заметок в каталог данных.

Проверяют атомарную запись ``.md``, создание каталога, замену существующего
файла без временных остатков и защиту от имён с каталогами/другим расширением.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tldw.storage import note_path, save_note

MARKDOWN = """---
title: "Заметка"
status: processed
---

## TL;DR

Текст заметки.
"""


def test_note_path_accepts_bare_md_filename(tmp_path: Path) -> None:
    assert note_path(tmp_path, "заметка.md") == tmp_path / "заметка.md"
    assert note_path(tmp_path, "заметка.MD") == tmp_path / "заметка.MD"


@pytest.mark.parametrize(
    "filename",
    [
        "",
        "   ",
        ".",
        "..",
        "../evil.md",
        "/tmp/evil.md",
        "sub/note.md",
        "note.txt",
        "note",
    ],
)
def test_note_path_rejects_unsafe_filenames(tmp_path: Path, filename: str) -> None:
    with pytest.raises(ValueError):
        note_path(tmp_path, filename)


def test_save_note_creates_data_dir_and_writes_markdown(tmp_path: Path) -> None:
    data_dir = tmp_path / "nested" / "data"

    path = save_note(data_dir, "заметка.md", MARKDOWN)

    assert path == data_dir / "заметка.md"
    assert path.read_text(encoding="utf-8") == MARKDOWN
    assert list(data_dir.iterdir()) == [path]


def test_save_note_replaces_existing_file_without_temp_leftovers(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    path = save_note(data_dir, "заметка.md", "старый текст\n")

    same_path = save_note(data_dir, "заметка.md", MARKDOWN)

    assert same_path == path
    assert path.read_text(encoding="utf-8") == MARKDOWN
    assert [item.name for item in data_dir.iterdir()] == ["заметка.md"]


def test_save_note_rejects_empty_markdown(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_note(tmp_path, "заметка.md", "   \n")

    assert list(tmp_path.iterdir()) == []

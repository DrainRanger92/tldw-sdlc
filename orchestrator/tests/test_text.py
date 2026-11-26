import pytest

from aidevteam.tasks import create_draft, load_drafts
from aidevteam.text import clean_text


def test_clean_text_passthrough():
    assert clean_text("каркас приложения") == "каркас приложения"


def test_clean_text_restores_utf8_from_surrogates():
    # так выглядит кириллица, прочитанная stdin'ом в локали POSIX
    broken = "каркас".encode("utf-8").decode("ascii", "surrogateescape")
    assert any("\ud800" <= ch <= "\udfff" for ch in broken)
    assert clean_text(broken) == "каркас"


def test_clean_text_replaces_broken_bytes():
    assert clean_text("\udcff") == "\ufffd"


def test_create_draft_writes_valid_utf8_from_surrogate_input(repo):
    title = "\udcd0\udcba\udcd0\udcb0\udcd1\udc80\udcd0\udcba\udcd0\udcb0\udcd1\udc81"
    draft = create_draft(repo, title, "описание")
    text = draft.path.read_text(encoding="utf-8")
    assert "title: каркас" in text
    assert draft.title == "каркас"
    assert [d.id for d in load_drafts(repo)] == [draft.id]


def test_create_draft_leaves_no_tmp_file(repo):
    draft = create_draft(repo, "заголовок")
    assert draft.path.exists()
    assert not list(draft.path.parent.glob("*.tmp"))


def test_create_draft_failure_leaves_no_empty_draft(repo, monkeypatch):
    import pathlib

    real_replace = pathlib.Path.replace

    def boom(self, target):
        raise OSError("диск переполнен")

    monkeypatch.setattr(pathlib.Path, "replace", boom)
    with pytest.raises(OSError):
        create_draft(repo, "не запишется")
    monkeypatch.setattr(pathlib.Path, "replace", real_replace)
    assert not [p for p in (repo / "specs" / "inbox").iterdir() if p.suffix == ".md"]

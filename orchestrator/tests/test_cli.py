import io

from aidevteam.cli import _force_utf8_stdio, main


def test_version(capsys):
    assert main(["--version"]) == 0
    assert "0.1.0" in capsys.readouterr().out


def test_help_lists_commands(capsys):
    assert main(["help"]) == 0
    out = capsys.readouterr().out
    for cmd in ("run", "next", "status", "list", "add", "refine", "watch", "log", "app"):
        assert cmd in out


def test_unknown_command_suggests(capsys):
    assert main(["watchh"]) == 1
    out = capsys.readouterr().out
    assert "Неизвестная команда" in out
    assert "watch" in out


def test_force_utf8_stdio_replaces_bad_bytes():
    stream = io.TextIOWrapper(
        io.BytesIO(b"\xd1A\n"), encoding="ascii", errors="strict"
    )
    _force_utf8_stdio([stream])
    assert stream.encoding == "utf-8"
    assert stream.readline() == "\ufffdA\n"


def test_force_utf8_stdio_keeps_valid_cyrillic():
    stream = io.TextIOWrapper(io.BytesIO("закладываем\n".encode("utf-8")), encoding="ascii")
    _force_utf8_stdio([stream])
    assert stream.readline() == "закладываем\n"

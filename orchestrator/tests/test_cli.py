from aidevteam.cli import main


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

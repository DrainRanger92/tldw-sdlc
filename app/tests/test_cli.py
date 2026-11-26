"""Тесты CLI: сборка приложения, коды возврата и понятные ошибки конфига."""

from __future__ import annotations

from pathlib import Path

import pytest

from tldw.bootstrap import Application
from tldw.cli import EXIT_CONFIG_ERROR, EXIT_FAILURE, EXIT_OK, main


def _empty_env_file(tmp_path: Path) -> Path:
    path = tmp_path / "test.env"
    path.write_text("", encoding="utf-8")
    return path


def test_check_config_builds_application_with_fake_env(
    fake_env: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        ["--check-config", "--env-file", str(_empty_env_file(tmp_path))],
        environ=fake_env,
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert "Конфигурация в порядке" in captured.out
    assert "test-model" in captured.out
    assert "Ошибка" not in captured.err
    assert (tmp_path / "data").is_dir()


def test_missing_bot_token_exits_with_clear_message(
    fake_env: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {name: value for name, value in fake_env.items() if name != "TELEGRAM_BOT_TOKEN"}

    exit_code = main(
        ["--check-config", "--env-file", str(_empty_env_file(tmp_path))],
        environ=env,
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_CONFIG_ERROR
    assert "TELEGRAM_BOT_TOKEN" in captured.err
    assert "app/.env.example" in captured.err
    assert "Traceback" not in captured.err


def test_missing_llm_key_exits_with_clear_message(
    fake_env: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {name: value for name, value in fake_env.items() if name != "LLM_API_KEY"}

    exit_code = main(
        ["--check-config", "--env-file", str(_empty_env_file(tmp_path))],
        environ=env,
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_CONFIG_ERROR
    assert "LLM_API_KEY" in captured.err
    # Токен бота в сообщении об ошибке не раскрывается.
    assert fake_env["TELEGRAM_BOT_TOKEN"] not in captured.err


def test_env_file_supplies_values_when_env_is_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / "app.env"
    env_file.write_text(
        "\n".join(
            [
                "TELEGRAM_BOT_TOKEN=123456789:from-file",
                "LLM_API_KEY=key-from-file",
                "LLM_MODEL=model-from-file",
                f"DATA_DIR={tmp_path / 'data'}",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(["--check-config", "--env-file", str(env_file)], environ={})

    assert exit_code == EXIT_OK
    assert "model-from-file" in capsys.readouterr().out


def test_real_env_wins_over_env_file(
    fake_env: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / "app.env"
    env_file.write_text(
        "\n".join(
            [
                "TELEGRAM_BOT_TOKEN=broken-token",
                "LLM_API_KEY=file-key",
                "LLM_MODEL=file-model",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(["--check-config", "--env-file", str(env_file)], environ=fake_env)

    assert exit_code == EXIT_OK
    assert "test-model" in capsys.readouterr().out


def test_explicit_missing_env_file_is_a_config_error(
    fake_env: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        ["--check-config", "--env-file", str(tmp_path / "nope.env")],
        environ=fake_env,
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_CONFIG_ERROR
    assert "не найден" in captured.err


def test_main_builds_application_for_startup(fake_env: dict[str, str], tmp_path: Path) -> None:
    started: list[Application] = []

    exit_code = main(
        ["--env-file", str(_empty_env_file(tmp_path))],
        environ=fake_env,
        run_application=started.append,
    )

    assert exit_code == EXIT_OK
    assert len(started) == 1
    application = started[0]
    assert application.settings.llm_model == "test-model"
    assert application.bot.id == 123456789
    assert application.settings.data_dir.is_dir()


def test_build_failure_is_reported_without_traceback(
    fake_env: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_build(settings):  # noqa: ANN001, ARG001 - сигнатура-заглушка
        raise RuntimeError("boom")

    monkeypatch.setattr("tldw.cli.build_application", broken_build)

    exit_code = main(
        ["--check-config", "--env-file", str(_empty_env_file(tmp_path))],
        environ=fake_env,
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_FAILURE
    assert "Не удалось запустить TLDW" in captured.err
    assert "RuntimeError" in captured.err
    assert "boom" in captured.err

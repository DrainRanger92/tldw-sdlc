"""Тесты CLI: сборка приложения, коды возврата и понятные ошибки конфига."""

from __future__ import annotations

from pathlib import Path

import pytest
from support import empty_env_file

from tldw.bootstrap import Application
from tldw.cli import EXIT_CONFIG_ERROR, EXIT_FAILURE, EXIT_OK, main


def test_check_config_builds_application_with_fake_env(
    fake_env: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        ["--check-config", "--env-file", str(empty_env_file(tmp_path))],
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
        ["--check-config", "--env-file", str(empty_env_file(tmp_path))],
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
        ["--check-config", "--env-file", str(empty_env_file(tmp_path))],
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


def test_invalid_allowlist_stops_startup_with_clear_error(
    fake_env: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[Application] = []
    env = {**fake_env, "TELEGRAM_ALLOWED_USER_IDS": "alice, bob"}

    exit_code = main(
        ["--env-file", str(empty_env_file(tmp_path))],
        environ=env,
        run_application=started.append,
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_CONFIG_ERROR
    assert "TELEGRAM_ALLOWED_USER_IDS" in captured.err
    assert "user ID" in captured.err
    assert "Traceback" not in captured.err
    assert started == [], "бот не должен запускаться с некорректным allowlist"


def test_check_config_reports_allowlist_size(
    fake_env: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {**fake_env, "TELEGRAM_ALLOWED_USER_IDS": "1001, 1002"}

    exit_code = main(
        ["--check-config", "--env-file", str(empty_env_file(tmp_path))],
        environ=env,
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert "доступ: 2 ID" in captured.out
    assert "1001" not in captured.out, "ID не должны попадать в вывод"


def test_main_builds_application_for_startup(fake_env: dict[str, str], tmp_path: Path) -> None:
    started: list[Application] = []

    exit_code = main(
        ["--env-file", str(empty_env_file(tmp_path))],
        environ=fake_env,
        run_application=started.append,
    )

    assert exit_code == EXIT_OK
    assert len(started) == 1
    application = started[0]
    assert application.settings.llm_model == "test-model"
    assert application.bot.id == 123456789
    assert application.settings.data_dir.is_dir()


def test_default_run_path_closes_application(
    fake_env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Штатный ``application.run()`` тоже закрывает приложение (P3 T-016).

    Раньше ``close()`` вызывался только при подменённом runner, поэтому в
    проде фоновые задачи не отменялись, а сессия Telegram не закрывалась.
    """
    started: list[Application] = []
    closed: list[Application] = []
    real_close = Application.close

    def fake_run(self: Application) -> None:
        started.append(self)

    async def recording_close(self: Application) -> None:
        closed.append(self)
        await real_close(self)

    monkeypatch.setattr(Application, "run", fake_run)
    monkeypatch.setattr(Application, "close", recording_close)

    exit_code = main(["--env-file", str(empty_env_file(tmp_path))], environ=fake_env)

    assert exit_code == EXIT_OK
    assert len(started) == 1, "должен использоваться штатный run(), а не подмена"
    assert closed == started, "close() обязан вызываться после run()"


def test_close_failure_on_run_path_does_not_escape_main(
    fake_env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Сбой закрытия на пути остановки гасится и не меняет код возврата.

    ``run_polling(close_bot_session=True)`` уже закрыл сессию, а ``asyncio.run``
    даёт новый loop — повторное закрытие может упасть. Раньше исключение
    вылетало из ``main()`` вместо кода возврата (REVIEW-2 T-016).
    """

    async def broken_close(self: Application) -> None:
        raise RuntimeError("session is closed")

    monkeypatch.setattr(Application, "run", lambda self: None)
    monkeypatch.setattr(Application, "close", broken_close)

    exit_code = main(["--env-file", str(empty_env_file(tmp_path))], environ=fake_env)

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert "не удалось закрыть приложение" in captured.err
    assert "Traceback" not in captured.err


def test_check_config_reports_close_failure_without_secret(
    fake_env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Сбой закрытия в ``--check-config`` даёт код возврата, а не трейсбек."""

    async def broken_close(self: Application) -> None:
        raise RuntimeError(f"session {fake_env['TELEGRAM_BOT_TOKEN']} is closed")

    monkeypatch.setattr(Application, "close", broken_close)

    exit_code = main(
        ["--check-config", "--env-file", str(empty_env_file(tmp_path))],
        environ=fake_env,
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_FAILURE
    assert "Не удалось корректно остановить TLDW" in captured.err
    assert "Traceback" not in captured.err
    assert fake_env["TELEGRAM_BOT_TOKEN"] not in captured.err, "токен не должен утекать"


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
        ["--check-config", "--env-file", str(empty_env_file(tmp_path))],
        environ=fake_env,
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_FAILURE
    assert "Не удалось запустить TLDW" in captured.err
    assert "RuntimeError" in captured.err
    assert "boom" in captured.err

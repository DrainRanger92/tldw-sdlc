"""Сквозные проверки точки входа ``app/main.py`` в отдельном процессе.

Сеть не используется: ``--check-config`` только читает env, собирает aiogram-
приложение и выходит. Фиктивные секреты передаются через окружение процесса.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tldw.config import ALL_ENV_VARS

APP_DIR = Path(__file__).resolve().parents[1]


def test_main_module_delegates_to_cli() -> None:
    import main as entrypoint
    from tldw.cli import main as cli_main

    assert entrypoint.main is cli_main


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in ALL_ENV_VARS:
        env.pop(name, None)
    return env


def _run_main(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "main.py", *args],
        cwd=APP_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _empty_env_file(tmp_path: Path) -> Path:
    path = tmp_path / "test.env"
    path.write_text("", encoding="utf-8")
    return path


def test_entrypoint_checks_config_with_fake_env(tmp_path: Path) -> None:
    env = _clean_env()
    env.update(
        {
            "TELEGRAM_BOT_TOKEN": "123456789:TEST-entrypoint-token",
            "LLM_API_KEY": "test-entrypoint-key",
            "LLM_MODEL": "test-model",
            "DATA_DIR": str(tmp_path / "data"),
        }
    )

    result = _run_main(
        "--check-config",
        "--env-file",
        str(_empty_env_file(tmp_path)),
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "Конфигурация в порядке" in result.stdout
    assert (tmp_path / "data").is_dir()


def test_entrypoint_without_env_exits_with_clear_message(tmp_path: Path) -> None:
    result = _run_main(
        "--check-config",
        "--env-file",
        str(_empty_env_file(tmp_path)),
        env=_clean_env(),
    )

    assert result.returncode == 2
    assert "TELEGRAM_BOT_TOKEN" in result.stderr
    assert "LLM_API_KEY" in result.stderr
    assert "app/.env.example" in result.stderr
    assert "Traceback" not in result.stderr

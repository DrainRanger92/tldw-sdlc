"""Проверки метаданных пакета, примера env и игнора каталога данных."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from tldw.config import ALL_ENV_VARS, read_env_file

APP_DIR = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((APP_DIR / "pyproject.toml").read_text(encoding="utf-8"))


def _dependency_names(dependencies: list[str]) -> set[str]:
    return {
        re.split(r"[<>=!~\[;]", dependency, maxsplit=1)[0].strip().lower()
        for dependency in dependencies
    }


def test_requires_python_311_or_newer() -> None:
    requires_python = _pyproject()["project"]["requires-python"]

    assert requires_python.startswith(">=3.11")


def test_runtime_dependencies_are_declared() -> None:
    project = _pyproject()["project"]

    names = _dependency_names(project["dependencies"])
    assert {"aiogram", "httpx", "youtube-transcript-api", "yt-dlp"} <= names

    aiogram = next(dep for dep in project["dependencies"] if dep.lower().startswith("aiogram"))
    assert aiogram.startswith("aiogram>=3"), "нужен aiogram 3.x"
    assert "<4" in aiogram, "мажорная версия aiogram должна быть зафиксирована"


def test_dev_dependencies_include_pytest_and_ruff() -> None:
    dev = _pyproject()["project"]["optional-dependencies"]["dev"]

    assert {"pytest", "ruff"} <= _dependency_names(dev)


def test_ruff_configuration_is_present() -> None:
    pyproject = _pyproject()

    assert pyproject["tool"]["ruff"]["target-version"] == "py311"
    assert pyproject["tool"]["ruff"]["line-length"] >= 88
    assert pyproject["tool"]["ruff"]["lint"]["select"]


def test_env_example_lists_every_app_variable_without_secrets() -> None:
    values = read_env_file(APP_DIR / ".env.example")

    assert set(values) == set(ALL_ENV_VARS)
    assert values["TELEGRAM_BOT_TOKEN"] == ""
    assert values["LLM_API_KEY"] == ""
    assert values["LLM_MODEL"] == ""
    # Дефолты продублированы в примере, чтобы их было видно без чтения кода.
    assert values["VIDEO_MAX_DURATION_SECONDS"] == "7200"
    assert values["LOG_LEVEL"] == "INFO"


def test_data_directory_is_gitignored() -> None:
    app_ignore = {
        line.strip().rstrip("/")
        for line in (APP_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert "data" in app_ignore

    root_ignore_path = APP_DIR.parent / ".gitignore"
    if root_ignore_path.exists():
        root_ignore = root_ignore_path.read_text(encoding="utf-8").splitlines()
        assert "app/data/" in {line.strip() for line in root_ignore}

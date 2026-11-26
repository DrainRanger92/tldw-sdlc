"""Общие фикстуры тестов.

Тесты полностью офлайновые: токен бота и ключ LLM — фиктивные строки,
никакие адаптеры не вызываются, каталог данных живёт в ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support import FAKE_BOT_TOKEN

APP_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def fake_env(tmp_path: Path) -> dict[str, str]:
    """Минимальный валидный env с фиктивными секретами."""
    return {
        "TELEGRAM_BOT_TOKEN": FAKE_BOT_TOKEN,
        "LLM_API_KEY": "test-llm-key-not-a-secret",
        "LLM_MODEL": "test-model",
        "DATA_DIR": str(tmp_path / "data"),
    }


@pytest.fixture
def app_dir() -> Path:
    """Каталог приложения ``app/``."""
    return APP_DIR

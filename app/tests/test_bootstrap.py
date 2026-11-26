"""Тесты сборки приложения и слоёв пакета (без сети и токенов)."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest
from aiogram import Bot, Dispatcher

from tldw.bootstrap import Application, build_application
from tldw.config import load_settings
from tldw.handlers import create_router
from tldw.storage import ensure_data_dir


@pytest.mark.parametrize("layer", ["handlers", "services", "adapters", "storage"])
def test_layers_are_importable_packages(layer: str) -> None:
    module = importlib.import_module(f"tldw.{layer}")

    assert Path(module.__file__).name == "__init__.py"
    assert module.__doc__, f"у слоя {layer} должно быть описание"


def test_root_router_is_named_tldw() -> None:
    assert create_router().name == "tldw"


def test_ensure_data_dir_creates_nested_directory(tmp_path: Path) -> None:
    target = tmp_path / "notes" / "cache"

    assert ensure_data_dir(target) == target
    assert target.is_dir()


def test_build_application_from_env(fake_env: dict[str, str]) -> None:
    settings = load_settings(fake_env)

    application = build_application(settings)
    try:
        assert isinstance(application, Application)
        assert isinstance(application.bot, Bot)
        assert isinstance(application.dispatcher, Dispatcher)
        assert application.bot.id == 123456789
        assert application.settings is settings
        assert "tldw" in {router.name for router in application.dispatcher.sub_routers}
        assert settings.data_dir.is_dir()
    finally:
        asyncio.run(application.close())


def test_close_is_safe_to_call_twice(fake_env: dict[str, str]) -> None:
    application = build_application(load_settings(fake_env))

    asyncio.run(application.close())
    asyncio.run(application.close())

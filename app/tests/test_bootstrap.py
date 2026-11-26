"""Тесты сборки приложения и слоёв пакета (без сети и токенов)."""

from __future__ import annotations

import asyncio
import importlib
import logging
from pathlib import Path

import pytest
from aiogram import Bot, Dispatcher, Router

from tldw.bootstrap import Application, build_application
from tldw.config import load_settings
from tldw.handlers import create_router
from tldw.storage import ensure_data_dir


@pytest.mark.parametrize("layer", ["handlers", "services", "adapters", "storage"])
def test_layers_are_importable_packages(layer: str) -> None:
    module = importlib.import_module(f"tldw.{layer}")

    assert Path(module.__file__).name == "__init__.py"
    assert module.__doc__, f"у слоя {layer} должно быть описание"


class _UnusedService:
    """Заглушка сервиса: router собирается без обработки ссылок."""

    def process(self, url: str) -> None:
        raise AssertionError("сервис не должен вызываться в тесте сборки")


def test_root_router_is_named_tldw() -> None:
    assert create_router(_UnusedService()).name == "tldw"


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


def test_build_application_uses_injected_summary_service(
    fake_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_service(settings: object) -> None:
        raise AssertionError("реальные адаптеры не должны собираться")

    monkeypatch.setattr("tldw.bootstrap.build_summary_service", broken_service)

    application = build_application(load_settings(fake_env), summary_service=_UnusedService())
    try:
        assert "tldw" in {router.name for router in application.dispatcher.sub_routers}
    finally:
        asyncio.run(application.close())


def test_close_is_safe_to_call_twice(fake_env: dict[str, str]) -> None:
    application = build_application(load_settings(fake_env))

    asyncio.run(application.close())
    asyncio.run(application.close())


def test_build_application_forwards_allowlist_to_router(
    fake_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, tuple[int, ...]] = {}

    def recording_create_router(
        service: object,
        *,
        allowed_user_ids: tuple[int, ...] = (),
    ) -> Router:
        captured["allowed_user_ids"] = allowed_user_ids
        return Router(name="tldw")

    monkeypatch.setattr("tldw.bootstrap.create_router", recording_create_router)
    settings = load_settings({**fake_env, "TELEGRAM_ALLOWED_USER_IDS": "42, 1001"})

    application = build_application(settings, summary_service=_UnusedService())
    try:
        assert captured["allowed_user_ids"] == (42, 1001)
    finally:
        asyncio.run(application.close())


def test_empty_allowlist_logs_open_access_warning(
    fake_env: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = load_settings(fake_env)
    assert settings.allowed_user_ids == ()

    with caplog.at_level(logging.WARNING, logger="tldw.access"):
        application = build_application(settings)
    try:
        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    finally:
        asyncio.run(application.close())

    assert warnings, "пустой allowlist должен давать предупреждение при старте"
    assert "TELEGRAM_ALLOWED_USER_IDS" in warnings[0].getMessage()
    assert "всем" in warnings[0].getMessage()


def test_configured_allowlist_logs_no_warning(
    fake_env: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = load_settings({**fake_env, "TELEGRAM_ALLOWED_USER_IDS": "1001"})

    with caplog.at_level(logging.WARNING, logger="tldw.access"):
        application = build_application(settings, summary_service=_UnusedService())
    try:
        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    finally:
        asyncio.run(application.close())

    assert warnings == []

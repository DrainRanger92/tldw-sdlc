"""Тесты сборки приложения и слоёв пакета (без сети и токенов)."""

from __future__ import annotations

import asyncio
import importlib
import logging
from pathlib import Path

import pytest
from aiogram import Bot, Dispatcher, Router

from tldw.bootstrap import (
    Application,
    build_application,
    build_speech_transcriber,
)
from tldw.config import load_settings
from tldw.handlers import MESSAGE_DONE, create_router
from tldw.storage import ensure_data_dir


@pytest.mark.parametrize("layer", ["handlers", "services", "adapters", "storage"])
def test_layers_are_importable_packages(layer: str) -> None:
    module = importlib.import_module(f"tldw.{layer}")

    assert Path(module.__file__).name == "__init__.py"
    assert module.__doc__, f"у слоя {layer} должно быть описание"


class _UnusedService:
    """Заглушка сервиса: router собирается без обработки ссылок."""

    def process(self, url: str, *, template: object | None = None) -> None:
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


def test_build_speech_transcriber_is_unconfigured_without_optional_env(
    fake_env: dict[str, str],
) -> None:
    """Без ``SPEECH_*`` адаптер создаётся, но голосовой ввод недоступен."""
    settings = load_settings(fake_env)

    transcriber = build_speech_transcriber(settings)

    assert transcriber.is_configured is False


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"SPEECH_API_KEY": "test-speech-key-not-a-secret"},
        {"SPEECH_MODEL": "test-whisper-model"},
    ],
    ids=["empty", "key-only", "model-only"],
)
def test_build_speech_transcriber_requires_both_key_and_model(
    fake_env: dict[str, str],
    extra: dict[str, str],
) -> None:
    """Одного ключа или одной модели мало: голосовой ввод остаётся выключен."""
    settings = load_settings({**fake_env, **extra})

    assert build_speech_transcriber(settings).is_configured is False


def test_build_speech_transcriber_takes_key_and_model_from_env(
    fake_env: dict[str, str],
) -> None:
    """Ключ и модель приходят из необязательных env и не утекают в repr."""
    key = "test-speech-key-not-a-secret"
    settings = load_settings(
        {**fake_env, "SPEECH_API_KEY": key, "SPEECH_MODEL": "test-whisper-model"}
    )

    transcriber = build_speech_transcriber(settings)

    assert transcriber.is_configured is True
    assert key not in repr(transcriber)


def test_close_is_safe_to_call_twice(fake_env: dict[str, str]) -> None:
    application = build_application(load_settings(fake_env))

    asyncio.run(application.close())
    asyncio.run(application.close())


def test_build_application_forwards_allowlist_to_router(
    fake_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def recording_create_router(
        service: object,
        *,
        allowed_user_ids: tuple[int, ...] = (),
        template_service: object | None = None,
        template_parser: object | None = None,
        speech_transcriber: object | None = None,
    ) -> Router:
        captured["allowed_user_ids"] = allowed_user_ids
        captured["service"] = service
        captured["template_service"] = template_service
        captured["template_parser"] = template_parser
        captured["speech_transcriber"] = speech_transcriber
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
        warnings = [
            record
            for record in caplog.records
            if record.name == "tldw.access" and record.levelno == logging.WARNING
        ]
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
        warnings = [
            record
            for record in caplog.records
            if record.name == "tldw.access" and record.levelno == logging.WARNING
        ]
    finally:
        asyncio.run(application.close())

    assert warnings == []


# --- T-016: порядок сборки и отмена фоновых задач при остановке -------------


def test_data_dir_is_created_before_bot(
    fake_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если каталог данных недоступен, Bot ещё не создан (закрывать нечего)."""
    created: list[object] = []

    def broken_ensure_data_dir(path: Path) -> Path:
        raise OSError("каталог данных недоступен")

    def recording_bot(**kwargs: object) -> object:
        created.append(kwargs)
        raise AssertionError("Bot не должен создаваться после ошибки каталога")

    monkeypatch.setattr("tldw.bootstrap.ensure_data_dir", broken_ensure_data_dir)
    monkeypatch.setattr("tldw.bootstrap.Bot", recording_bot)

    with pytest.raises(OSError, match="каталог данных недоступен"):
        build_application(load_settings(fake_env))

    assert created == []


class _BlockingService:
    """Сервис, который висит в рабочем потоке до release: имитация долгой LLM."""

    def __init__(self, result: object, started: object, release: object) -> None:
        self.result = result
        self.started = started
        self.release = release
        self.calls: list[str] = []

    def process(self, url: str, *, template: object | None = None) -> object:
        self.calls.append(url)
        self.started.set()  # type: ignore[attr-defined]
        self.release.wait(5)  # type: ignore[attr-defined]
        return self.result


def test_application_close_cancels_background_tasks(
    fake_env: dict[str, str],
    tmp_path: Path,
) -> None:
    """Остановка отменяет обработку и не пишет «готово» в закрытую сессию."""
    import threading

    from aiogram.methods import SendMessage
    from support import VIDEO_URL, app_harness, make_result

    started = threading.Event()
    release = threading.Event()
    service = _BlockingService(make_result(tmp_path), started, release)

    async def scenario() -> list[str]:
        settings = load_settings(fake_env)
        async with app_harness(settings, summary_service=service) as application:
            await application.send(VIDEO_URL)
            assert await asyncio.to_thread(started.wait, 5), "обработка не стартовала"
            await application.application.close()
            release.set()
            await asyncio.sleep(0.05)
            return [
                request.text
                for request in application.requests
                if isinstance(request, SendMessage)
            ]

    texts = asyncio.run(scenario())

    assert MESSAGE_DONE not in texts, "«готово» не должно уходить после закрытия"
    assert service.calls == [VIDEO_URL]

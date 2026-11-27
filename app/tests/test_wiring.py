"""Тесты проводки T-008: шаблон из Telegram-сценария доходит до генерации.

Сеть и токены не нужны. Проверяются три уровня проводки:

* :func:`tldw.handlers.router.create_router` с зависимостями шаблона
  регистрирует обработчик шаблона **раньше** обработчика ссылок и проводит
  ``template_service`` в обработчик ссылки — активный шаблон отправителя
  доходит до сервиса конспекта, а чужой шаблон к другому пользователю нет;
* старые вызовы ``LinkHandler(service)`` и ``create_router(service)`` остаются
  валидными: без зависимостей шаблона поведение прежнее;
* :func:`tldw.bootstrap.build_application` собирает **один** ``LLMClient``,
  ``TemplateStore`` в ``DATA_DIR`` и ``TemplateService`` без сетевых вызовов и
  передаёт их в роутер.

Фейки — только на внешних границах (сессия Telegram, разбор описания) и на
сервисе конспекта, который здесь играет роль записывающего наблюдателя.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.methods import SendMessage
from aiogram.types import Update
from support import (
    FAKE_BOT_TOKEN,
    VIDEO_URL,
    FakeSession,
    FakeSummarizer,
    FakeTemplateParser,
    FakeTemplateRepository,
    FakeVideoSource,
    make_result,
    make_update,
)

from tldw.bootstrap import (
    build_application,
    build_summary_service,
    build_template_service,
)
from tldw.config import load_settings
from tldw.handlers import MESSAGE_ACCEPTED, LinkHandler, create_router
from tldw.handlers.template import TemplateHandler
from tldw.services.summary import SummaryResult, SummaryService
from tldw.services.template import (
    ActiveTemplate,
    NoteTemplate,
    TemplateOrigin,
    TemplateService,
)
from tldw.storage import TemplateStore

#: Пользовательский шаблон проводки: разделы в порядке применения и тон.
WIRED_TEMPLATE = NoteTemplate(sections=("Коротко", "Что попробовать"), tone="по делу")


class UnusedSummaryService:
    """Заглушка сервиса конспекта: обработка ссылки не ожидается."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def process(
        self,
        url: str,
        *,
        template: ActiveTemplate | None = None,
    ) -> SummaryResult:
        self.calls.append(url)
        raise AssertionError("сервис не должен вызываться в этом тесте")


@dataclass
class RecordingSummaryService:
    """Сервис конспекта, записывающий снимок шаблона, дошедший от обработчика."""

    result: SummaryResult | None = None
    calls: list[tuple[str, ActiveTemplate | None]] = field(default_factory=list)

    def process(
        self,
        url: str,
        *,
        template: ActiveTemplate | None = None,
    ) -> SummaryResult:
        self.calls.append((url, template))
        assert self.result is not None, "тест не задал результат сервиса"
        return self.result


def registered_link_handler(router: Router) -> LinkHandler:
    """Найти созданный ``create_router`` обработчик ссылки среди обработчиков."""
    for handler in reversed(router.message.handlers):
        owner = getattr(handler.callback, "__self__", None)
        if isinstance(owner, LinkHandler):
            return owner
    raise AssertionError("create_router не зарегистрировал обработчик ссылки")


def registered_owners(router: Router) -> list[type]:
    """Типы владельцев message-обработчиков в порядке регистрации."""
    return [
        type(getattr(handler.callback, "__self__", None))
        for handler in router.message.handlers
    ]


def saved_templates() -> tuple[TemplateService, FakeTemplateRepository]:
    """Сервис шаблонов с шаблоном, сохранённым для пользователя 42."""
    repository = FakeTemplateRepository()
    repository.records[42] = (WIRED_TEMPLATE.sections, WIRED_TEMPLATE.tone)
    return TemplateService(repository), repository


def run_updates(router: Router, updates: list[Update]) -> list[Any]:
    """Прогнать апдейты через настоящий диспетчер и дождаться фоновых задач."""
    session = FakeSession()
    bot = Bot(token=FAKE_BOT_TOKEN, session=session)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    handler = registered_link_handler(router)

    async def scenario() -> None:
        try:
            for update in updates:
                await dispatcher.feed_update(bot, update)
                await handler.wait_idle()
        finally:
            await bot.session.close()

    asyncio.run(scenario())
    return session.requests


def test_router_registers_template_handler_before_link_handler() -> None:
    """С новыми зависимостями шаблон подключается раньше ссылок."""
    templates, _ = saved_templates()
    router = create_router(
        UnusedSummaryService(),
        template_service=templates,
        template_parser=FakeTemplateParser(),
    )

    assert registered_owners(router) == [
        TemplateHandler,
        TemplateHandler,
        TemplateHandler,
        TemplateHandler,
        LinkHandler,
    ]
    assert len(router.callback_query.handlers) == 1


def registered_template_methods(router: Router) -> list[str]:
    """Имена методов ``TemplateHandler`` в порядке регистрации."""
    return [
        getattr(handler.callback, "__name__", "")
        for handler in router.message.handlers
        if isinstance(getattr(handler.callback, "__self__", None), TemplateHandler)
    ]


def test_router_registers_voice_handler_after_description() -> None:
    """Голосовой обработчик подключён в сценарии шаблона, до обработчика ссылок.

    Порядок «описание → голос» фиксирован: F.voice не конкурирует с F.text, а
    allowlist проверяется в самом обработчике голоса. Регистрация раньше
    обработчика ссылок важна и для голосовых сообщений.
    """
    templates, _ = saved_templates()
    router = create_router(
        UnusedSummaryService(),
        template_service=templates,
        template_parser=FakeTemplateParser(),
    )

    assert registered_template_methods(router) == [
        "handle_open",
        "handle_open",
        "handle_description",
        "handle_voice",
    ]
    owners = registered_owners(router)
    assert owners.index(LinkHandler) == len(owners) - 1


def test_router_passes_active_template_of_the_sender_to_summary_service(
    tmp_path: Path,
) -> None:
    """Роутер проводит ``template_service`` в обработчик ссылки: шаблон доходит."""
    templates, repository = saved_templates()
    service = RecordingSummaryService(result=make_result(tmp_path))
    router = create_router(
        service,
        allowed_user_ids=(42, 1001),
        template_service=templates,
        template_parser=FakeTemplateParser(),
    )

    requests = run_updates(router, [make_update(VIDEO_URL, user_id=42)])

    assert len(service.calls) == 1
    url, template = service.calls[0]
    assert url == VIDEO_URL
    assert isinstance(template, ActiveTemplate)
    assert template.sections == WIRED_TEMPLATE.sections
    assert template.tone == WIRED_TEMPLATE.tone
    assert template.origin is TemplateOrigin.USER
    assert repository.loads == [42]
    texts = [request.text for request in requests if isinstance(request, SendMessage)]
    assert texts[0] == MESSAGE_ACCEPTED


def test_router_does_not_apply_template_of_one_user_to_another(tmp_path: Path) -> None:
    """Через роутер: шаблон 42 не применяется к ссылке пользователя 1001."""
    templates, _ = saved_templates()
    service = RecordingSummaryService(result=make_result(tmp_path))
    router = create_router(
        service,
        template_service=templates,
        template_parser=FakeTemplateParser(),
    )

    run_updates(
        router,
        [
            make_update(VIDEO_URL, user_id=42),
            make_update(VIDEO_URL, user_id=1001, update_id=2),
        ],
    )

    assert len(service.calls) == 2
    _, template_a = service.calls[0]
    _, template_b = service.calls[1]
    assert isinstance(template_a, ActiveTemplate)
    assert template_b is None, "пользователь без шаблона должен идти по встроенному пути"


def test_old_constructors_without_template_arguments_still_work() -> None:
    """``LinkHandler(service)`` и ``create_router(service)`` остаются валидными."""
    service = SummaryService(FakeVideoSource(), FakeSummarizer())

    assert LinkHandler(service) is not None
    assert LinkHandler(service, allowed_user_ids=(42,)) is not None

    router = create_router(service)
    assert router.name == "tldw"
    assert len(router.message.handlers) == 1
    assert len(router.callback_query.handlers) == 0


def test_build_summary_service_without_new_arguments_still_works(
    fake_env: dict[str, str],
) -> None:
    """Прежний вызов ``build_summary_service(settings)`` собирает сервис без сети."""
    settings = load_settings(fake_env)

    service = build_summary_service(settings)

    assert isinstance(service, SummaryService)
    assert service.max_duration_seconds == settings.max_video_duration_seconds


def test_build_template_service_keeps_templates_in_data_dir(fake_env: dict[str, str]) -> None:
    """``build_template_service`` пишет шаблон в ``DATA_DIR`` через ``TemplateStore``."""
    settings = load_settings(fake_env)

    service = build_template_service(settings)
    service.save(42, WIRED_TEMPLATE)

    stored = TemplateStore(settings.data_dir).load(42)
    assert stored == (WIRED_TEMPLATE.sections, WIRED_TEMPLATE.tone)
    assert (settings.data_dir / "templates" / "42.json").is_file()


def test_bootstrap_builds_one_llm_client_for_summary_and_template_parser(
    fake_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Один ``LLMClient`` уходит и в конспект, и в разбор описания шаблона."""
    settings = load_settings(fake_env)
    client = object()
    built: list[object] = []
    captured: dict[str, Any] = {}

    def fake_build_llm_client(settings_arg: object) -> object:
        built.append(settings_arg)
        return client

    def fake_build_summary_service(
        settings_arg: object,
        *,
        llm_client: object | None = None,
    ) -> UnusedSummaryService:
        captured["llm_client"] = llm_client
        return UnusedSummaryService()

    def recording_create_router(
        service: object,
        *,
        allowed_user_ids: tuple[int, ...] = (),
        template_service: object | None = None,
        template_parser: object | None = None,
        speech_transcriber: object | None = None,
    ) -> Router:
        captured["template_service"] = template_service
        captured["template_parser"] = template_parser
        captured["speech_transcriber"] = speech_transcriber
        return Router(name="tldw")

    monkeypatch.setattr("tldw.bootstrap.build_llm_client", fake_build_llm_client)
    monkeypatch.setattr("tldw.bootstrap.build_summary_service", fake_build_summary_service)
    monkeypatch.setattr("tldw.bootstrap.create_router", recording_create_router)

    application = build_application(settings)
    try:
        assert built == [settings], "клиент LLM собирается один раз на приложение"
        assert captured["llm_client"] is client
        assert captured["template_parser"] is client, "разбор описания использует тот же клиент"
        assert isinstance(captured["template_service"], TemplateService)
        speech = captured["speech_transcriber"]
        assert speech is not None, "адаптер распознавания речи должен собираться"
        assert speech.is_configured is False, "без SPEECH_* голосовой ввод выключен"
    finally:
        asyncio.run(application.close())


def test_bootstrap_skips_llm_client_when_both_consumers_are_injected(
    fake_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Обе подмены (конспект и разбор) — адаптер LLM не собирается вовсе."""

    def broken_build_llm_client(settings_arg: object) -> object:
        raise AssertionError("LLMClient не должен собираться при полной подмене")

    monkeypatch.setattr("tldw.bootstrap.build_llm_client", broken_build_llm_client)
    settings = load_settings(fake_env)

    application = build_application(
        settings,
        summary_service=UnusedSummaryService(),
        template_service=TemplateService(FakeTemplateRepository()),
        template_parser=FakeTemplateParser(),
    )
    try:
        assert "tldw" in {router.name for router in application.dispatcher.sub_routers}
    finally:
        asyncio.run(application.close())


def test_build_application_wires_both_handlers_with_defaults(
    fake_env: dict[str, str],
) -> None:
    """Сборка по умолчанию подключает шаблон раньше ссылок и создаёт ``DATA_DIR``."""
    settings = load_settings(fake_env)

    application = build_application(settings)
    try:
        (router,) = application.dispatcher.sub_routers
        assert registered_owners(router) == [
            TemplateHandler,
            TemplateHandler,
            TemplateHandler,
            TemplateHandler,
            LinkHandler,
        ]
        assert len(router.callback_query.handlers) == 1
        assert settings.data_dir.is_dir()
    finally:
        asyncio.run(application.close())

"""Сборка приложения TLDW из конфига.

Модуль создаёт aiogram-бота и Dispatcher, собирает сервис конспекта и сервис
шаблонов из настроек, подключает слой handlers и готовит каталог данных.
Сетевые запросы здесь не выполняются: адаптеры только создаются, поэтому
сборка безопасна для тестов и для проверки конфигурации без токенов Telegram.

Проводка шаблона (T-008, ``specs/002-user-note-templates``):

* на приложение создаётся **один** :class:`tldw.adapters.llm.LLMClient` — он же
  суммаризатор конспекта и разбор описания шаблона, поэтому ключ и HTTP-клиент
  не дублируются;
* :class:`tldw.storage.templates.TemplateStore` живёт в ``DATA_DIR`` из
  конфига, а :class:`tldw.services.template.TemplateService` работает поверх
  него без сети;
* зависимости передаются в :func:`tldw.handlers.router.create_router`, который
  регистрирует обработчик шаблона раньше обработчика ссылок и проводит
  ``template_service`` в обработчик ссылки.

Голосовой ввод (T-012, FR-11): :class:`tldw.adapters.speech.SpeechAdapter`
собирается из необязательных ``SPEECH_*`` и уходит в обработчик шаблона через
узкий протокол ``SpeechTranscriber``. Без ключа и модели адаптер создаётся, но
не настроен, поэтому текстовый сценарий работает как обычно, а бот предлагает
текстовый ввод. Сеть при сборке не используется.

Сервис конспекта, разбор шаблона и распознавание речи можно подменить явно
(сквозные тесты) — тогда соответствующий адаптер не собирается.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from tldw.access import describe_allowlist, warn_if_open_access
from tldw.config import Settings
from tldw.handlers import (
    SpeechTranscriber,
    SummaryProcessor,
    TemplateParser,
    create_router,
)
from tldw.services.summary import SummaryProvider, SummaryService
from tldw.services.template import TemplateService
from tldw.storage import TemplateStore, ensure_data_dir

if TYPE_CHECKING:  # pragma: no cover - только для аннотаций
    from tldw.adapters.llm import LLMClient

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Application:
    """Собранное приложение: настройки, бот и диспетчер."""

    settings: Settings
    bot: Bot
    dispatcher: Dispatcher

    def run(self) -> None:
        """Запустить получение апдейтов Telegram (блокирующий вызов)."""
        self.dispatcher.run_polling(self.bot)

    async def close(self) -> None:
        """Закрыть сетевые ресурсы бота."""
        await self.bot.session.close()


def build_llm_client(settings: Settings) -> LLMClient:
    """Собрать клиент LLM из конфига; обращений к сети нет.

    Клиент один на приложение: его же получает разбор описания шаблона, поэтому
    ключ, таймаут и повтор не настраиваются дважды.
    """
    # Тяжёлая библиотека адаптера подключается при сборке, а не при импорте
    # bootstrap: тестам конфига и слоя handlers она не нужна.
    from tldw.adapters.llm import LLMClient

    return LLMClient.from_settings(settings)


def build_template_service(settings: Settings) -> TemplateService:
    """Собрать сервис шаблонов на файловом хранилище в ``DATA_DIR``.

    Каталог данных создаётся отдельно :func:`~tldw.storage.ensure_data_dir`;
    хранилище ничего не читает и не пишет до первого обращения пользователя,
    поэтому сборка не ходит в сеть и не трогает файлы.
    """
    return TemplateService(TemplateStore(settings.data_dir))


def build_speech_transcriber(settings: Settings) -> SpeechTranscriber:
    """Собрать адаптер распознавания речи из необязательных ``SPEECH_*``.

    Обращений к сети нет: без ключа или модели адаптер создаётся, но
    ``is_configured`` ложно, и обработчик шаблона предлагает текстовый ввод.
    """
    # Адаптер подключается при сборке, а не при импорте bootstrap: тестам
    # конфига и слоя handlers ``httpx`` адаптера не нужен.
    from tldw.adapters.speech import SpeechAdapter

    return SpeechAdapter.from_settings(settings)


def build_summary_service(
    settings: Settings,
    *,
    llm_client: SummaryProvider | None = None,
) -> SummaryService:
    """Собрать сервис конспекта на реальных адаптерах и каталоге из конфига.

    :param llm_client: готовый клиент LLM (общий с шаблонами); по умолчанию
        собирается новый. Обращений к сети нет: клиенты YouTube и LLM
        создаются, но запросы выполняются только при вызове ``process``.
    """
    # Тяжёлая библиотека адаптера подключается при сборке сервиса, а не при
    # импорте bootstrap: тестам конфига и слоя handlers они не нужны.
    from tldw.adapters.youtube import YouTubeAdapter

    client = llm_client if llm_client is not None else build_llm_client(settings)
    return SummaryService.from_settings(settings, YouTubeAdapter(), client)


def build_application(
    settings: Settings,
    *,
    summary_service: SummaryProcessor | None = None,
    template_service: TemplateService | None = None,
    template_parser: TemplateParser | None = None,
    speech_transcriber: SpeechTranscriber | None = None,
) -> Application:
    """Собрать приложение из настроек, не обращаясь к сети.

    :param settings: настройки из env.
    :param summary_service: готовая замена сервиса конспекта (сквозные тесты);
        по умолчанию собирается на реальных адаптерах.
    :param template_service: готовый сервис шаблонов (тесты); по умолчанию —
        :class:`TemplateStore` в ``settings.data_dir``.
    :param template_parser: готовый разбор описания шаблона (тесты); по
        умолчанию — общий клиент LLM.
    :param speech_transcriber: готовая замена распознавания речи (тесты);
        по умолчанию — адаптер из ``SPEECH_*`` (может быть не настроен).
    :raises OSError: если каталог данных из конфига нельзя создать.
    """
    warn_if_open_access(settings.allowed_user_ids)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()

    # Один клиент LLM на приложение: конспект и разбор шаблона. Если обе
    # зависимости подменены тестом, адаптер не собирается вовсе.
    llm_client = (
        build_llm_client(settings)
        if summary_service is None or template_parser is None
        else None
    )
    service = (
        build_summary_service(settings, llm_client=llm_client)
        if summary_service is None
        else summary_service
    )
    parser = template_parser if template_parser is not None else llm_client
    templates = (
        template_service if template_service is not None else build_template_service(settings)
    )
    speech = (
        speech_transcriber
        if speech_transcriber is not None
        else build_speech_transcriber(settings)
    )
    dispatcher.include_router(
        create_router(
            service,
            allowed_user_ids=settings.allowed_user_ids,
            template_service=templates,
            template_parser=parser,
            speech_transcriber=speech,
        )
    )
    ensure_data_dir(settings.data_dir)
    log.debug(
        "Приложение собрано: модель LLM %s, доступ: %s, голосовой ввод: %s",
        settings.llm_model,
        describe_allowlist(settings.allowed_user_ids),
        "включён" if speech.is_configured else "выключен",
    )
    return Application(settings=settings, bot=bot, dispatcher=dispatcher)


__all__ = [
    "Application",
    "build_application",
    "build_llm_client",
    "build_speech_transcriber",
    "build_summary_service",
    "build_template_service",
]

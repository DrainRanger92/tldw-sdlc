"""Сборка приложения TLDW из конфига.

Модуль создаёт aiogram-бота и Dispatcher, собирает сервис конспекта из
настроек, подключает слой handlers и готовит каталог данных. Сетевые запросы
здесь не выполняются: адаптеры только создаются, поэтому сборка безопасна для
тестов и для проверки конфигурации без токенов Telegram.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from tldw.config import Settings
from tldw.handlers import SummaryProcessor, create_router
from tldw.services.summary import SummaryService
from tldw.storage import ensure_data_dir

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


def build_summary_service(settings: Settings) -> SummaryService:
    """Собрать сервис конспекта на реальных адаптерах и каталоге из конфига.

    Обращений к сети нет: клиенты YouTube и LLM создаются, но запросы
    выполняются только при вызове ``process``.
    """
    # Тяжёлые библиотеки адаптеров подключаются при сборке сервиса, а не при
    # импорте bootstrap: тестам конфига и слоя handlers они не нужны.
    from tldw.adapters.llm import LLMClient
    from tldw.adapters.youtube import YouTubeAdapter

    return SummaryService.from_settings(
        settings,
        YouTubeAdapter(),
        LLMClient.from_settings(settings),
    )


def build_application(
    settings: Settings,
    *,
    summary_service: SummaryProcessor | None = None,
) -> Application:
    """Собрать приложение из настроек, не обращаясь к сети.

    :param settings: настройки из env.
    :param summary_service: готовая замена сервиса конспекта (сквозные тесты);
        по умолчанию собирается на реальных адаптерах.
    :raises OSError: если каталог данных из конфига нельзя создать.
    """
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    service = build_summary_service(settings) if summary_service is None else summary_service
    dispatcher.include_router(create_router(service))
    ensure_data_dir(settings.data_dir)
    log.debug("Приложение собрано: модель LLM %s", settings.llm_model)
    return Application(settings=settings, bot=bot, dispatcher=dispatcher)


__all__ = ["Application", "build_application", "build_summary_service"]

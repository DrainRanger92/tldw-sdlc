"""Сборка приложения TLDW из конфига.

Модуль создаёт aiogram-бота и Dispatcher, подключает слой handlers и готовит
каталог данных. Сетевые запросы здесь не выполняются: сборка безопасна для
тестов и для проверки конфигурации без токенов Telegram.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from tldw.config import Settings
from tldw.handlers import create_router
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


def build_application(settings: Settings) -> Application:
    """Собрать приложение из настроек, не обращаясь к сети.

    :raises OSError: если каталог данных из конфига нельзя создать.
    """
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())
    ensure_data_dir(settings.data_dir)
    log.debug("Приложение собрано: модель LLM %s", settings.llm_model)
    return Application(settings=settings, bot=bot, dispatcher=dispatcher)


__all__ = ["Application", "build_application"]

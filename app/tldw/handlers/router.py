"""Корневой роутер TLDW.

Единая точка подключения слоя handlers к Dispatcher: корневой роутер получает
сервис конспекта и регистрирует обработчик ссылок. Сборка не ходит в сеть.
"""

from aiogram import Router

from tldw.handlers.link import LinkHandler, SummaryProcessor


def create_router(service: SummaryProcessor) -> Router:
    """Создать корневой роутер и подключить обработчик ссылок."""
    router = Router(name="tldw")
    LinkHandler(service).register(router)
    return router


__all__ = ["create_router"]

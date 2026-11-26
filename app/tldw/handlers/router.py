"""Корневой роутер TLDW.

Единая точка подключения слоя handlers к Dispatcher: корневой роутер получает
сервис конспекта и allowlist из настроек, регистрирует обработчик ссылок.
Сборка не ходит в сеть.
"""

from aiogram import Router

from tldw.handlers.link import LinkHandler, SummaryProcessor


def create_router(
    service: SummaryProcessor,
    *,
    allowed_user_ids: tuple[int, ...] = (),
) -> Router:
    """Создать корневой роутер и подключить обработчик ссылок.

    :param service: сервис конспекта.
    :param allowed_user_ids: allowlist из ``Settings``; пустой — доступ всем.
    """
    router = Router(name="tldw")
    LinkHandler(service, allowed_user_ids=allowed_user_ids).register(router)
    return router


__all__ = ["create_router"]

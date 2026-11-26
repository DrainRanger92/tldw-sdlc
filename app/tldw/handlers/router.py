"""Корневой роутер TLDW.

Обработчики ссылок и команд добавляются в следующих задачах MVP; каркас
фиксирует единую точку подключения слоя handlers к Dispatcher.
"""

from aiogram import Router


def create_router() -> Router:
    """Создать корневой роутер приложения."""
    return Router(name="tldw")

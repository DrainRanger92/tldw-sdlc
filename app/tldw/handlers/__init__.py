"""Telegram-слой: роутеры и обработчики сообщений.

Обработчики — тонкие: разбирают апдейт, проверяют доступ и вызывают сервисы.
Сетевые вызовы выполняются через адаптеры, а не напрямую из handlers.
"""

from tldw.handlers.link import (
    MESSAGE_ACCEPTED,
    MESSAGE_DONE,
    MESSAGE_FAILED,
    MESSAGE_NEED_LINK,
    MESSAGE_NO_FILE,
    MESSAGE_PROCESSING,
    LinkHandler,
    SummaryProcessor,
    extract_first_url,
)
from tldw.handlers.router import create_router

__all__ = [
    "MESSAGE_ACCEPTED",
    "MESSAGE_DONE",
    "MESSAGE_FAILED",
    "MESSAGE_NEED_LINK",
    "MESSAGE_NO_FILE",
    "MESSAGE_PROCESSING",
    "LinkHandler",
    "SummaryProcessor",
    "create_router",
    "extract_first_url",
]

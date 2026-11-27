"""Telegram-слой: роутеры и обработчики сообщений.

Обработчики — тонкие: разбирают апдейт, проверяют доступ и вызывают сервисы.
Сетевые вызовы выполняются через адаптеры, а не напрямую из handlers.
"""

from tldw.handlers.link import (
    MESSAGE_ACCEPTED,
    MESSAGE_ACCESS_DENIED,
    MESSAGE_DONE,
    MESSAGE_NEED_LINK,
    MESSAGE_NO_FILE,
    MESSAGE_PROCESSING,
    LinkHandler,
    SummaryProcessor,
    extract_first_url,
)
from tldw.handlers.router import create_router
from tldw.handlers.template import (
    TEMPLATE_BUTTON_TEXT,
    TemplateAction,
    TemplateHandler,
    TemplateParser,
    TemplateStates,
    main_keyboard,
    render_candidate_text,
    render_template_text,
)

__all__ = [
    "MESSAGE_ACCESS_DENIED",
    "MESSAGE_ACCEPTED",
    "MESSAGE_DONE",
    "MESSAGE_NEED_LINK",
    "MESSAGE_NO_FILE",
    "MESSAGE_PROCESSING",
    "TEMPLATE_BUTTON_TEXT",
    "LinkHandler",
    "SummaryProcessor",
    "TemplateAction",
    "TemplateHandler",
    "TemplateParser",
    "TemplateStates",
    "create_router",
    "extract_first_url",
    "main_keyboard",
    "render_candidate_text",
    "render_template_text",
]

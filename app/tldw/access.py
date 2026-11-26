"""Доступ к боту по allowlist Telegram user ID (T-007).

Список разрешённых ID читается из env (``TELEGRAM_ALLOWED_USER_IDS``) в
:mod:`tldw.config`; здесь — только правило проверки. Оно применяется в
обработчике **до** разбора ссылки и до любых вызовов адаптеров, чтобы запрос
постороннего не тратил квоту YouTube/LLM. Пустой список — доступ открыт всем;
при старте об этом пишется предупреждение в лог.

Модуль нейтральный: не импортирует ``handlers``, ``services`` и ``adapters``,
поэтому его могут использовать и слой Telegram, и сборка приложения (ADR-002).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: Предупреждение при старте, если allowlist пуст и бот открыт всем.
OPEN_ACCESS_WARNING = (
    "TELEGRAM_ALLOWED_USER_IDS не задан: доступ к боту открыт всем пользователям. "
    "Укажите Telegram user ID через запятую или пробел, чтобы ограничить доступ."
)


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    """Правило доступа: пустой allowlist пропускает всех, иначе — только список.

    ``None`` в :meth:`is_allowed` означает, что у сообщения нет отправителя
    (например, анонимная публикация канала). При заданном allowlist такой
    запрос отклоняется: доступ по умолчанию закрыт.
    """

    allowed_user_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        """Убрать повторы, сохранив порядок из конфига."""
        object.__setattr__(self, "allowed_user_ids", tuple(dict.fromkeys(self.allowed_user_ids)))

    @property
    def is_open(self) -> bool:
        """Доступ открыт всем (allowlist пуст)."""
        return not self.allowed_user_ids

    def is_allowed(self, user_id: int | None) -> bool:
        """Может ли пользователь с таким ID пользоваться ботом."""
        if self.is_open:
            return True
        return user_id is not None and user_id in self.allowed_user_ids


def describe_allowlist(allowed_user_ids: tuple[int, ...]) -> str:
    """Описать allowlist, не раскрывая сами ID."""
    if not allowed_user_ids:
        return "все пользователи"
    return f"{len(allowed_user_ids)} ID"


def warn_if_open_access(allowed_user_ids: tuple[int, ...]) -> None:
    """Записать в лог предупреждение, если allowlist пуст."""
    if allowed_user_ids:
        return
    log.warning(OPEN_ACCESS_WARNING)


__all__ = [
    "OPEN_ACCESS_WARNING",
    "AccessPolicy",
    "describe_allowlist",
    "warn_if_open_access",
]

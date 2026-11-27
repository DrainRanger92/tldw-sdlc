"""Доступ к боту по allowlist Telegram user ID (T-007).

Список разрешённых ID читается из env (``TELEGRAM_ALLOWED_USER_IDS``) в
:mod:`tldw.config`; здесь — только правило проверки. Оно применяется в
обработчике **до** разбора ссылки и до любых вызовов адаптеров, чтобы запрос
постороннего не тратил квоту YouTube/LLM. Пустой список — доступ открыт всем;
при старте об этом пишется предупреждение в лог.

T-016: «доступ всем» — это осознанное решение, а не молчаливый fail-open.
Забытый аргумент ``allowed_user_ids`` в проводке даёт :data:`MISSING_ALLOWLIST_WARNING`
(явный :meth:`AccessPolicy.open`), а явный пустой кортеж остаётся
документированным поведением MVP. Дедупликация ID и проверка ``is_open``
живут здесь, в одном месте: ``config`` и обработчики используют
:func:`unique_user_ids`/:class:`AccessPolicy`, а не свои копии правила.

Модуль нейтральный: не импортирует ``handlers``, ``services`` и ``adapters``,
поэтому его могут использовать и слой Telegram, и сборка приложения (ADR-002).
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

log = logging.getLogger(__name__)

#: Сколько ID отказов limiter помнит для «первый раз — WARNING».
#: Публичный бот может отклонить неограниченное число пользователей, поэтому
#: память ограничена: самые старые ID вытесняются (T-016).
MAX_TRACKED_DENIALS: Final = 1024

#: Предупреждение при старте, если allowlist пуст и бот открыт всем.
OPEN_ACCESS_WARNING = (
    "TELEGRAM_ALLOWED_USER_IDS не задан: доступ к боту открыт всем пользователям. "
    "Укажите Telegram user ID через запятую или пробел, чтобы ограничить доступ."
)
#: Предупреждение, если проводка вообще забыла передать allowlist (T-016).
#: Отличается от :data:`OPEN_ACCESS_WARNING`: пустой список может быть осознанным
#: решением, а отсутствие аргумента — ошибкой проводки.
MISSING_ALLOWLIST_WARNING = (
    "allowlist не передан: доступ к боту открыт всем пользователям. "
    "Передайте allowed_user_ids явно; пустой кортеж — осознанный доступ всем."
)


def unique_user_ids(allowed_user_ids: Iterable[int]) -> tuple[int, ...]:
    """Убрать повторы ID, сохранив порядок из конфига.

    Единственная реализация правила дедупликации: её используют и
    :func:`tldw.config._parse_allowed_user_ids`, и :class:`AccessPolicy`.
    """
    return tuple(dict.fromkeys(allowed_user_ids))


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    """Правило доступа: пустой allowlist пропускает всех, иначе — только список.

    ``None`` в :meth:`is_allowed` означает, что у сообщения нет отправителя
    (например, анонимная публикация канала). При заданном allowlist такой
    запрос отклоняется: доступ по умолчанию закрыт.
    """

    allowed_user_ids: tuple[int, ...] = ()

    @classmethod
    def open(cls) -> AccessPolicy:
        """Явно открытая политика: доступ всем (документированный MVP).

        Отдельный конструктор нужен, чтобы fail-open в проводке был виден в
        коде: забытый аргумент даёт предупреждение и ``AccessPolicy.open()``,
        а не случайную пустую последовательность.
        """
        return cls(())

    def __post_init__(self) -> None:
        """Убрать повторы, сохранив порядок из конфига."""
        object.__setattr__(
            self,
            "allowed_user_ids",
            unique_user_ids(self.allowed_user_ids),
        )

    @property
    def is_open(self) -> bool:
        """Доступ открыт всем (allowlist пуст)."""
        return not self.allowed_user_ids

    def is_allowed(self, user_id: int | None) -> bool:
        """Может ли пользователь с таким ID пользоваться ботом."""
        if self.is_open:
            return True
        return user_id is not None and user_id in self.allowed_user_ids


class DenialLogLimiter:
    """Не даёт повторным отказам заливать лог ``WARNING`` (T-016).

    Первый отказ пользователя пишется на ``WARNING``, повторы — на ``DEBUG``.
    Лимитер живёт в экземпляре обработчика, поэтому счётчик не общий для
    нескольких ботов в тестах. Ключ — ``user_id`` (у сообщения без отправителя
    — ``None``): один и тот же посторонний не создаёт новую запись на каждое
    сообщение, а разные пользователи остаются видны.

    Память ограничена :data:`MAX_TRACKED_DENIALS`: на публичном боте множество
    отклонённых ID росло бы бесконечно. При переполнении вытесняется самый
    старый ID — он снова получит ``WARNING``, что безопасно (лишняя запись, а
    не потеря).
    """

    __slots__ = ("_limit", "_reported")

    def __init__(self, *, limit: int = MAX_TRACKED_DENIALS) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit должен быть положительным целым")
        self._limit = limit
        self._reported: OrderedDict[int | None, None] = OrderedDict()

    @property
    def tracked(self) -> int:
        """Сколько ID сейчас в памяти limiter (не больше ``limit``)."""
        return len(self._reported)

    def log_denial(
        self,
        logger: logging.Logger,
        message: str,
        *args: object,
        user_id: int | None = None,
    ) -> None:
        """Записать первый отказ на ``WARNING``, повторный — на ``DEBUG``."""
        level = logging.DEBUG if user_id in self._reported else logging.WARNING
        self._reported[user_id] = None
        self._reported.move_to_end(user_id)
        while len(self._reported) > self._limit:
            self._reported.popitem(last=False)
        logger.log(level, message, *args)


def describe_allowlist(allowed_user_ids: tuple[int, ...]) -> str:
    """Описать allowlist, не раскрывая сами ID."""
    policy = AccessPolicy(allowed_user_ids)
    if policy.is_open:
        return "все пользователи"
    return f"{len(policy.allowed_user_ids)} ID"


def warn_if_open_access(allowed_user_ids: tuple[int, ...]) -> None:
    """Записать в лог предупреждение, если allowlist пуст."""
    if not AccessPolicy(allowed_user_ids).is_open:
        return
    log.warning(OPEN_ACCESS_WARNING)


def warn_if_allowlist_missing() -> None:
    """Записать предупреждение, если проводка забыла allowlist (T-016)."""
    log.warning(MISSING_ALLOWLIST_WARNING)


__all__ = [
    "MAX_TRACKED_DENIALS",
    "MISSING_ALLOWLIST_WARNING",
    "OPEN_ACCESS_WARNING",
    "AccessPolicy",
    "DenialLogLimiter",
    "describe_allowlist",
    "unique_user_ids",
    "warn_if_allowlist_missing",
    "warn_if_open_access",
]

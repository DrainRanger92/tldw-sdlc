"""Тесты правила доступа по allowlist (T-007).

Без сети, токенов и файлов: проверяются чистая политика
:class:`tldw.access.AccessPolicy`, описание allowlist и предупреждение в лог
при пустом списке.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from tldw.access import (
    MISSING_ALLOWLIST_WARNING,
    OPEN_ACCESS_WARNING,
    AccessPolicy,
    DenialLogLimiter,
    describe_allowlist,
    warn_if_open_access,
)
from tldw.handlers import MESSAGE_NEED_LINK


@pytest.mark.parametrize("user_id", [None, 0, 42, 10**12])
def test_empty_allowlist_allows_everyone(user_id: int | None) -> None:
    policy = AccessPolicy()

    assert policy.is_open is True
    assert policy.is_allowed(user_id) is True


def test_listed_user_id_is_allowed() -> None:
    policy = AccessPolicy((1001, 1002))

    assert policy.is_open is False
    assert policy.is_allowed(1001) is True
    assert policy.is_allowed(1002) is True


@pytest.mark.parametrize("user_id", [None, 0, 42, 1003])
def test_other_user_id_is_denied(user_id: int | None) -> None:
    policy = AccessPolicy((1001, 1002))

    assert policy.is_allowed(user_id) is False


def test_duplicate_ids_are_collapsed_and_order_is_kept() -> None:
    policy = AccessPolicy((1002, 1001, 1002))

    assert policy.allowed_user_ids == (1002, 1001)


@pytest.mark.parametrize(
    ("allowed_user_ids", "expected"),
    [((), "все пользователи"), ((1001,), "1 ID"), ((1001, 1002), "2 ID")],
)
def test_describe_allowlist_does_not_reveal_ids(
    allowed_user_ids: tuple[int, ...], expected: str
) -> None:
    description = describe_allowlist(allowed_user_ids)

    assert description == expected
    assert "1001" not in description


def test_open_access_logs_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="tldw.access"):
        warn_if_open_access(())

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings, "пустой allowlist должен давать предупреждение"
    assert warnings[0].getMessage() == OPEN_ACCESS_WARNING
    assert "TELEGRAM_ALLOWED_USER_IDS" in warnings[0].getMessage()
    assert "всем" in warnings[0].getMessage()


def test_filled_allowlist_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="tldw.access"):
        warn_if_open_access((1001,))

    assert [record for record in caplog.records if record.levelno == logging.WARNING] == []


# --- T-016: явный fail-open, единый источник правила, лимит отказов ---------


def test_open_policy_is_explicit_and_open() -> None:
    policy = AccessPolicy.open()

    assert policy.is_open is True
    assert policy.allowed_user_ids == ()
    assert policy.is_allowed(42) is True


def test_config_and_policy_use_the_same_dedup_function() -> None:
    """Одно правило дедупликации ID, а не две копии (P3 T-007)."""
    from tldw import access, config

    assert config.unique_user_ids is access.unique_user_ids


def test_describe_allowlist_uses_the_policy_rule() -> None:
    assert describe_allowlist((1001, 1001, 1002)) == "2 ID"


def test_missing_allowlist_warns_but_keeps_mvp_access(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Забытый аргумент не молчит, но доступ остаётся «всем» (MVP)."""
    from tldw.handlers import LinkHandler

    class _Service:
        def process(self, url: str, *, template: object | None = None) -> None:
            raise AssertionError("сервис не должен вызываться")

    with caplog.at_level(logging.WARNING, logger="tldw.access"):
        handler = LinkHandler(_Service())

    warnings = [record for record in caplog.records if record.name == "tldw.access"]
    assert warnings
    assert warnings[0].getMessage() == MISSING_ALLOWLIST_WARNING

    # Сообщение denied не отправляется: политика явно открыта.
    answers: list[str] = []

    class _Message:
        from_user = SimpleNamespace(id=42)
        text = None

        async def answer(self, text: str, **kwargs: object) -> None:
            answers.append(text)

    asyncio.run(handler.handle_message(_Message()))  # type: ignore[arg-type]
    assert answers == [MESSAGE_NEED_LINK]


def test_explicit_empty_allowlist_does_not_warn_about_missing_argument(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from tldw.handlers import LinkHandler

    class _Service:
        def process(self, url: str, *, template: object | None = None) -> None:
            raise AssertionError("сервис не должен вызываться")

    with caplog.at_level(logging.WARNING, logger="tldw.access"):
        LinkHandler(_Service(), allowed_user_ids=())

    assert caplog.records == []


def test_denial_log_limiter_writes_only_the_first_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    limiter = DenialLogLimiter()
    logger = logging.getLogger("tldw.test.denials")

    with caplog.at_level(logging.DEBUG, logger="tldw.test.denials"):
        for _ in range(3):
            limiter.log_denial(logger, "Доступ закрыт: %s", 42, user_id=42)
        limiter.log_denial(logger, "Другой пользователь: %s", 43, user_id=43)

    records = [record for record in caplog.records if record.name == logger.name]
    levels = [record.levelno for record in records]
    assert levels.count(logging.WARNING) == 2, "по одному WARNING на пользователя"
    assert levels.count(logging.DEBUG) == 2, "повторы пишутся на DEBUG"


def test_denial_log_limiter_does_not_shadow_module_log() -> None:
    """Метод назван ``log_denial`` и не затеняет модульный ``log`` (REVIEW-2).

    До FIX-2 метод назывался ``log``: выполнение он не путал, но чтение
    ``limiter.log(...)`` рядом с модульным
    ``log = logging.getLogger(__name__)`` выглядело как запись в чужой лог.
    """
    from tldw import access

    assert isinstance(access.log, logging.Logger)
    assert hasattr(DenialLogLimiter, "log_denial")
    assert not hasattr(DenialLogLimiter, "log"), "метод не должен затенять log"


def test_denial_log_limiter_does_not_grow_without_bound(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Память limiter ограничена: старые ID вытесняются (P3 T-016)."""
    limiter = DenialLogLimiter(limit=2)
    logger = logging.getLogger("tldw.test.denials.bounded")

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        limiter.log_denial(logger, "отказ %s", 1, user_id=1)
        limiter.log_denial(logger, "отказ %s", 2, user_id=2)
        assert limiter.tracked == 2
        limiter.log_denial(logger, "отказ %s", 3, user_id=3)
        assert limiter.tracked == 2, "третий ID вытеснил самый старый"
        # Вытесненный ID снова даёт WARNING, а не теряет запись навсегда.
        limiter.log_denial(logger, "отказ %s", 1, user_id=1)

    records = [record for record in caplog.records if record.name == logger.name]
    levels = [record.levelno for record in records]
    assert levels.count(logging.WARNING) == 4
    assert levels.count(logging.DEBUG) == 0

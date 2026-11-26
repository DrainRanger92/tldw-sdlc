"""Тесты правила доступа по allowlist (T-007).

Без сети, токенов и файлов: проверяются чистая политика
:class:`tldw.access.AccessPolicy`, описание allowlist и предупреждение в лог
при пустом списке.
"""

from __future__ import annotations

import logging

import pytest

from tldw.access import OPEN_ACCESS_WARNING, AccessPolicy, describe_allowlist, warn_if_open_access


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

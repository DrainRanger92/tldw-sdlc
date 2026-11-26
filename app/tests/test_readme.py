"""Проверки README и исходников: инструкция запуска и отсутствие секретов.

Тесты читают только файлы, поэтому проходят без сети, токенов и ``tmp_path``.
Они удерживают критерии T-009: README описывает установку, все переменные env,
запуск и ручную проверку на реальной ссылке YouTube с ожидаемыми сообщениями,
полный шаблон заметки, а настоящих токенов и ключей нет ни в документации,
ни в коде, ни в тестах.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from support import FAKE_SECRETS

from tldw.config import ALL_ENV_VARS
from tldw.handlers import MESSAGE_ACCEPTED, MESSAGE_DONE, MESSAGE_PROCESSING
from tldw.services.note import FRONTMATTER_KEYS, SECTION_HEADINGS

APP_DIR = Path(__file__).resolve().parents[1]
README = APP_DIR / "README.md"

#: Формы настоящих секретов; фиктивные значения — в allow-list ниже.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b"),  # токен Telegram-бота
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),  # ключ в стиле OpenAI/DeepSeek
    re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._~+/=-]{10,})\b"),  # значение заголовка
)

#: Известные фиктивные значения: они похожи на секреты, но заведомо ненастоящие.
_KNOWN_FAKE_SECRETS = frozenset(
    {
        *FAKE_SECRETS,
        "123456789:TEST-entrypoint-token",  # tests/test_entrypoint.py
        "123456789:test-token-not-a-secret",  # tests/test_llm_adapter.py
        "test-llm-key-not-a-secret",  # ключ LLM в тестах адаптера и сервиса
    }
)

_IGNORED_PARTS = frozenset({".venv", "data", "__pycache__"})


def readme_text() -> str:
    return README.read_text(encoding="utf-8")


def python_sources() -> Iterator[Path]:
    """Все исходники приложения и тестов, кроме окружения и данных."""
    for path in sorted(APP_DIR.rglob("*.py")):
        if not _IGNORED_PARTS.intersection(path.relative_to(APP_DIR).parts):
            yield path


def matched_secret(match: re.Match[str]) -> str:
    """Значение секрета: группа шаблона (Bearer) или всё совпадение."""
    return next((group for group in match.groups() if group), match.group(0))


@pytest.mark.parametrize(
    "command",
    [
        "python3 -m venv .venv",
        'pip install -e ".[dev]"',
        ".venv/bin/python main.py",
        "--check-config",
        ".venv/bin/python -m pytest -q",
        ".venv/bin/ruff check .",
    ],
)
def test_readme_describes_installation_and_launch(command: str) -> None:
    assert command in readme_text(), f"в README нет команды {command!r}"


@pytest.mark.parametrize("name", ALL_ENV_VARS)
def test_readme_lists_every_env_var(name: str) -> None:
    assert name in readme_text(), f"переменная {name} не описана в README"


def test_readme_links_env_example() -> None:
    assert ".env.example" in readme_text(), "README должен ссылаться на образец env"


def test_readme_has_manual_check_with_real_youtube_link_and_messages() -> None:
    text = readme_text()
    lowered = text.lower()

    assert "ручная проверка" in lowered
    assert "https://www.youtube.com/watch?v=" in text
    for message in (MESSAGE_ACCEPTED, MESSAGE_PROCESSING, MESSAGE_DONE):
        assert message in text, f"ожидаемое сообщение {message!r} не описано"
    assert "DATA_DIR" in text, "README должен указать, где искать .md"
    for key in FRONTMATTER_KEYS:
        assert key in text, f"поле frontmatter {key} не описано"
    for heading in SECTION_HEADINGS:
        title = heading.removeprefix("## ")
        assert title in text, f"секция {title!r} не описана"


@pytest.mark.parametrize("pattern", _SECRET_PATTERNS, ids=["telegram-token", "sk-key", "bearer"])
def test_readme_contains_no_real_secrets(pattern: re.Pattern[str]) -> None:
    match = pattern.search(readme_text())

    assert match is None, f"в README похоже на настоящий секрет: {match.group(0)!r}"


@pytest.mark.parametrize("pattern", _SECRET_PATTERNS, ids=["telegram-token", "sk-key", "bearer"])
def test_code_and_tests_contain_no_real_secrets(pattern: re.Pattern[str]) -> None:
    leaks = [
        f"{path.relative_to(APP_DIR)}: {match.group(0)!r}"
        for path in python_sources()
        for match in pattern.finditer(path.read_text(encoding="utf-8"))
        if matched_secret(match) not in _KNOWN_FAKE_SECRETS
    ]

    assert not leaks, "похоже на настоящий секрет: " + "; ".join(leaks)

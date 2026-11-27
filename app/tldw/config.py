"""Загрузчик конфигурации TLDW.

Единственный источник настроек — переменные окружения. Файл ``app/.env``
лишь подставляет значения для переменных, которых нет в окружении (реальный
env всегда приоритетнее). Модуль не ходит в сеть и никогда не пишет значения
секретов в сообщения об ошибках и в repr настроек.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = APP_DIR / ".env"
DEFAULT_DATA_DIR = APP_DIR / "data"
DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_VIDEO_DURATION_SECONDS = 2 * 60 * 60
DEFAULT_LOG_LEVEL = "INFO"

ENV_TELEGRAM_BOT_TOKEN = "TELEGRAM_BOT_TOKEN"
ENV_TELEGRAM_ALLOWED_USER_IDS = "TELEGRAM_ALLOWED_USER_IDS"
ENV_LLM_API_KEY = "LLM_API_KEY"
ENV_LLM_MODEL = "LLM_MODEL"
ENV_LLM_BASE_URL = "LLM_BASE_URL"
ENV_LLM_TIMEOUT_SECONDS = "LLM_TIMEOUT_SECONDS"
ENV_VIDEO_MAX_DURATION_SECONDS = "VIDEO_MAX_DURATION_SECONDS"
ENV_DATA_DIR = "DATA_DIR"
ENV_LOG_LEVEL = "LOG_LEVEL"

# --- Распознавание речи (необязательный голосовой ввод, T-011) ---
#: Базовый URL OpenAI-совместимого API распознавания речи по умолчанию.
DEFAULT_SPEECH_BASE_URL = "https://api.openai.com/v1"
#: Таймаут одного запроса распознавания речи по умолчанию, секунды.
DEFAULT_SPEECH_TIMEOUT_SECONDS = 60.0
#: Жёсткий лимит длительности голосового сообщения, секунды (FR-11).
#: Живёт в нейтральном конфиге, чтобы адаптер и обработчик не дублировали 60.
MAX_AUDIO_DURATION_SECONDS = 60

ENV_SPEECH_API_KEY = "SPEECH_API_KEY"
ENV_SPEECH_BASE_URL = "SPEECH_BASE_URL"
ENV_SPEECH_MODEL = "SPEECH_MODEL"
ENV_SPEECH_TIMEOUT_SECONDS = "SPEECH_TIMEOUT_SECONDS"

#: Переменные, без которых приложение не запускается.
REQUIRED_ENV_VARS: tuple[str, ...] = (
    ENV_TELEGRAM_BOT_TOKEN,
    ENV_LLM_API_KEY,
    ENV_LLM_MODEL,
)
#: Переменные с безопасными дефолтами.
OPTIONAL_ENV_VARS: tuple[str, ...] = (
    ENV_TELEGRAM_ALLOWED_USER_IDS,
    ENV_LLM_BASE_URL,
    ENV_LLM_TIMEOUT_SECONDS,
    ENV_VIDEO_MAX_DURATION_SECONDS,
    ENV_DATA_DIR,
    ENV_LOG_LEVEL,
    ENV_SPEECH_API_KEY,
    ENV_SPEECH_BASE_URL,
    ENV_SPEECH_MODEL,
    ENV_SPEECH_TIMEOUT_SECONDS,
)
#: Все переменные приложения; список сверяется с ``app/.env.example`` тестом.
ALL_ENV_VARS: tuple[str, ...] = (*REQUIRED_ENV_VARS, *OPTIONAL_ENV_VARS)

CONFIG_HINT = "Заполните переменные в app/.env (образец — app/.env.example)."

_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_BOT_TOKEN_RE = re.compile(r"^[0-9]+:[^\s]+$")
_USER_ID_RE = re.compile(r"^[0-9]+$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INLINE_COMMENT_RE = re.compile(r"\s+#")


class ConfigError(ValueError):
    """Понятная ошибка конфигурации; сообщение безопасно показывать в логе."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Настройки приложения, собранные из env.

    Секреты исключены из ``repr``, чтобы случайное логирование настроек
    не раскрывало токен бота и ключ LLM.
    """

    telegram_bot_token: str = field(repr=False)
    llm_api_key: str = field(repr=False)
    llm_model: str
    allowed_user_ids: tuple[int, ...] = ()
    max_video_duration_seconds: int = DEFAULT_MAX_VIDEO_DURATION_SECONDS
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    data_dir: Path = DEFAULT_DATA_DIR
    log_level: str = DEFAULT_LOG_LEVEL
    #: Необязательная настройка голосового ввода: пустые ключ и модель значат
    #: «распознавание речи не настроено», текстовый сценарий работает без них.
    speech_api_key: str = field(default="", repr=False)
    speech_model: str = ""
    speech_base_url: str = DEFAULT_SPEECH_BASE_URL
    speech_timeout_seconds: float = DEFAULT_SPEECH_TIMEOUT_SECONDS


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Собрать и провалидировать настройки из env.

    :param environ: источник переменных; по умолчанию ``os.environ``.
    :raises ConfigError: если обязательные переменные отсутствуют или значение
        имеет неверный формат.
    """
    env = os.environ if environ is None else environ
    values = {name: _clean(env.get(name)) for name in ALL_ENV_VARS}

    missing = [name for name in REQUIRED_ENV_VARS if values[name] is None]
    if missing:
        raise ConfigError(
            f"Не заданы обязательные переменные окружения: {', '.join(missing)}. {CONFIG_HINT}"
        )

    token = _required(values, ENV_TELEGRAM_BOT_TOKEN)
    if not _BOT_TOKEN_RE.fullmatch(token):
        raise ConfigError(
            f"{ENV_TELEGRAM_BOT_TOKEN} имеет неверный формат: ожидается "
            f"«<bot_id>:<секрет>» без пробелов. {CONFIG_HINT}"
        )

    return Settings(
        telegram_bot_token=token,
        llm_api_key=_required(values, ENV_LLM_API_KEY),
        llm_model=_required(values, ENV_LLM_MODEL),
        allowed_user_ids=_parse_allowed_user_ids(values[ENV_TELEGRAM_ALLOWED_USER_IDS]),
        max_video_duration_seconds=_parse_int(
            values[ENV_VIDEO_MAX_DURATION_SECONDS],
            ENV_VIDEO_MAX_DURATION_SECONDS,
            default=DEFAULT_MAX_VIDEO_DURATION_SECONDS,
            minimum=1,
        ),
        llm_base_url=_parse_base_url(values[ENV_LLM_BASE_URL]),
        llm_timeout_seconds=_parse_float(
            values[ENV_LLM_TIMEOUT_SECONDS],
            ENV_LLM_TIMEOUT_SECONDS,
            default=DEFAULT_LLM_TIMEOUT_SECONDS,
            minimum=1.0,
        ),
        data_dir=_parse_data_dir(values[ENV_DATA_DIR]),
        log_level=_parse_log_level(values[ENV_LOG_LEVEL]),
        speech_api_key=_optional_text(values, ENV_SPEECH_API_KEY),
        speech_model=_optional_text(values, ENV_SPEECH_MODEL),
        speech_base_url=_parse_http_url(
            values[ENV_SPEECH_BASE_URL],
            ENV_SPEECH_BASE_URL,
            default=DEFAULT_SPEECH_BASE_URL,
        ),
        speech_timeout_seconds=_parse_float(
            values[ENV_SPEECH_TIMEOUT_SECONDS],
            ENV_SPEECH_TIMEOUT_SECONDS,
            default=DEFAULT_SPEECH_TIMEOUT_SECONDS,
            minimum=1.0,
        ),
    )


def read_env_file(path: Path) -> dict[str, str]:
    """Прочитать ``.env`` в словарь, не трогая ``os.environ``.

    Поддерживаются комментарии ``#``, пустые строки, префикс ``export``,
    значения в одинарных/двойных кавычках и inline-комментарии после пробела.
    Отсутствующий файл — это пустой словарь.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError(f"Не удалось прочитать файл {path}: {exc}") from exc

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not separator or not _ENV_NAME_RE.fullmatch(name):
            raise ConfigError(
                f"Некорректная строка {line_number} в {path.name}: "
                "ожидается формат ПЕРЕМЕННАЯ=значение."
            )
        values[name] = _parse_env_value(raw_value.strip(), path=path, line_number=line_number)
    return values


def load_env_file(path: Path, environ: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Подставить значения из ``.env`` в окружение, не перетирая реальный env."""
    values = read_env_file(path)
    target = os.environ if environ is None else environ
    for name, value in values.items():
        target.setdefault(name, value)
    return values


def _clean(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _required(values: Mapping[str, str | None], name: str) -> str:
    value = values[name]
    if value is None:  # pragma: no cover - защита от вызова без проверки missing
        raise ConfigError(f"Не задана обязательная переменная окружения: {name}. {CONFIG_HINT}")
    return value


def _optional_text(values: Mapping[str, str | None], name: str) -> str:
    """Необязательное значение строкой: пусто — значит «не настроено»."""
    return values[name] or ""


def _parse_allowed_user_ids(raw: str | None) -> tuple[int, ...]:
    if raw is None:
        return ()
    ids: list[int] = []
    for part in re.split(r"[\s,;]+", raw):
        if not part:
            continue
        if not _USER_ID_RE.fullmatch(part):
            raise ConfigError(
                f"{ENV_TELEGRAM_ALLOWED_USER_IDS}: ожидается список Telegram user ID — "
                "положительных целых чисел через запятую или пробел."
            )
        user_id = int(part)
        if user_id <= 0:
            raise ConfigError(
                f"{ENV_TELEGRAM_ALLOWED_USER_IDS}: user ID должен быть положительным."
            )
        if user_id not in ids:
            ids.append(user_id)
    return tuple(ids)


def _parse_int(raw: str | None, name: str, *, default: int, minimum: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(
            f"{name} должна быть целым числом секунд, например 7200 (2 часа)."
        ) from None
    if value < minimum:
        raise ConfigError(f"{name} должна быть не меньше {minimum}.")
    return value


def _parse_float(raw: str | None, name: str, *, default: float, minimum: float) -> float:
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} должна быть числом.") from None
    if not math.isfinite(value):
        # inf/nan проходят float(), но таймаут с ними ломает httpx и адаптер;
        # лучше понятная ошибка конфигурации при старте.
        raise ConfigError(f"{name} должна быть конечным числом.")
    if value < minimum:
        raise ConfigError(f"{name} должна быть не меньше {minimum}.")
    return value


def _parse_base_url(raw: str | None) -> str:
    return _parse_http_url(raw, ENV_LLM_BASE_URL, default=DEFAULT_LLM_BASE_URL)


def _parse_http_url(raw: str | None, name: str, *, default: str) -> str:
    """Разобрать необязательный базовый URL HTTP-API (LLM или распознавания)."""
    if raw is None:
        return default
    if not raw.startswith(("http://", "https://")):
        raise ConfigError(f"{name} должна начинаться с http:// или https://.")
    return raw.rstrip("/")


def _parse_data_dir(raw: str | None) -> Path:
    if raw is None:
        return DEFAULT_DATA_DIR
    path = Path(raw).expanduser()
    return path if path.is_absolute() else APP_DIR / path


def _parse_log_level(raw: str | None) -> str:
    if raw is None:
        return DEFAULT_LOG_LEVEL
    level = raw.upper()
    if level not in _LOG_LEVELS:
        allowed = ", ".join(sorted(_LOG_LEVELS))
        raise ConfigError(
            f"{ENV_LOG_LEVEL}: недопустимый уровень логирования. Допустимо: {allowed}."
        )
    return level


def _parse_env_value(raw: str, *, path: Path, line_number: int) -> str:
    if raw.startswith(('"', "'")):
        quote = raw[0]
        end = raw.find(quote, 1)
        if end == -1:
            raise ConfigError(f"Незакрытая кавычка в строке {line_number} файла {path.name}.")
        return raw[1:end]
    comment = _INLINE_COMMENT_RE.search(raw)
    if comment:
        raw = raw[: comment.start()]
    return raw.strip()


__all__ = [
    "ALL_ENV_VARS",
    "APP_DIR",
    "CONFIG_HINT",
    "ConfigError",
    "DEFAULT_DATA_DIR",
    "DEFAULT_ENV_FILE",
    "MAX_AUDIO_DURATION_SECONDS",
    "OPTIONAL_ENV_VARS",
    "REQUIRED_ENV_VARS",
    "Settings",
    "load_env_file",
    "load_settings",
    "read_env_file",
]

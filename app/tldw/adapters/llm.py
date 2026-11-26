"""Адаптер LLM: промпт конспекта, httpx-клиент и разбор JSON-ответа.

Модуль — единственная граница вызовов LLM (ADR-002): сервисы и обработчики
не импортируют ``httpx`` и не знают формат ответа провайдера. Контракт:

1. промпт и шаблон заметки — ресурсы пакета (``adapters/resources``), а не env;
2. ключ, модель, базовый URL и таймаут приходят из :class:`tldw.config.Settings`;
3. запрос идёт в OpenAI-совместимый ``chat/completions`` с требованием JSON;
4. ответ строго валидируется по разделам шаблона **до** модели заметки:
   битый JSON, отсутствие обязательного поля, пустой ответ или нарушение
   количества элементов дают :class:`LLMError`, а не частичную заметку;
5. таймаут и HTTP-ошибки провайдера поднимаются как :class:`LLMError`;
   повтор ограничен кодами 429 и 5xx.

Адаптер синхронный: вызывающий слой уводит его в отдельный поток, чтобы не
блокировать event loop Telegram-бота. Тесты подменяют транспорт
``httpx.MockTransport`` и не ходят в сеть.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Final, NoReturn

import httpx

from tldw.config import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    Settings,
)
from tldw.errors import UserFacingError

log = logging.getLogger(__name__)

#: Пакет с ресурсами промпта и шаблона.
RESOURCES_PACKAGE: Final = "tldw.adapters.resources"
#: Системный промпт: роль, жёсткая структура и JSON-схема ответа.
PROMPT_RESOURCE: Final = "summary_prompt.md"
#: Шаблон заметки, который подставляется в промпт.
TEMPLATE_RESOURCE: Final = "note_template.md"
#: Маркер подстановки шаблона в промпт.
TEMPLATE_PLACEHOLDER: Final = "{{NOTE_TEMPLATE}}"

#: Путь OpenAI-совместимого API относительно ``LLM_BASE_URL``.
CHAT_COMPLETIONS_PATH: Final = "chat/completions"
#: Ограничение на размер ответа: заметки хватает с запасом.
MAX_OUTPUT_TOKENS: Final = 4096
#: Всего попыток, включая первую; повтор только на 429/5xx.
DEFAULT_MAX_ATTEMPTS: Final = 3
#: Базовая пауза перед повтором; дальше она удваивается.
DEFAULT_BACKOFF_SECONDS: Final = 1.0

#: Границы разделов шаблона: проверяются до сборки модели заметки.
MIN_TLDR: Final = 3
MAX_TLDR: Final = 5
MIN_KEY_IDEAS: Final = 5
MAX_KEY_IDEAS: Final = 10
MIN_QUOTES: Final = 2
MAX_QUOTES: Final = 3
MIN_FOLLOW_UPS: Final = 1
MAX_FOLLOW_UPS: Final = 10
MAX_TIMELINE_ENTRIES: Final = 30

MESSAGE_LLM_UNAVAILABLE: Final = "AI не ответил. Попробуйте позже."
MESSAGE_LLM_TIMEOUT: Final = "AI не ответил вовремя. Попробуйте позже."
MESSAGE_LLM_BAD_RESPONSE: Final = "AI вернул некорректный ответ. Попробуйте позже."

#: Таймкод ``MM:SS`` или ``HH:MM:SS``; секунды всегда две цифры.
_TIMECODE_RE: Final = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")

JSON_MAPPING = Mapping[str, Any]


class LLMError(UserFacingError):
    """Базовая ошибка LLM-адаптера.

    Наследуется от :class:`tldw.errors.UserFacingError`: сообщение безопасно
    показывать пользователю, а подробная причина сбоя пишется в лог и не
    содержит ключа или транскрипта.
    """

    def __init__(self, message: str = MESSAGE_LLM_UNAVAILABLE) -> None:
        super().__init__(message)


class LLMTimeoutError(LLMError):
    """Провайдер не ответил за таймаут из конфига."""

    def __init__(self, message: str = MESSAGE_LLM_TIMEOUT) -> None:
        super().__init__(message)


class LLMProviderError(LLMError):
    """Провайдер ответил HTTP-ошибкой или запрос не дошёл по сети."""

    def __init__(self, message: str = MESSAGE_LLM_UNAVAILABLE) -> None:
        super().__init__(message)


class LLMResponseError(LLMError):
    """Ответ провайдера пуст, не JSON или не соответствует шаблону."""

    def __init__(self, message: str = MESSAGE_LLM_BAD_RESPONSE) -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SummaryTimelineEntry:
    """Смысловой блок заметки: нормализованный таймкод и название."""

    timecode: str
    title: str


@dataclass(frozen=True, slots=True)
class SummaryQuote:
    """Дословная цитата из видео с обязательным таймкодом."""

    text: str
    timecode: str


@dataclass(frozen=True, slots=True)
class VideoSummary:
    """Провалидированный ответ LLM по разделам шаблона.

    Это ещё не модель заметки: frontmatter (title, source, channel, duration,
    date, tags, status) собирает сервис конспекта из метаданных видео.
    """

    tldr: tuple[str, ...]
    key_ideas: tuple[str, ...]
    timeline: tuple[SummaryTimelineEntry, ...]
    quotes: tuple[SummaryQuote, ...]
    follow_ups: tuple[str, ...]


class LLMClient:
    """Клиент LLM с ключом и моделью из конфига, таймаутом и повтором.

    :param api_key: ключ провайдера; в лог и repr не попадает.
    :param model: имя модели для поля ``model`` запроса.
    :param base_url: базовый URL OpenAI-совместимого API.
    :param timeout: таймаут одного HTTP-запроса, секунды.
    :param max_attempts: всего попыток при 429/5xx, включая первую.
    :param backoff_seconds: пауза перед первым повтором (далее удваивается).
    :param transport: подмена транспорта httpx (в тестах — ``MockTransport``).
    :param sleep: функция паузы перед повтором (в тестах — без реального сна).
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_LLM_BASE_URL,
        timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        key = api_key.strip()
        model_name = model.strip()
        base = base_url.strip().rstrip("/")
        if not key:
            raise ValueError("LLM_API_KEY не задан")
        if not model_name:
            raise ValueError("LLM_MODEL не задан")
        if not base.startswith(("http://", "https://")):
            raise ValueError("LLM_BASE_URL должен начинаться с http:// или https://")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout должен быть положительным конечным числом секунд")
        if max_attempts < 1:
            raise ValueError("max_attempts должен быть не меньше 1")
        if not math.isfinite(backoff_seconds) or backoff_seconds < 0:
            raise ValueError("backoff_seconds не может быть отрицательным или бесконечным")

        self.model = model_name
        self.base_url = base
        self.timeout = float(timeout)
        self.max_attempts = max_attempts
        self.backoff_seconds = float(backoff_seconds)
        self._api_key = key
        self._transport = transport
        self._sleep = time.sleep if sleep is None else sleep

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    ) -> LLMClient:
        """Собрать клиента из настроек приложения (ключ и модель — из env)."""
        return cls(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            transport=transport,
            sleep=sleep,
        )

    def __repr__(self) -> str:
        """Не показывать ключ при случайном логировании клиента."""
        return f"LLMClient(model={self.model!r}, base_url={self.base_url!r})"

    def summarize(
        self,
        transcript: str,
        *,
        title: str = "",
        channel: str = "",
    ) -> VideoSummary:
        """Отправить транскрипт в LLM и вернуть провалидированный конспект.

        :param transcript: текст с таймкодами (склейку делает сервис конспекта).
        :param title: название видео для контекста промпта.
        :param channel: канал видео для контекста промпта.
        :raises LLMTimeoutError: провайдер не ответил за таймаут.
        :raises LLMProviderError: HTTP-ошибка, сетевой сбой или исчерпаны повторы.
        :raises LLMResponseError: пустой, битый или неполный ответ провайдера.
        """
        text = transcript.strip()
        if not text:
            log.warning("LLM: пустой транскрипт — конспект не запрашивается")
            raise LLMError("Нет текста для конспекта.")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": build_system_prompt()},
                {
                    "role": "user",
                    "content": build_user_prompt(text, title=title, channel=channel),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "stream": False,
        }

        with self._make_client() as client:
            for attempt in range(1, self.max_attempts + 1):
                try:
                    response = client.post(CHAT_COMPLETIONS_PATH, json=payload)
                except httpx.TimeoutException as exc:
                    log.warning(
                        "LLM: таймаут запроса (%.1f с, попытка %s/%s, %s)",
                        self.timeout,
                        attempt,
                        self.max_attempts,
                        type(exc).__name__,
                    )
                    raise LLMTimeoutError() from exc
                except httpx.HTTPError as exc:
                    log.warning(
                        "LLM: сетевая ошибка запроса (попытка %s/%s, %s)",
                        attempt,
                        self.max_attempts,
                        type(exc).__name__,
                    )
                    raise LLMProviderError() from exc

                if _is_retryable(response.status_code) and attempt < self.max_attempts:
                    delay = self.backoff_seconds * (2 ** (attempt - 1))
                    log.warning(
                        "LLM: HTTP %s %s, повтор через %.1f с (попытка %s/%s)",
                        response.status_code,
                        response.reason_phrase,
                        delay,
                        attempt,
                        self.max_attempts,
                    )
                    self._sleep(delay)
                    continue

                if response.status_code >= 400:
                    log.error(
                        "LLM: провайдер ответил HTTP %s %s (попытка %s/%s)",
                        response.status_code,
                        response.reason_phrase,
                        attempt,
                        self.max_attempts,
                    )
                    raise LLMProviderError()

                return _parse_response(response)

        raise LLMProviderError()  # pragma: no cover - цикл всегда возвращает или бросает

    def _make_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
            transport=self._transport,
        )


def parse_summary(content: str) -> VideoSummary:
    """Разобрать JSON-ответ LLM и провалидировать его по шаблону.

    Лишние поля провайдера игнорируются: обязательные разделы проверяются
    ниже, а необязательный мусор не должен ломать заметку.

    :raises LLMResponseError: если ответ пуст, не JSON, не объект, не содержит
        обязательного поля или нарушает количество элементов раздела.
    """
    data = _loads_json_object(content)
    return VideoSummary(
        tldr=_string_list(data, "tldr", MIN_TLDR, MAX_TLDR),
        key_ideas=_string_list(data, "key_ideas", MIN_KEY_IDEAS, MAX_KEY_IDEAS),
        timeline=_timeline(data),
        quotes=_quotes(data),
        follow_ups=_string_list(data, "follow_ups", MIN_FOLLOW_UPS, MAX_FOLLOW_UPS),
    )


def load_summary_prompt() -> str:
    """Прочитать системный промпт из ресурсов пакета."""
    return _read_resource(PROMPT_RESOURCE)


def load_note_template() -> str:
    """Прочитать шаблон заметки из ресурсов пакета."""
    return _read_resource(TEMPLATE_RESOURCE)


@lru_cache(maxsize=1)
def build_system_prompt() -> str:
    """Собрать системный промпт: инструкции плюс шаблон заметки.

    Шаблон — отдельный ресурс пакета; если маркер подстановки пропал,
    это ошибка сборки, а не повод отправить LLM неполный промпт.
    """
    prompt = load_summary_prompt()
    template = load_note_template()
    if TEMPLATE_PLACEHOLDER not in prompt:
        raise RuntimeError(f"В ресурсе {PROMPT_RESOURCE} нет маркера {TEMPLATE_PLACEHOLDER}")
    return prompt.replace(TEMPLATE_PLACEHOLDER, template).strip()


def build_user_prompt(transcript: str, *, title: str = "", channel: str = "") -> str:
    """Собрать пользовательское сообщение: метаданные и транскрипт в маркерах."""
    header: list[str] = []
    title_text = _one_line(title)
    channel_text = _one_line(channel)
    if title_text:
        header.append(f"Название видео: {title_text}")
    if channel_text:
        header.append(f"Канал: {channel_text}")
    body = transcript.strip()
    if header:
        return "\n".join([*header, "", "<transcript>", body, "</transcript>"])
    return "\n".join(["<transcript>", body, "</transcript>"])


def _read_resource(name: str) -> str:
    try:
        resource = resources.files(RESOURCES_PACKAGE).joinpath(name)
        return resource.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise RuntimeError(f"Не найден ресурс LLM-адаптера: {name}") from exc


def _parse_response(response: httpx.Response) -> VideoSummary:
    """Достать content из ответа chat/completions и разобрать его."""
    try:
        data = response.json()
    except ValueError as exc:
        log.warning("LLM: тело ответа не является JSON (%s)", type(exc).__name__)
        raise LLMResponseError() from exc

    if not isinstance(data, dict):
        log.warning("LLM: тело ответа — не объект, а %s", type(data).__name__)
        raise LLMResponseError()

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        log.warning("LLM: пустой ответ — нет choices")
        raise LLMResponseError()

    choice = choices[0]
    if not isinstance(choice, dict):
        log.warning("LLM: элемент choices — не объект")
        raise LLMResponseError()

    message = choice.get("message")
    if not isinstance(message, dict):
        log.warning("LLM: пустой ответ — нет message")
        raise LLMResponseError()

    text = message.get("content")
    if not isinstance(text, str) or not text.strip():
        log.warning("LLM: пустой ответ — content отсутствует или пуст")
        raise LLMResponseError()
    return parse_summary(text)


def _loads_json_object(content: str) -> JSON_MAPPING:
    """Разобрать JSON-объект; допускается одна markdown-обёртка вокруг него."""
    text = content.strip()
    if not text:
        _fail("пустой ответ")
    if text.startswith("```"):
        text = _strip_code_fence(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _fail(f"битый JSON: {exc.msg} (строка {exc.lineno}, колонка {exc.colno})")
    if not isinstance(data, dict):
        _fail(f"ожидается JSON-объект, получено {type(data).__name__}")
    return data


def _strip_code_fence(text: str) -> str:
    lines = text.splitlines()
    closing = next(
        (index for index in range(len(lines) - 1, 0, -1) if lines[index].strip() == "```"),
        None,
    )
    if closing is None:
        return text
    return "\n".join(lines[1:closing]).strip()


def _string_list(
    data: JSON_MAPPING,
    field: str,
    minimum: int,
    maximum: int,
) -> tuple[str, ...]:
    raw = data.get(field)
    if raw is None:
        _fail(f"нет обязательного поля {field!r}")
    if not isinstance(raw, list):
        _fail(f"поле {field!r} должно быть массивом строк")
    items: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, str):
            _fail(f"{field}[{index}] должно быть строкой")
        text = _one_line(value)
        if not text:
            _fail(f"{field}[{index}] пустое значение")
        items.append(text)
    _check_count(field, len(items), minimum, maximum)
    return tuple(items)


def _timeline(data: JSON_MAPPING) -> tuple[SummaryTimelineEntry, ...]:
    raw = data.get("timeline")
    if raw is None:
        _fail("нет обязательного поля 'timeline'")
    if not isinstance(raw, list):
        _fail("поле 'timeline' должно быть массивом объектов")
    if not raw:
        _fail("timeline: нужен хотя бы один смысловой блок")
    if len(raw) > MAX_TIMELINE_ENTRIES:
        _fail(f"timeline: не больше {MAX_TIMELINE_ENTRIES} смысловых блоков")

    entries: list[SummaryTimelineEntry] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            _fail(f"timeline[{index}] должно быть объектом")
        entries.append(
            SummaryTimelineEntry(
                timecode=_timecode(item.get("timecode"), f"timeline[{index}].timecode"),
                title=_required_text(item.get("title"), f"timeline[{index}].title"),
            )
        )
    return tuple(entries)


def _quotes(data: JSON_MAPPING) -> tuple[SummaryQuote, ...]:
    raw = data.get("quotes")
    if raw is None:
        _fail("нет обязательного поля 'quotes'")
    if not isinstance(raw, list):
        _fail("поле 'quotes' должно быть массивом объектов")
    _check_count("quotes", len(raw), MIN_QUOTES, MAX_QUOTES)

    quotes: list[SummaryQuote] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            _fail(f"quotes[{index}] должно быть объектом")
        quotes.append(
            SummaryQuote(
                text=_required_text(item.get("text"), f"quotes[{index}].text"),
                timecode=_timecode(item.get("timecode"), f"quotes[{index}].timecode"),
            )
        )
    return tuple(quotes)


def _timecode(value: object, where: str) -> str:
    """Проверить таймкод и привести его к виду ``MM:SS`` / ``HH:MM:SS``."""
    if isinstance(value, bool):
        _fail(f"{where}: таймкод должен быть строкой MM:SS или HH:MM:SS")
    if isinstance(value, int):
        if value < 0:
            _fail(f"{where}: таймкод не может быть отрицательным")
        return _format_seconds(value)
    if not isinstance(value, str):
        _fail(f"{where}: таймкод должен быть строкой MM:SS или HH:MM:SS")

    match = _TIMECODE_RE.fullmatch(value.strip())
    if match is None:
        _fail(f"{where}: неверный таймкод {value.strip()!r}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    if minutes > 59 or seconds > 59:
        _fail(f"{where}: минуты и секунды должны быть меньше 60")
    return _format_seconds(hours * 3600 + minutes * 60 + seconds)


def _format_seconds(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _required_text(value: object, where: str) -> str:
    if not isinstance(value, str):
        _fail(f"{where}: ожидается непустая строка")
    text = _one_line(value)
    if not text:
        _fail(f"{where}: пустое значение")
    return text


def _check_count(field: str, count: int, minimum: int, maximum: int) -> None:
    if not minimum <= count <= maximum:
        _fail(f"{field}: {count} элементов, нужно от {minimum} до {maximum}")


def _is_retryable(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


def _fail(reason: str) -> NoReturn:
    """Залогировать причину и поднять безопасную для пользователя ошибку."""
    log.warning("LLM: некорректный ответ — %s", reason)
    raise LLMResponseError()


def _one_line(value: str) -> str:
    return " ".join(value.split())


__all__ = [
    "CHAT_COMPLETIONS_PATH",
    "DEFAULT_BACKOFF_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "LLMClient",
    "LLMError",
    "LLMProviderError",
    "LLMResponseError",
    "LLMTimeoutError",
    "MAX_KEY_IDEAS",
    "MAX_QUOTES",
    "MAX_TIMELINE_ENTRIES",
    "MAX_TLDR",
    "MESSAGE_LLM_BAD_RESPONSE",
    "MESSAGE_LLM_TIMEOUT",
    "MESSAGE_LLM_UNAVAILABLE",
    "MIN_FOLLOW_UPS",
    "MIN_KEY_IDEAS",
    "MIN_QUOTES",
    "MIN_TLDR",
    "PROMPT_RESOURCE",
    "RESOURCES_PACKAGE",
    "SummaryQuote",
    "SummaryTimelineEntry",
    "TEMPLATE_RESOURCE",
    "VideoSummary",
    "build_system_prompt",
    "build_user_prompt",
    "load_note_template",
    "load_summary_prompt",
    "parse_summary",
]

"""Адаптер LLM: промпт конспекта, httpx-клиент и разбор JSON-ответа.

Модуль — единственная граница вызовов LLM (ADR-002): сервисы и обработчики
не импортируют ``httpx`` и не знают формат ответа провайдера. Контракт:

1. промпт и шаблон заметки — ресурсы пакета (``adapters/resources``), а не env;
   отсутствие ресурса или маркера подстановки — :class:`LLMConfigError`
   (наследник ``UserFacingError``), а не сырой ``RuntimeError``;
2. ключ, модель, базовый URL и таймаут приходят из :class:`tldw.config.Settings`;
3. запрос идёт в OpenAI-совместимый ``chat/completions`` с требованием JSON;
4. ответ строго валидируется по разделам шаблона **до** модели заметки:
   битый JSON, отсутствие обязательного поля, пустой ответ или нарушение
   количества элементов дают :class:`LLMError`, а не частичную заметку;
   для пользовательского шаблона проверяются ровно те же разделы в том же
   порядке и их лимиты;
5. таймаут и HTTP-ошибки провайдера поднимаются как :class:`LLMError`;
   ``httpx.InvalidURL``/``ValueError`` при создании клиента и запросе тоже
   оборачиваются, повтор ограничен кодами 429 и 5xx;
6. разбор описания шаблона человеческим языком
   (:meth:`LLMClient.parse_template`) — это **второй** сценарий того же
   клиента: один вызов LLM через общий приватный метод (тот же таймаут и
   повтор), промпт из ``template_parse_prompt.md``, на выходе нейтральный
   :class:`TemplatePatchDraft` с примитивами. Домен шаблона адаптер не
   импортирует: лимиты и слияние с текущим шаблоном проверяет
   :class:`tldw.services.template.TemplateService`.

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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Final, NoReturn

import httpx

from tldw.config import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    Settings,
    validate_http_url,
)
from tldw.errors import TemplateParseError, UserFacingError
from tldw.text import one_line

log = logging.getLogger(__name__)

#: Пакет с ресурсами промпта и шаблона.
RESOURCES_PACKAGE: Final = "tldw.adapters.resources"
#: Системный промпт: роль, жёсткая структура и JSON-схема ответа.
PROMPT_RESOURCE: Final = "summary_prompt.md"
#: Системный промпт пользовательского шаблона: разделы, тон и динамическая схема.
CUSTOM_PROMPT_RESOURCE: Final = "summary_prompt_custom.md"
#: Шаблон заметки, который подставляется в промпт.
TEMPLATE_RESOURCE: Final = "note_template.md"
#: Маркер подстановки шаблона в промпт.
TEMPLATE_PLACEHOLDER: Final = "{{NOTE_TEMPLATE}}"

#: Маркеры динамического промпта: разделы, тон, схема и её ограничения.
SECTIONS_PLACEHOLDER: Final = "{{SECTIONS}}"
TONE_PLACEHOLDER: Final = "{{TONE}}"
SCHEMA_PLACEHOLDER: Final = "{{RESPONSE_SCHEMA}}"
SECTION_COUNT_PLACEHOLDER: Final = "{{SECTION_COUNT}}"
SECTION_LIMITS_PLACEHOLDER: Final = "{{SECTION_LIMITS}}"
_DYNAMIC_PLACEHOLDERS: Final[tuple[str, ...]] = (
    SECTIONS_PLACEHOLDER,
    TONE_PLACEHOLDER,
    SCHEMA_PLACEHOLDER,
    SECTION_COUNT_PLACEHOLDER,
    SECTION_LIMITS_PLACEHOLDER,
)
#: Текст вместо пустого тона: тон остаётся данными, а не отсутствующим блоком.
TONE_NOT_SET: Final = "Тон не задан."

#: Системный промпт разбора описания шаблона человеческим языком (T-006).
TEMPLATE_PARSE_PROMPT_RESOURCE: Final = "template_parse_prompt.md"
#: Маркеры промпта разбора: текущий шаблон и лимиты нового.
CURRENT_SECTIONS_PLACEHOLDER: Final = "{{CURRENT_SECTIONS}}"
CURRENT_TONE_PLACEHOLDER: Final = "{{CURRENT_TONE}}"
TEMPLATE_LIMITS_PLACEHOLDER: Final = "{{TEMPLATE_LIMITS}}"
_TEMPLATE_PARSE_PLACEHOLDERS: Final[tuple[str, ...]] = (
    CURRENT_SECTIONS_PLACEHOLDER,
    CURRENT_TONE_PLACEHOLDER,
    TEMPLATE_LIMITS_PLACEHOLDER,
)
#: Маркеры промпта разбора подставляются за один проход, как и динамические.
_TEMPLATE_PARSE_PLACEHOLDER_RE: Final = re.compile(
    "|".join(re.escape(placeholder) for placeholder in _TEMPLATE_PARSE_PLACEHOLDERS)
)

#: Путь OpenAI-совместимого API относительно ``LLM_BASE_URL``.
CHAT_COMPLETIONS_PATH: Final = "chat/completions"
#: Ограничение на размер ответа: заметки хватает с запасом.
MAX_OUTPUT_TOKENS: Final = 4096
#: Патч шаблона короче заметки: списка разделов и тона хватает с запасом.
MAX_PATCH_OUTPUT_TOKENS: Final = 1024
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
MIN_TIMELINE_ENTRIES: Final = 1
MAX_TIMELINE_ENTRIES: Final = 30

#: Лимиты пунктов пользовательского раздела (FR-7); для встроенных заголовков
#: действуют прежние ограничения из :data:`BUILTIN_SECTION_LIMITS`.
MIN_CUSTOM_SECTION_ITEMS: Final = 1
MAX_CUSTOM_SECTION_ITEMS: Final = 10

#: Прежние лимиты количества пунктов для встроенных заголовков шаблона.
#:
#: Ключи совпадают с :data:`tldw.services.template.BUILTIN_SECTIONS`; адаптер
#: не импортирует ``services`` (ADR-002), поэтому расхождение ловит тест
#: ``test_llm_template.py``, а не импорт между слоями.
BUILTIN_SECTION_LIMITS: Final[Mapping[str, tuple[int, int]]] = {
    "TL;DR": (MIN_TLDR, MAX_TLDR),
    "Ключевые идеи": (MIN_KEY_IDEAS, MAX_KEY_IDEAS),
    "Таймкоды": (MIN_TIMELINE_ENTRIES, MAX_TIMELINE_ENTRIES),
    "Цитаты": (MIN_QUOTES, MAX_QUOTES),
    "Что проверить": (MIN_FOLLOW_UPS, MAX_FOLLOW_UPS),
}

#: Лимиты шаблона заметки из T-001 (FR-2/FR-5/FR-12).
#:
#: Копия констант домена: адаптер разбора описания не импортирует ``services``
#: (ADR-002). Расхождение ловит ``tests/test_template_parser.py``, сверяя
#: значения с :mod:`tldw.services.template`.
MIN_TEMPLATE_SECTIONS: Final = 1
MAX_TEMPLATE_SECTIONS: Final = 12
MAX_TEMPLATE_HEADING_LENGTH: Final = 80
MAX_TEMPLATE_TONE_LENGTH: Final = 1000
MIN_TEMPLATE_DESCRIPTION_LENGTH: Final = 1
MAX_TEMPLATE_DESCRIPTION_LENGTH: Final = 4000

MESSAGE_LLM_UNAVAILABLE: Final = "AI не ответил. Попробуйте позже."
MESSAGE_LLM_TIMEOUT: Final = "AI не ответил вовремя. Попробуйте позже."
MESSAGE_LLM_BAD_RESPONSE: Final = "AI вернул некорректный ответ. Попробуйте позже."
MESSAGE_LLM_CONFIG: Final = (
    "AI временно недоступен из-за ошибки настройки. Попробуйте позже."
)

#: Сообщения разбора описания шаблона (T-006) — на русском, без текста шаблона.
MESSAGE_TEMPLATE_DESCRIPTION_RANGE: Final = (
    f"Опишите изменения шаблона текстом от {MIN_TEMPLATE_DESCRIPTION_LENGTH} "
    f"до {MAX_TEMPLATE_DESCRIPTION_LENGTH} символов."
)
MESSAGE_TEMPLATE_PATCH_INVALID: Final = (
    "Не удалось разобрать описание изменений. Попробуйте переформулировать."
)
MESSAGE_TEMPLATE_PATCH_EMPTY: Final = (
    "В описании не нашлось изменений разделов или тона. "
    "Опишите, что именно изменить в шаблоне."
)

#: Таймкод ``MM:SS`` или ``HH:MM:SS``; секунды всегда две цифры.
_TIMECODE_RE: Final = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")
#: Маркеры динамического промпта: подставляются за один проход, чтобы текст
#: пользователя не мог подменить другой маркер.
_DYNAMIC_PLACEHOLDER_RE: Final = re.compile(
    "|".join(re.escape(placeholder) for placeholder in _DYNAMIC_PLACEHOLDERS)
)

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


class LLMConfigError(LLMError):
    """Не найден ресурс промпта или в нём нет обязательного маркера подстановки.

    Это ошибка сборки/упаковки, а не ответа провайдера, но пользователь должен
    получить понятный текст, а обработчик — типизированную ``UserFacingError``,
    поэтому ошибка наследуется от :class:`LLMError`, а не от ``RuntimeError``.
    """

    def __init__(self, message: str = MESSAGE_LLM_CONFIG) -> None:
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
class SummarySection:
    """Раздел пользовательского шаблона в ответе LLM.

    ``heading`` — точное название раздела из шаблона (в порядке применения),
    ``items`` — непустые пункты раздела. Для встроенных заголовков количество
    пунктов ограничено прежними лимитами (:data:`BUILTIN_SECTION_LIMITS`),
    для остальных — :data:`MIN_CUSTOM_SECTION_ITEMS`–:data:`MAX_CUSTOM_SECTION_ITEMS`.
    """

    heading: str
    items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VideoSummary:
    """Провалидированный ответ LLM по разделам шаблона.

    Это ещё не модель заметки: frontmatter (title, source, channel, duration,
    date, tags, status) собирает сервис конспекта из метаданных видео.

    Встроенный путь (``summarize`` без ``sections``) заполняет пять прежних
    полей и оставляет :attr:`sections` пустым; пользовательский шаблон
    заполняет :attr:`sections` в порядке применения, а прежние поля остаются
    пустыми — они не имеют смысла для произвольных разделов.
    """

    tldr: tuple[str, ...] = ()
    key_ideas: tuple[str, ...] = ()
    timeline: tuple[SummaryTimelineEntry, ...] = ()
    quotes: tuple[SummaryQuote, ...] = ()
    follow_ups: tuple[str, ...] = ()
    sections: tuple[SummarySection, ...] = ()


@dataclass(frozen=True, slots=True)
class TemplatePatchDraft:
    """Нейтральная правка шаблона из разбора описания человеческим языком.

    Это ещё не доменный патч: адаптер не знает модель шаблона и не импортирует
    ``services`` (ADR-002). Здесь только примитивы:

    * ``sections`` — **полный** новый список разделов в порядке применения;
      ``None`` — «не менять разделы»;
    * ``tone`` — новый тон; ``None`` — «не менять тон», пустая строка —
      осознанная очистка тона.

    Лимиты разделов и тона проверяет
    :meth:`tldw.services.template.TemplateService.candidate_from_parse`, а
    слияние с текущим шаблоном — :func:`tldw.services.template.merge_template`.
    """

    sections: tuple[str, ...] | None = None
    tone: str | None = None


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
        base = validate_http_url(base_url, name="LLM_BASE_URL")
        if not key:
            raise ValueError("LLM_API_KEY не задан")
        if not model_name:
            raise ValueError("LLM_MODEL не задан")
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
        sections: Sequence[str] | None = None,
        tone: str = "",
    ) -> VideoSummary:
        """Отправить транскрипт в LLM и вернуть провалидированный конспект.

        :param transcript: текст с таймкодами (склейку делает сервис конспекта).
        :param title: название видео для контекста промпта.
        :param channel: канал видео для контекста промпта.
        :param sections: заголовки пользовательского шаблона в порядке
            применения. ``None`` (по умолчанию) — встроенный шаблон MVP с
            прежним промптом и прежним разбором; пустая последовательность
            равнозначна ``None``.
        :param tone: тон пользовательского шаблона. Передаётся в промпт как
            данные и приоритетнее встроенного указания «пиши на русском»;
            без указания языка заметка остаётся русской. На встроенном пути
            игнорируется.
        :raises ValueError: если ``sections`` — не последовательность строк или
            ``tone`` — не строка (это ошибка вызывающего кода, а не ответа LLM).
        :raises LLMTimeoutError: провайдер не ответил за таймаут.
        :raises LLMProviderError: HTTP-ошибка, сетевой сбой или исчерпаны повторы.
        :raises LLMResponseError: пустой, битый или неполный ответ провайдера.
        """
        text = transcript.strip()
        if not text:
            log.warning("LLM: пустой транскрипт — конспект не запрашивается")
            raise LLMError("Нет текста для конспекта.")

        requested = _requested_sections(sections)
        if requested is None:
            system_prompt = build_system_prompt()
        else:
            system_prompt = _render_custom_prompt(requested, _tone_text(tone))
            log.info("LLM: конспект по пользовательскому шаблону (%s разделов)", len(requested))

        payload = _chat_payload(
            self.model,
            system_prompt,
            build_user_prompt(text, title=title, channel=channel),
            max_tokens=MAX_OUTPUT_TOKENS,
        )

        return parse_summary(self._complete(payload), sections=requested)

    def parse_template(
        self,
        text: str,
        *,
        current_sections: Sequence[str],
        current_tone: str = "",
    ) -> TemplatePatchDraft:
        """Разобрать описание шаблона человеческим языком в нейтральный патч.

        Ровно один вызов LLM через общий приватный метод :meth:`_complete`:
        тот же таймаут, тот же повтор только на 429/5xx и те же
        :class:`LLMError`. Сеть YouTube не используется.

        :param text: описание изменений от пользователя, 1–4000 символов.
        :param current_sections: разделы текущего активного шаблона
            примитивами — только заголовки, без доменной модели.
        :param current_tone: тон текущего шаблона; пустая строка — «не задан».
        :raises TemplateParseError: если описание пустое или длиннее лимита, а
            также если response пуст, битый JSON, нет ни разделов, ни тона или
            нарушена структура полей.
        :raises ValueError: если ``text``/``current_sections``/``current_tone``
            не того типа (ошибка вызывающего кода, а не ответа LLM).
        :raises LLMTimeoutError: провайдер не ответил за таймаут.
        :raises LLMProviderError: HTTP-ошибка, сетевой сбой или исчерпаны повторы.
        """
        description = _template_description(text)
        headings = _template_current_sections(current_sections)
        tone = _template_current_tone(current_tone)
        payload = _chat_payload(
            self.model,
            build_template_parse_prompt(headings, tone),
            build_template_user_prompt(description),
            max_tokens=MAX_PATCH_OUTPUT_TOKENS,
        )

        draft = parse_template_patch(self._complete(payload))
        log.info(
            "LLM: патч шаблона разобран (%s)",
            _patch_summary(draft),
        )
        return draft

    def _complete(self, payload: JSON_MAPPING) -> str:
        """Один логический запрос к провайдеру: таймаут, повтор, ``LLMError``.

        Общий приватный метод всех сценариев клиента: HTTP-логика, повтор
        только на 429/5xx и преобразование сбоев в :class:`LLMError` живут
        здесь, поэтому разбор шаблона (T-006) не дублирует их. Возвращает
        ``content`` первого choice — уже текст ответа модели.

        Создание клиента и запрос обёрнуты одинаково: ``httpx.InvalidURL`` не
        наследуется от ``httpx.HTTPError``, а ``ValueError`` может прийти из
        разбора адреса/заголовков, поэтому оба превращаются в
        :class:`LLMProviderError`. В лог пишется только тип исключения: текст
        httpx может содержать URL с query и ключом.
        """
        try:
            client = self._make_client()
        except (httpx.InvalidURL, ValueError) as exc:
            log.warning("LLM: некорректный URL клиента (%s)", type(exc).__name__)
            raise LLMProviderError() from exc

        with client:
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
                except (httpx.InvalidURL, ValueError) as exc:
                    log.warning(
                        "LLM: некорректный URL запроса (попытка %s/%s, %s)",
                        attempt,
                        self.max_attempts,
                        type(exc).__name__,
                    )
                    raise LLMProviderError() from exc
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
                        "LLM: HTTP %s, повтор через %.1f с (попытка %s/%s)",
                        response.status_code,
                        delay,
                        attempt,
                        self.max_attempts,
                    )
                    self._sleep(delay)
                    continue

                if response.status_code >= 400:
                    log.error(
                        "LLM: провайдер ответил HTTP %s (попытка %s/%s)",
                        response.status_code,
                        attempt,
                        self.max_attempts,
                    )
                    raise LLMProviderError()

                return _completion_content(response)

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


def parse_summary(content: str, *, sections: Sequence[str] | None = None) -> VideoSummary:
    """Разобрать JSON-ответ LLM и провалидировать его по шаблону.

    Лишние поля провайдера игнорируются: обязательные разделы проверяются
    ниже, а необязательный мусор не должен ломать заметку.

    :param sections: заголовки пользовательского шаблона в порядке применения.
        ``None`` (по умолчанию) — прежний встроенный разбор по пяти полям;
        иначе ответ должен содержать ``{"sections": [{"heading", "items"}]}``
        ровно с этими разделами в этом порядке.
    :raises LLMResponseError: если ответ пуст, не JSON, не объект, не содержит
        обязательного поля, нарушает количество элементов раздела или набор
        и порядок разделов пользовательского шаблона.
    :raises ValueError: если ``sections`` — не последовательность непустых
        уникальных строк (ошибка вызывающего кода, а не ответа LLM).
    """
    requested = _requested_sections(sections)
    data = _loads_json_object(content)
    if requested is not None:
        return VideoSummary(sections=_summary_sections(data, requested))
    return VideoSummary(
        tldr=_string_list(data, "tldr", MIN_TLDR, MAX_TLDR),
        key_ideas=_string_list(data, "key_ideas", MIN_KEY_IDEAS, MAX_KEY_IDEAS),
        timeline=_timeline(data),
        quotes=_quotes(data),
        follow_ups=_string_list(data, "follow_ups", MIN_FOLLOW_UPS, MAX_FOLLOW_UPS),
    )


def parse_template_patch(content: str) -> TemplatePatchDraft:
    """Разобрать JSON-патч шаблона из ответа LLM в нейтральные примитивы.

    Ожидается объект ``{"sections": [...] | null, "tone": "..." | null}``:

    * ``null`` и отсутствие поля означают «не менять эту часть»;
    * пустая строка тона — осознанная очистка тона;
    * ``sections`` — **полный** новый список заголовков в порядке применения.

    Лимиты разделов и тона здесь не проверяются: это работа домена шаблона
    (T-001), который адаптер не импортирует (ADR-002). Структура ответа
    проверяется строго: мусор вместо строк — это ошибка разбора, а не данных.

    :raises TemplateParseError: если ответ пуст, не JSON, не объект, не
        содержит ни разделов, ни тона или поля не того типа.
    """
    data = _loads_json_object(content, fail=_template_fail)
    try:
        sections = _patch_sections(data)
        tone = _patch_tone(data)
    except TemplateParseError:
        # Причина уже в логе; добавляем только число разделов, без их текста.
        log.warning(
            "LLM: патч шаблона отклонён (разделов в ответе: %s)",
            _raw_section_count(data),
        )
        raise
    if sections is None and tone is None:
        log.warning("LLM: патч шаблона пуст — ни разделов, ни тона (изменений нет)")
        raise TemplateParseError(MESSAGE_TEMPLATE_PATCH_EMPTY)
    return TemplatePatchDraft(sections=sections, tone=tone)


def _raw_section_count(data: JSON_MAPPING) -> str:
    """Сколько разделов пришло в ответе — для лога, без самих заголовков."""
    raw = data.get("sections")
    if not isinstance(raw, list):
        return "нет"
    return str(len(raw))


def _patch_sections(data: JSON_MAPPING) -> tuple[str, ...] | None:
    """Извлечь новый список разделов: ``None`` — «не менять»."""
    raw = data.get("sections")
    if raw is None:
        return None
    if not isinstance(raw, list):
        _template_fail("поле 'sections' должно быть массивом строк или null")
    sections: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, str):
            _template_fail(f"sections[{index}] должно быть строкой")
        heading = one_line(value)
        if not heading:
            _template_fail(f"sections[{index}] — пустой заголовок")
        sections.append(heading)
    return tuple(sections)


def _patch_tone(data: JSON_MAPPING) -> str | None:
    """Извлечь новый тон: ``None`` — «не менять», пустая строка — «очистить»."""
    raw = data.get("tone")
    if raw is None:
        return None
    if not isinstance(raw, str):
        _template_fail("поле 'tone' должно быть строкой или null")
    return one_line(raw)


def _template_fail(reason: str) -> NoReturn:
    """Залогировать причину разбора патча и поднять безопасную ошибку.

    В лог попадают только причина и число разделов: текст описания, тон и
    JSON ответа не логируются (FR-12).
    """
    log.warning("LLM: некорректный патч шаблона — %s", reason)
    raise TemplateParseError(MESSAGE_TEMPLATE_PATCH_INVALID)


def _patch_summary(draft: TemplatePatchDraft) -> str:
    """Строка для лога: число разделов и факт изменения тона, без их текста."""
    if draft.sections is None:
        sections = "разделы без изменений"
    else:
        sections = f"разделов {len(draft.sections)}"
    tone = "тон без изменений" if draft.tone is None else "тон изменён"
    return f"{sections}, {tone}"




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
        log.error(
            "LLM: в ресурсе %s нет маркера подстановки %s",
            PROMPT_RESOURCE,
            TEMPLATE_PLACEHOLDER,
        )
        raise LLMConfigError()
    return prompt.replace(TEMPLATE_PLACEHOLDER, template).strip()


def build_custom_system_prompt(sections: Sequence[str], tone: str = "") -> str:
    """Собрать системный промпт пользовательского шаблона.

    Промпт содержит точные названия разделов и их порядок, тон и динамическую
    JSON-схему ответа ``{"sections": [{"heading": ..., "items": [...]}]}``.
    Разделы и тон передаются как **данные** внутри блоков ``<sections>`` и
    ``<tone>``: угловые скобки в пользовательском тексте заменяются, чтобы
    текст не мог закрыть блок и стать инструкцией.

    Тон приоритетнее встроенного «пиши на русском»; если тон не задаёт язык,
    промпт требует русскую заметку.

    :raises ValueError: если ``sections`` — не последовательность непустых
        уникальных строк, а ``tone`` — не строка.
    :raises LLMConfigError: если ресурс промпта потерял маркер подстановки.
    """
    heading_list = _requested_sections(sections)
    if heading_list is None:
        raise ValueError("sections должен содержать хотя бы один раздел")
    return _render_custom_prompt(heading_list, _tone_text(tone))


def build_user_prompt(transcript: str, *, title: str = "", channel: str = "") -> str:
    """Собрать пользовательское сообщение: метаданные и транскрипт в маркерах.

    ``title``, ``channel`` и транскрипт — данные, а не инструкции. Название и
    канал схлопываются в одну строку, а угловые скобки в них обезвреживаются
    тем же :func:`_data_text`, что и в шаблоне: метаданные не могут закрыть
    блок ``<transcript>`` и подменить промпт. В самом транскрипте нейтрализуются
    только маркеры ``<transcript>``/``</transcript>`` — остальной текст
    сохраняется, поэтому внедрение из субтитров не ломает структуру (T-016).
    """
    header: list[str] = []
    title_text = _data_text(one_line(title))
    channel_text = _data_text(one_line(channel))
    if title_text:
        header.append(f"Название видео: {title_text}")
    if channel_text:
        header.append(f"Канал: {channel_text}")
    body = _escape_transcript_markers(transcript.strip())
    if header:
        return "\n".join([*header, "", "<transcript>", body, "</transcript>"])
    return "\n".join(["<transcript>", body, "</transcript>"])


def build_template_parse_prompt(
    current_sections: Sequence[str] = (),
    current_tone: str = "",
) -> str:
    """Собрать системный промпт разбора описания шаблона (T-006).

    Промпт содержит текущий шаблон (разделы примитивами и тон), требование
    вернуть JSON-патч ``{"sections": [...] | null, "tone": "..." | null}`` и
    правило «текст — данные, не инструкции». Текущие разделы и тон
    подставляются как данные: угловые скобки в них обезвреживаются, поэтому
    они не могут закрыть блок и стать инструкцией.

    :raises ValueError: если ``current_sections`` — не последовательность
        непустых строк, а ``current_tone`` — не строка.
    :raises LLMConfigError: если ресурс промпта потерял маркер подстановки.
    """
    headings = _template_current_sections(current_sections)
    tone = _template_current_tone(current_tone)
    template = _read_resource(TEMPLATE_PARSE_PROMPT_RESOURCE)
    values = {
        CURRENT_SECTIONS_PLACEHOLDER: _template_sections_block(headings),
        CURRENT_TONE_PLACEHOLDER: _data_text(tone) if tone else TONE_NOT_SET,
        TEMPLATE_LIMITS_PLACEHOLDER: _template_limits_note(),
    }
    missing = [placeholder for placeholder in values if placeholder not in template]
    if missing:
        log.error(
            "LLM: в ресурсе %s нет маркеров: %s",
            TEMPLATE_PARSE_PROMPT_RESOURCE,
            ", ".join(missing),
        )
        raise LLMConfigError()
    rendered = _TEMPLATE_PARSE_PLACEHOLDER_RE.sub(lambda match: values[match.group()], template)
    return re.sub(r"\n{3,}", "\n\n", rendered).strip()


def build_template_user_prompt(description: str) -> str:
    """Собрать пользовательское сообщение разбора: описание в маркерах данных.

    Описание — данные, а не инструкции: угловые скобки обезвреживаются, чтобы
    текст не мог закрыть блок ``<description>`` и подменить промпт.
    """
    return "\n".join(["<description>", _data_text(description.strip()), "</description>"])


def _template_description(value: object) -> str:
    """Проверить текст описания: строка длиной 1–4000 символов.

    :raises ValueError: если текст не строка (ошибка вызывающего кода).
    :raises TemplateParseError: если описание пустое или слишком длинное.
    """
    if not isinstance(value, str):
        raise ValueError("text должен быть строкой")
    description = value.strip()
    if not MIN_TEMPLATE_DESCRIPTION_LENGTH <= len(description) <= MAX_TEMPLATE_DESCRIPTION_LENGTH:
        raise TemplateParseError(MESSAGE_TEMPLATE_DESCRIPTION_RANGE)
    return description


def _template_current_sections(sections: object) -> tuple[str, ...]:
    """Проверить текущие разделы (примитивы) и нормализовать их для промпта."""
    if isinstance(sections, (str, bytes)) or not isinstance(sections, Sequence):
        raise ValueError("current_sections должен быть последовательностью заголовков")
    headings: list[str] = []
    for heading in sections:
        if not isinstance(heading, str):
            raise ValueError("заголовок текущего шаблона должен быть строкой")
        text = one_line(heading)
        if not text:
            raise ValueError("заголовок текущего шаблона не может быть пустым")
        headings.append(text)
    return tuple(headings)


def _template_current_tone(tone: object) -> str:
    """Проверить текущий тон (примитив) и нормализовать его для промпта."""
    if not isinstance(tone, str):
        raise ValueError("current_tone должен быть строкой")
    return one_line(tone)


def _template_sections_block(headings: tuple[str, ...]) -> str:
    """Текущие разделы: нумерованный список как данные, без ``##``."""
    if not headings:
        return "(разделы не заданы)"
    return "\n".join(
        f"{index}. {_data_text(heading)}" for index, heading in enumerate(headings, start=1)
    )


def _template_limits_note() -> str:
    """Лимиты нового шаблона из T-001 — строкой для промпта разбора."""
    return (
        f"- В шаблоне от {MIN_TEMPLATE_SECTIONS} до {MAX_TEMPLATE_SECTIONS} разделов, "
        f"заголовок до {MAX_TEMPLATE_HEADING_LENGTH} символов, "
        f"тон до {MAX_TEMPLATE_TONE_LENGTH} символов.\n"
        "- Заголовок — одна строка без `##`, без `---` и YAML frontmatter; "
        "заголовки не повторяются, даже в другом регистре."
    )


def _chat_payload(
    model: str,
    system_prompt: str,
    user_content: str,
    *,
    max_tokens: int,
) -> dict[str, Any]:
    """Тело запроса chat/completions: общий формат конспекта и разбора шаблона."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
    }


def _requested_sections(sections: object) -> tuple[str, ...] | None:
    """Проверить разделы пользовательского шаблона и нормализовать заголовки.

    ``None`` и пустая последовательность — встроенный путь. Это единственная
    точка нормализации для промпта и разбора, поэтому они не расходятся.
    """
    if sections is None:
        return None
    if isinstance(sections, (str, bytes)) or not isinstance(sections, Sequence):
        raise ValueError("sections должен быть последовательностью заголовков")
    normalized: list[str] = []
    seen: set[str] = set()
    for heading in sections:
        if not isinstance(heading, str):
            raise ValueError("заголовок раздела должен быть строкой")
        text = one_line(heading)
        if not text:
            raise ValueError("заголовок раздела не может быть пустым")
        key = text.casefold()
        if key in seen:
            raise ValueError(f"заголовок раздела повторяется: {text!r}")
        seen.add(key)
        normalized.append(text)
    return tuple(normalized) or None


def _tone_text(tone: object) -> str:
    """Проверить и нормализовать тон пользовательского шаблона."""
    if not isinstance(tone, str):
        raise ValueError("tone должен быть строкой")
    return one_line(tone)


def _render_custom_prompt(headings: tuple[str, ...], tone: str) -> str:
    """Подставить разделы, тон и схему в ресурс динамического промпта.

    Подстановка идёт одним проходом по маркерам: текст пользователя не может
    подменить собою другой маркер, а недостающий маркер ресурса — ошибка
    сборки, а не повод отправить LLM неполный промпт.
    """
    template = _read_resource(CUSTOM_PROMPT_RESOURCE)
    values = {
        SECTIONS_PLACEHOLDER: _sections_block(headings),
        TONE_PLACEHOLDER: _data_text(tone) if tone else TONE_NOT_SET,
        SCHEMA_PLACEHOLDER: _response_schema(headings),
        SECTION_COUNT_PLACEHOLDER: str(len(headings)),
        SECTION_LIMITS_PLACEHOLDER: _section_limits_note(headings),
    }
    missing = [placeholder for placeholder in values if placeholder not in template]
    if missing:
        log.error(
            "LLM: в ресурсе %s нет маркеров: %s",
            CUSTOM_PROMPT_RESOURCE,
            ", ".join(missing),
        )
        raise LLMConfigError()
    rendered = _DYNAMIC_PLACEHOLDER_RE.sub(lambda match: values[match.group()], template)
    # Пустая подстановка (нет встроенных заголовков) не должна оставлять
    # пустую строку внутри списка ограничений.
    return re.sub(r"\n{3,}", "\n\n", rendered).strip()


def _sections_block(headings: tuple[str, ...]) -> str:
    """Список разделов: точные названия и порядок применения, как данные."""
    return "\n".join(
        f"{index}. {_data_text(heading)}" for index, heading in enumerate(headings, start=1)
    )


def _response_schema(headings: tuple[str, ...]) -> str:
    """Динамическая JSON-схема ответа с точными заголовками разделов."""
    schema = {
        "sections": [
            {"heading": _data_text(heading), "items": ["пункт 1", "пункт 2", "пункт 3"]}
            for heading in headings
        ]
    }
    return json.dumps(schema, ensure_ascii=False, indent=2)


def _section_limits_note(headings: tuple[str, ...]) -> str:
    """Строка с прежними лимитами для встроенных заголовков, если они нужны."""
    limits: list[str] = []
    for heading in headings:
        builtin = _builtin_limits(heading)
        if builtin is None:
            continue
        minimum, maximum = builtin
        limits.append(f"«{_data_text(heading)}» — {minimum}–{maximum}")
    if not limits:
        return ""
    return (
        "- Для встроенных разделов действуют прежние лимиты количества: "
        + ", ".join(limits)
        + "."
    )


def _builtin_limits(heading: str) -> tuple[int, int] | None:
    """Лимиты пунктов для встроенного заголовка или ``None`` для своего."""
    key = heading.casefold()
    for builtin_heading, limits in BUILTIN_SECTION_LIMITS.items():
        if builtin_heading.casefold() == key:
            return limits
    return None


def _data_text(value: str) -> str:
    """Обезвредить маркеры блоков в пользовательском тексте.

    Угловые скобки заменяются на похожие символы, поэтому тон или заголовок
    с ``</tone>`` не может закрыть блок данных и превратиться в инструкцию.
    """
    return value.replace("<", "‹").replace(">", "›")


#: Маркер блока транскрипта в пользовательском сообщении.
_TRANSCRIPT_MARKER_RE: Final = re.compile(r"(?i)<\s*/?\s*transcript\s*>")


def _escape_transcript_markers(value: str) -> str:
    """Обезвредить только маркеры ``<transcript>``/``</transcript>``.

    Остальной текст транскрипта сохраняется: таймкоды, код и угловые скобки в
    обычном тексте не меняются. Инъекция вида ``</transcript>…`` становится
    данными и не может закрыть блок (T-016).
    """
    return _TRANSCRIPT_MARKER_RE.sub(lambda match: _data_text(match.group(0)), value)


def _summary_sections(
    data: JSON_MAPPING,
    headings: tuple[str, ...],
) -> tuple[SummarySection, ...]:
    """Проверить динамическую схему: те же разделы, тот же порядок, лимиты."""
    raw = data.get("sections")
    if raw is None:
        _fail("нет обязательного поля 'sections'")
    if not isinstance(raw, list):
        _fail("поле 'sections' должно быть массивом объектов")
    if len(raw) != len(headings):
        _fail(f"sections: {len(raw)} разделов, ожидается ровно {len(headings)}")

    result: list[SummarySection] = []
    for index, (item, heading) in enumerate(zip(raw, headings, strict=True)):
        if not isinstance(item, dict):
            _fail(f"sections[{index}] должно быть объектом")
        _check_section_heading(item.get("heading"), heading, index)
        result.append(
            SummarySection(
                heading=heading,
                items=_dynamic_items(item.get("items"), heading, index),
            )
        )
    return tuple(result)


def _check_section_heading(value: object, expected: str, index: int) -> None:
    """Заголовок ответа должен совпасть с шаблоном (без учёта регистра).

    В промпте угловые скобки заголовка обезврежены (:func:`_data_text`),
    поэтому принимаются оба написания: исходное и обезвреженное.

    Причина несовпадения не содержит ни ожидаемого заголовка из шаблона
    пользователя, ни полученного от LLM: в лог попадает только индекс
    раздела (FR-12, T-015). Этого достаточно, чтобы найти ответ модели, а
    текст шаблона и ответа остаётся вне лога.
    """
    if not isinstance(value, str):
        _fail(f"sections[{index}].heading должно быть строкой")
    actual = one_line(value).casefold()
    accepted = {expected.casefold(), _data_text(expected).casefold()}
    if actual not in accepted:
        _fail(f"sections[{index}].heading не совпал с шаблоном")


def _dynamic_items(value: object, heading: str, index: int) -> tuple[str, ...]:
    """Проверить пункты раздела и применить лимиты его заголовка."""
    if not isinstance(value, list):
        _fail(f"sections[{index}].items должно быть массивом строк")
    items: list[str] = []
    for item_index, raw_item in enumerate(value):
        if not isinstance(raw_item, str):
            _fail(f"sections[{index}].items[{item_index}] должно быть строкой")
        text = one_line(raw_item)
        if not text:
            _fail(f"sections[{index}].items[{item_index}] пустое значение")
        items.append(text)
    minimum, maximum = _builtin_limits(heading) or (
        MIN_CUSTOM_SECTION_ITEMS,
        MAX_CUSTOM_SECTION_ITEMS,
    )
    _check_count(f"sections[{index}].items", len(items), minimum, maximum)
    return tuple(items)


def _read_resource(name: str) -> str:
    """Прочитать ресурс пакета; отсутствие файла — типизированная LLMError.

    ``RuntimeError`` здесь был бы сырым исключением: он вылетал бы мимо
    контракта ``LLMError``/``UserFacingError``. Поэтому ошибка упаковки
    превращается в :class:`LLMConfigError`, а в лог попадает только имя
    ресурса и тип исключения.
    """
    try:
        resource = resources.files(RESOURCES_PACKAGE).joinpath(name)
        return resource.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        log.error("LLM: не найден ресурс адаптера %s (%s)", name, type(exc).__name__)
        raise LLMConfigError() from exc


def _completion_content(response: httpx.Response) -> str:
    """Достать content первого choice из ответа chat/completions."""
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
    return text


def _loads_json_object(
    content: str,
    *,
    fail: Callable[[str], NoReturn] | None = None,
) -> JSON_MAPPING:
    """Разобрать JSON-объект; допускается одна markdown-обёртка вокруг него.

    ``fail`` задаёт слой ошибки: конспект поднимает :class:`LLMResponseError`,
    разбор описания шаблона — :class:`TemplateParseError` (T-006). Логика
    разбора при этом одна.
    """
    if fail is None:
        fail = _fail
    text = content.strip()
    if not text:
        fail("пустой ответ")
    if text.startswith("```"):
        text = _strip_code_fence(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        fail(f"битый JSON: {exc.msg} (строка {exc.lineno}, колонка {exc.colno})")
    if not isinstance(data, dict):
        fail(f"ожидается JSON-объект, получено {type(data).__name__}")
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
        text = one_line(value)
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
    text = one_line(value)
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


__all__ = [
    "BUILTIN_SECTION_LIMITS",
    "CHAT_COMPLETIONS_PATH",
    "CURRENT_SECTIONS_PLACEHOLDER",
    "CURRENT_TONE_PLACEHOLDER",
    "CUSTOM_PROMPT_RESOURCE",
    "DEFAULT_BACKOFF_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "LLMClient",
    "LLMConfigError",
    "LLMError",
    "LLMProviderError",
    "LLMResponseError",
    "LLMTimeoutError",
    "MAX_CUSTOM_SECTION_ITEMS",
    "MAX_FOLLOW_UPS",
    "MAX_KEY_IDEAS",
    "MAX_PATCH_OUTPUT_TOKENS",
    "MAX_QUOTES",
    "MAX_TEMPLATE_DESCRIPTION_LENGTH",
    "MAX_TEMPLATE_HEADING_LENGTH",
    "MAX_TEMPLATE_SECTIONS",
    "MAX_TEMPLATE_TONE_LENGTH",
    "MAX_TIMELINE_ENTRIES",
    "MAX_TLDR",
    "MESSAGE_LLM_BAD_RESPONSE",
    "MESSAGE_LLM_CONFIG",
    "MESSAGE_LLM_TIMEOUT",
    "MESSAGE_LLM_UNAVAILABLE",
    "MESSAGE_TEMPLATE_DESCRIPTION_RANGE",
    "MESSAGE_TEMPLATE_PATCH_EMPTY",
    "MESSAGE_TEMPLATE_PATCH_INVALID",
    "MIN_CUSTOM_SECTION_ITEMS",
    "MIN_FOLLOW_UPS",
    "MIN_KEY_IDEAS",
    "MIN_QUOTES",
    "MIN_TEMPLATE_DESCRIPTION_LENGTH",
    "MIN_TLDR",
    "PROMPT_RESOURCE",
    "RESOURCES_PACKAGE",
    "SECTION_COUNT_PLACEHOLDER",
    "SECTION_LIMITS_PLACEHOLDER",
    "SECTIONS_PLACEHOLDER",
    "SCHEMA_PLACEHOLDER",
    "SummaryQuote",
    "SummarySection",
    "SummaryTimelineEntry",
    "TEMPLATE_LIMITS_PLACEHOLDER",
    "TEMPLATE_PARSE_PROMPT_RESOURCE",
    "TEMPLATE_RESOURCE",
    "TONE_NOT_SET",
    "TONE_PLACEHOLDER",
    "TemplatePatchDraft",
    "VideoSummary",
    "build_custom_system_prompt",
    "build_system_prompt",
    "build_template_parse_prompt",
    "build_template_user_prompt",
    "build_user_prompt",
    "load_note_template",
    "load_summary_prompt",
    "parse_summary",
    "parse_template_patch",
]

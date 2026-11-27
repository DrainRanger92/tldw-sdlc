"""Адаптер распознавания речи: граница голосового ввода (T-011).

Модуль — единственное место, где приложение обращается к сервису распознавания
речи (ADR-002): обработчики и сервисы не импортируют ``httpx`` и не знают
формат ответа провайдера. Контракт:

1. ключ, базовый URL, модель и таймаут приходят из :class:`tldw.config.Settings`,
   то есть из **необязательных** env (``SPEECH_API_KEY``, ``SPEECH_BASE_URL``,
   ``SPEECH_MODEL``, ``SPEECH_TIMEOUT_SECONDS``); текстовый сценарий работает
   без них;
2. если ключ или модель не заданы, :class:`SpeechNotConfiguredError` сообщает
   пользователю, что голосовой ввод недоступен, — сеть не используется;
3. длительность голосового сообщения ограничена константой
   :data:`MAX_AUDIO_DURATION_SECONDS` (60 секунд, FR-11); превышение даёт
   :class:`SpeechTooLongError` до любого сетевого вызова;
4. ровно **одна** попытка запроса: отправляется ``audio/transcriptions``
   OpenAI-совместимого API с файлом аудио и моделью; повторов нет;
5. HTTP-ошибка, таймаут, сетевой сбой и пустой ответ превращаются в
   типизированные :class:`SpeechError` — наследники
   :class:`tldw.errors.UserFacingError`, поэтому их сообщение безопасно
   показывать пользователю;
6. аудио и полный текст распознавания в лог не попадают: пишутся только
   длительность, число символов, код ответа и тип исключения.

Адаптер синхронный: вызывающий слой уводит его в отдельный поток, чтобы не
блокировать event loop Telegram-бота. Тесты подменяют транспорт
``httpx.MockTransport`` и не ходят в сеть.
"""

from __future__ import annotations

import logging
import math
from typing import Final

import httpx

from tldw.config import (
    DEFAULT_SPEECH_BASE_URL,
    DEFAULT_SPEECH_TIMEOUT_SECONDS,
    MAX_AUDIO_DURATION_SECONDS,
    Settings,
)
from tldw.errors import UserFacingError

log = logging.getLogger(__name__)

#: Путь OpenAI-совместимого API распознавания относительно ``SPEECH_BASE_URL``.
TRANSCRIPTIONS_PATH: Final = "audio/transcriptions"
#: Имя файла по умолчанию: голосовые сообщения Telegram — OGG/Opus.
DEFAULT_AUDIO_FILENAME: Final = "audio.ogg"
#: MIME-тип аудио по умолчанию.
DEFAULT_AUDIO_MEDIA_TYPE: Final = "audio/ogg"

MESSAGE_SPEECH_UNAVAILABLE: Final = "Не удалось распознать речь. Попробуйте позже."
MESSAGE_SPEECH_NOT_CONFIGURED: Final = (
    "Голосовой ввод недоступен: распознавание речи не настроено. "
    "Отправьте описание текстом."
)
MESSAGE_SPEECH_TIMEOUT: Final = "Распознавание речи не ответило вовремя. Попробуйте позже."
MESSAGE_SPEECH_BAD_RESPONSE: Final = (
    "Сервис распознавания речи вернул непонятный ответ. "
    "Попробуйте ещё раз или отправьте текст."
)
MESSAGE_SPEECH_TOO_LONG: Final = (
    f"Голосовое сообщение длиннее {MAX_AUDIO_DURATION_SECONDS} секунд. "
    "Отправьте сообщение короче."
)


class SpeechError(UserFacingError):
    """Базовая ошибка распознавания речи.

    Наследуется от :class:`tldw.errors.UserFacingError`: сообщение безопасно
    показывать пользователю, а причина сбоя пишется в лог без аудио и без
    распознанного текста.
    """

    def __init__(self, message: str = MESSAGE_SPEECH_UNAVAILABLE) -> None:
        super().__init__(message)


class SpeechNotConfiguredError(SpeechError):
    """Голосовой ввод не настроен: нет ключа или модели распознавания."""

    def __init__(self, message: str = MESSAGE_SPEECH_NOT_CONFIGURED) -> None:
        super().__init__(message)


class SpeechTooLongError(SpeechError):
    """Голосовое сообщение длиннее :data:`MAX_AUDIO_DURATION_SECONDS`."""

    def __init__(self, message: str = MESSAGE_SPEECH_TOO_LONG) -> None:
        super().__init__(message)


class SpeechTimeoutError(SpeechError):
    """Сервис распознавания не ответил за таймаут из конфига."""

    def __init__(self, message: str = MESSAGE_SPEECH_TIMEOUT) -> None:
        super().__init__(message)


class SpeechProviderError(SpeechError):
    """HTTP-ошибка сервиса распознавания или сетевой сбой."""

    def __init__(self, message: str = MESSAGE_SPEECH_UNAVAILABLE) -> None:
        super().__init__(message)


class SpeechResponseError(SpeechError):
    """Ответ сервиса пуст, не JSON или не содержит текста распознавания."""

    def __init__(self, message: str = MESSAGE_SPEECH_BAD_RESPONSE) -> None:
        super().__init__(message)


class SpeechAdapter:
    """Клиент распознавания речи: ключ, URL, модель и таймаут из конфига.

    :param api_key: ключ сервиса; в лог и ``repr`` не попадает. Пустая строка
        значит «распознавание не настроено».
    :param model: имя модели распознавания, например ``whisper-1``.
    :param base_url: базовый URL OpenAI-совместимого API.
    :param timeout: таймаут одного HTTP-запроса, секунды.
    :param transport: подмена транспорта httpx (в тестах — ``MockTransport``).

    Конструктор не требует настройки: без ключа или модели адаптер создаётся,
    но :meth:`transcribe` поднимает :class:`SpeechNotConfiguredError`, поэтому
    текстовый сценарий и тесты работают без этих env.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        model: str = "",
        base_url: str = DEFAULT_SPEECH_BASE_URL,
        timeout: float = DEFAULT_SPEECH_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        key = api_key.strip()
        model_name = model.strip()
        base = base_url.strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ValueError("SPEECH_BASE_URL должен начинаться с http:// или https://")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout должен быть положительным конечным числом секунд")

        self.model = model_name
        self.base_url = base
        self.timeout = float(timeout)
        self._api_key = key
        self._transport = transport

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> SpeechAdapter:
        """Собрать адаптер из настроек приложения.

        Все значения — из необязательных env; отсутствие ключа или модели не
        ошибка конфигурации, а состояние «голосовой ввод недоступен».
        """
        return cls(
            api_key=settings.speech_api_key,
            model=settings.speech_model,
            base_url=settings.speech_base_url,
            timeout=settings.speech_timeout_seconds,
            transport=transport,
        )

    @property
    def is_configured(self) -> bool:
        """Настроено ли распознавание: заданы и ключ, и модель."""
        return bool(self._api_key and self.model)

    def __repr__(self) -> str:
        """Не показывать ключ при случайном логировании адаптера."""
        return f"SpeechAdapter(model={self.model!r}, base_url={self.base_url!r})"

    def transcribe(
        self,
        audio: bytes,
        *,
        duration_seconds: float,
        filename: str = DEFAULT_AUDIO_FILENAME,
        media_type: str = DEFAULT_AUDIO_MEDIA_TYPE,
    ) -> str:
        """Распознать речь из аудио одним запросом и вернуть текст.

        :param audio: содержимое аудиофайла; в лог не попадает.
        :param duration_seconds: длительность аудио в секундах; больше
            :data:`MAX_AUDIO_DURATION_SECONDS` — ошибка до сетевого вызова.
        :param filename: имя файла в multipart-запросе (провайдеру нужен формат).
        :param media_type: MIME-тип аудио.
        :raises SpeechNotConfiguredError: ключ или модель не заданы в env.
        :raises SpeechTooLongError: аудио длиннее лимита.
        :raises SpeechTimeoutError: сервис не ответил за таймаут.
        :raises SpeechProviderError: HTTP-ошибка или сетевой сбой.
        :raises SpeechResponseError: пустой, битый ответ или нет текста.
        :raises ValueError: если ``duration_seconds``/``audio`` не того типа —
            это ошибка вызывающего кода, а не ответа сервиса.
        """
        duration = _check_duration(duration_seconds)
        if not self.is_configured:
            log.warning("Речь: адаптер не настроен — голосовой ввод недоступен")
            raise SpeechNotConfiguredError()

        data = _audio_bytes(audio)
        log.info("Речь: запрос распознавания (%.1f с, %s байт)", duration, len(data))

        with self._make_client() as client:
            try:
                response = client.post(
                    TRANSCRIPTIONS_PATH,
                    files={"file": (filename, data, media_type)},
                    data={"model": self.model},
                )
            except httpx.TimeoutException as exc:
                # Только тип исключения: текст httpx может содержать URL запроса,
                # а тело запроса — аудио.
                log.warning(
                    "Речь: таймаут запроса (%.1f с, %s)",
                    self.timeout,
                    type(exc).__name__,
                )
                raise SpeechTimeoutError() from exc
            except httpx.HTTPError as exc:
                log.warning("Речь: сетевая ошибка запроса (%s)", type(exc).__name__)
                raise SpeechProviderError() from exc

        if response.status_code >= 400:
            log.error(
                "Речь: сервис ответил HTTP %s %s",
                response.status_code,
                response.reason_phrase,
            )
            raise SpeechProviderError()

        text = _transcript_text(response)
        log.info("Речь: распознано %s символов за %.1f с", len(text), duration)
        return text

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


def _check_duration(value: object) -> float:
    """Проверить длительность аудио: конечное неотрицательное число и лимит.

    ``bool`` отвергается явно: это не длительность, хотя формально подкласс
    ``int``. Лимит проверяется до сетевого вызова.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("duration_seconds должен быть числом секунд")
    duration = float(value)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("duration_seconds должен быть неотрицательным конечным числом")
    if duration > MAX_AUDIO_DURATION_SECONDS:
        log.warning(
            "Речь: аудио длиннее лимита (%.1f с > %s с)",
            duration,
            MAX_AUDIO_DURATION_SECONDS,
        )
        raise SpeechTooLongError()
    return duration


def _audio_bytes(audio: object) -> bytes:
    """Проверить аудио: непустые bytes-подобные данные, без содержимого в логе."""
    if not isinstance(audio, (bytes, bytearray, memoryview)):
        raise ValueError("audio должен быть bytes")
    data = bytes(audio)
    if not data:
        raise ValueError("audio не может быть пустым")
    return data


def _transcript_text(response: httpx.Response) -> str:
    """Достать ``text`` из ответа ``audio/transcriptions``.

    Тело ответа не логируется: в нём может быть полный текст распознавания.
    """
    try:
        data = response.json()
    except ValueError as exc:
        log.warning("Речь: тело ответа не является JSON (%s)", type(exc).__name__)
        raise SpeechResponseError() from exc

    if not isinstance(data, dict):
        log.warning("Речь: ответ сервиса — не JSON-объект (%s)", type(data).__name__)
        raise SpeechResponseError()

    raw = data.get("text")
    if not isinstance(raw, str) or not raw.strip():
        log.warning("Речь: в ответе сервиса нет текста распознавания")
        raise SpeechResponseError()
    return raw.strip()


__all__ = [
    "DEFAULT_AUDIO_FILENAME",
    "DEFAULT_AUDIO_MEDIA_TYPE",
    "MAX_AUDIO_DURATION_SECONDS",
    "MESSAGE_SPEECH_BAD_RESPONSE",
    "MESSAGE_SPEECH_NOT_CONFIGURED",
    "MESSAGE_SPEECH_TIMEOUT",
    "MESSAGE_SPEECH_TOO_LONG",
    "MESSAGE_SPEECH_UNAVAILABLE",
    "TRANSCRIPTIONS_PATH",
    "SpeechAdapter",
    "SpeechError",
    "SpeechNotConfiguredError",
    "SpeechProviderError",
    "SpeechResponseError",
    "SpeechTimeoutError",
    "SpeechTooLongError",
]

"""Офлайновые тесты адаптера распознавания речи (T-011).

Все HTTP-вызовы идут через ``httpx.MockTransport``: сеть и реальные ключи не
нужны. Тесты не пишут на диск и не используют ``tmp_path``, поэтому файл
проходит и в read-only песочнице ревьюера.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from support import FAKE_API_KEY as FAKE_SUPPORT_API_KEY
from support import FAKE_BOT_TOKEN

from tldw.adapters import (
    MAX_AUDIO_DURATION_SECONDS,
    SpeechAdapter,
    SpeechError,
    SpeechNotConfiguredError,
    SpeechProviderError,
    SpeechResponseError,
    SpeechTimeoutError,
    SpeechTooLongError,
)
from tldw.adapters.speech import (
    MESSAGE_SPEECH_NOT_CONFIGURED,
    MESSAGE_SPEECH_TOO_LONG,
)
from tldw.config import (
    DEFAULT_SPEECH_BASE_URL,
    DEFAULT_SPEECH_TIMEOUT_SECONDS,
    load_settings,
)
from tldw.errors import UserFacingError, describe_error

FAKE_API_KEY = "test-speech-key-not-a-secret"
MODEL = "test-whisper-model"
BASE_URL = "https://speech.test/v1"
AUDIO = b"OggS-fake-audio-bytes-0123456789"
TRANSCRIPT = "секретный распознанный текст для проверки логов"

#: Минимальный валидный env без speech-переменных: файлов тест не создаёт,
#: поэтому весь файл проходит и в read-only песочнице ревьюера.
BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": FAKE_BOT_TOKEN,
    "LLM_API_KEY": FAKE_SUPPORT_API_KEY,
    "LLM_MODEL": "test-model",
}


def make_adapter(
    transport: httpx.BaseTransport,
    *,
    api_key: str = FAKE_API_KEY,
    model: str = MODEL,
) -> SpeechAdapter:
    return SpeechAdapter(
        api_key=api_key,
        model=model,
        base_url=BASE_URL,
        timeout=12.5,
        transport=transport,
    )


def transcription_response(text: str = TRANSCRIPT) -> httpx.Response:
    return httpx.Response(200, json={"text": text})


def recording_transport(
    response: httpx.Response | None = None,
) -> tuple[list[httpx.Request], httpx.MockTransport]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response if response is not None else transcription_response()

    return requests, httpx.MockTransport(handler)


def test_success_returns_text_and_makes_one_request() -> None:
    requests, transport = recording_transport(transcription_response(f"  {TRANSCRIPT}  "))

    text = make_adapter(transport).transcribe(AUDIO, duration_seconds=12.5)

    assert text == TRANSCRIPT, "текст распознавания возвращается без пробелов по краям"
    assert len(requests) == 1, "ровно одна попытка на сообщение"
    request = requests[0]
    assert str(request.url) == f"{BASE_URL}/audio/transcriptions"
    assert request.headers["Authorization"] == f"Bearer {FAKE_API_KEY}"
    assert request.headers["Content-Type"].startswith("multipart/form-data")

    body = request.content
    assert MODEL.encode() in body, "модель передаётся в запросе"
    assert AUDIO in body, "аудио уходит в запросе"
    assert b'name="file"; filename="audio.ogg"' in body

    # Таймаут из конфига действительно применяется к запросу.
    assert request.extensions["timeout"]["read"] == 12.5


def test_from_settings_takes_key_url_model_and_timeout_from_optional_env() -> None:
    settings = load_settings(
        {
            **BASE_ENV,
            "SPEECH_API_KEY": FAKE_API_KEY,
            "SPEECH_BASE_URL": f"{BASE_URL}/",
            "SPEECH_MODEL": MODEL,
            "SPEECH_TIMEOUT_SECONDS": "12.5",
        }
    )
    requests, transport = recording_transport()

    adapter = SpeechAdapter.from_settings(settings, transport=transport)

    assert settings.speech_api_key == FAKE_API_KEY
    assert settings.speech_base_url == BASE_URL
    assert settings.speech_model == MODEL
    assert settings.speech_timeout_seconds == 12.5
    assert adapter.is_configured
    assert adapter.model == MODEL
    assert adapter.base_url == BASE_URL
    assert adapter.timeout == 12.5
    assert FAKE_API_KEY not in repr(adapter)
    assert FAKE_API_KEY not in repr(settings)

    adapter.transcribe(AUDIO, duration_seconds=1)

    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == f"Bearer {FAKE_API_KEY}"


def test_load_settings_without_speech_env_is_not_configured() -> None:
    settings = load_settings(BASE_ENV)

    assert settings.speech_api_key == ""
    assert settings.speech_model == ""
    assert settings.speech_base_url == DEFAULT_SPEECH_BASE_URL
    assert settings.speech_timeout_seconds == DEFAULT_SPEECH_TIMEOUT_SECONDS

    requests, transport = recording_transport()
    adapter = SpeechAdapter.from_settings(settings, transport=transport)

    assert not adapter.is_configured
    with pytest.raises(SpeechNotConfiguredError) as exc_info:
        adapter.transcribe(AUDIO, duration_seconds=1)

    assert requests == [], "без настройки сеть не используется"
    assert str(exc_info.value) == MESSAGE_SPEECH_NOT_CONFIGURED
    assert "текстом" in str(exc_info.value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_key": "", "model": MODEL},
        {"api_key": FAKE_API_KEY, "model": ""},
        {"api_key": "   ", "model": "   "},
    ],
    ids=["no-key", "no-model", "blank"],
)
def test_missing_key_or_model_raises_user_facing_not_configured(
    kwargs: dict[str, str],
) -> None:
    requests, transport = recording_transport()

    adapter = SpeechAdapter(base_url=BASE_URL, transport=transport, **kwargs)

    with pytest.raises(SpeechNotConfiguredError) as exc_info:
        adapter.transcribe(AUDIO, duration_seconds=1)

    error = exc_info.value
    assert isinstance(error, SpeechError)
    assert isinstance(error, UserFacingError)
    assert requests == []
    report = describe_error(error)
    assert report.user_message == str(error)
    assert report.level == logging.WARNING
    assert report.traceback_text is None


@pytest.mark.parametrize("duration", [MAX_AUDIO_DURATION_SECONDS + 1, 61.5, 120])
def test_audio_longer_than_limit_is_rejected_before_network(duration: float) -> None:
    requests, transport = recording_transport()

    with pytest.raises(SpeechTooLongError) as exc_info:
        make_adapter(transport).transcribe(AUDIO, duration_seconds=duration)

    assert requests == [], "длительность проверяется до сетевого вызова"
    assert str(exc_info.value) == MESSAGE_SPEECH_TOO_LONG
    assert isinstance(exc_info.value, UserFacingError)
    assert str(MAX_AUDIO_DURATION_SECONDS) in str(exc_info.value)


def test_audio_exactly_at_limit_is_allowed() -> None:
    requests, transport = recording_transport()

    text = make_adapter(transport).transcribe(
        AUDIO,
        duration_seconds=MAX_AUDIO_DURATION_SECONDS,
    )

    assert text == TRANSCRIPT
    assert len(requests) == 1


@pytest.mark.parametrize(
    "duration",
    ["12", None, True, -1, float("nan"), float("inf")],
    ids=["string", "none", "bool", "negative", "nan", "inf"],
)
def test_invalid_duration_is_a_caller_error(duration: object) -> None:
    requests, transport = recording_transport()

    with pytest.raises(ValueError):
        make_adapter(transport).transcribe(AUDIO, duration_seconds=duration)  # type: ignore[arg-type]

    assert requests == []


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
def test_http_errors_raise_provider_error_without_retry(
    status: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    error_body = "body-with-echo and fake api key"
    requests, transport = recording_transport(httpx.Response(status, text=error_body))

    with (
        caplog.at_level(logging.DEBUG, logger="tldw.adapters.speech"),
        pytest.raises(SpeechProviderError) as exc_info,
    ):
        make_adapter(transport).transcribe(AUDIO, duration_seconds=5)

    assert len(requests) == 1, "одна попытка: повторов нет"
    assert isinstance(exc_info.value, SpeechError)
    assert isinstance(exc_info.value, UserFacingError)
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert error_body not in logged, "тело ответа не логируется"


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.WriteTimeout("write timed out"),
        httpx.PoolTimeout("pool timed out"),
    ],
    ids=["read", "connect", "write", "pool"],
)
def test_timeout_raises_typed_error(error: httpx.TimeoutException) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise error

    with pytest.raises(SpeechTimeoutError) as exc_info:
        make_adapter(httpx.MockTransport(handler)).transcribe(AUDIO, duration_seconds=5)

    assert len(calls) == 1, "таймаут не повторяется: одна попытка"
    assert isinstance(exc_info.value, SpeechError)
    assert isinstance(exc_info.value, UserFacingError)


def test_network_error_raises_provider_error() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("connection refused")

    with pytest.raises(SpeechProviderError) as exc_info:
        make_adapter(httpx.MockTransport(handler)).transcribe(AUDIO, duration_seconds=5)

    assert len(calls) == 1, "сетевой сбой не повторяется: одна попытка"
    assert isinstance(exc_info.value, UserFacingError)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text=""),
        httpx.Response(200, text="not json at all"),
        httpx.Response(200, json={"text": ""}),
        httpx.Response(200, json={"text": "   "}),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"text": 123}),
        httpx.Response(200, json=["text"]),
    ],
    ids=["empty-body", "not-json", "empty-text", "blank-text", "no-field", "not-str", "list"],
)
def test_empty_or_bad_response_raises_response_error(response: httpx.Response) -> None:
    requests, transport = recording_transport(response)

    with pytest.raises(SpeechResponseError) as exc_info:
        make_adapter(transport).transcribe(AUDIO, duration_seconds=5)

    assert len(requests) == 1
    assert isinstance(exc_info.value, SpeechError)
    assert isinstance(exc_info.value, UserFacingError)


def test_audio_and_transcript_never_reach_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests, transport = recording_transport()

    with caplog.at_level(logging.DEBUG, logger="tldw.adapters.speech"):
        text = make_adapter(transport).transcribe(AUDIO, duration_seconds=5)

    assert text == TRANSCRIPT
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert TRANSCRIPT not in logged, "полный текст распознавания не логируется"
    assert AUDIO.decode() not in logged, "аудио не логируется"
    assert FAKE_API_KEY not in logged, "ключ не логируется"
    assert f"{len(TRANSCRIPT)} символов" in logged, "в лог пишется только длина"


def test_error_log_contains_no_audio_or_transcript(
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = httpx.Response(500, text=f"{TRANSCRIPT} {AUDIO.decode()}")
    requests, transport = recording_transport(response)

    with (
        caplog.at_level(logging.DEBUG, logger="tldw.adapters.speech"),
        pytest.raises(SpeechProviderError),
    ):
        make_adapter(transport).transcribe(AUDIO, duration_seconds=5)

    assert len(requests) == 1
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert TRANSCRIPT not in logged
    assert AUDIO.decode() not in logged
    assert FAKE_API_KEY not in logged
    assert "500" in logged, "код ответа виден в логе"


def test_response_error_log_contains_no_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = httpx.Response(200, text=f"{TRANSCRIPT} {AUDIO.decode()}")

    with (
        caplog.at_level(logging.DEBUG, logger="tldw.adapters.speech"),
        pytest.raises(SpeechResponseError),
    ):
        make_adapter(httpx.MockTransport(lambda request: response)).transcribe(
            AUDIO,
            duration_seconds=5,
        )

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert TRANSCRIPT not in logged
    assert AUDIO.decode() not in logged


@pytest.mark.parametrize(
    "audio",
    [b"", bytearray(), memoryview(b"")],
    ids=["bytes", "bytearray", "memoryview"],
)
def test_empty_audio_is_a_caller_error(audio: object) -> None:
    requests, transport = recording_transport()

    with pytest.raises(ValueError):
        make_adapter(transport).transcribe(audio, duration_seconds=5)  # type: ignore[arg-type]

    assert requests == []


@pytest.mark.parametrize("audio", ["text", None, 123])
def test_non_bytes_audio_is_a_caller_error(audio: object) -> None:
    requests, transport = recording_transport()

    with pytest.raises(ValueError):
        make_adapter(transport).transcribe(audio, duration_seconds=5)  # type: ignore[arg-type]

    assert requests == []


@pytest.mark.parametrize(
    "audio",
    [bytearray(AUDIO), memoryview(AUDIO)],
    ids=["bytearray", "memoryview"],
)
def test_audio_bytes_like_objects_are_accepted(audio: object) -> None:
    requests, transport = recording_transport()

    text = make_adapter(transport).transcribe(audio, duration_seconds=5)  # type: ignore[arg-type]

    assert text == TRANSCRIPT
    assert len(requests) == 1


@pytest.mark.parametrize("base_url", ["speech.test/v1", "ftp://speech.test/v1", ""])
def test_invalid_base_url_is_rejected(base_url: str) -> None:
    with pytest.raises(ValueError, match="http"):
        SpeechAdapter(api_key=FAKE_API_KEY, model=MODEL, base_url=base_url)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        SpeechAdapter(api_key=FAKE_API_KEY, model=MODEL, base_url=BASE_URL, timeout=timeout)

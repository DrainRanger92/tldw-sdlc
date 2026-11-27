"""Тесты разбора сбоев: сообщение пользователю, причина в логе, секреты (T-008).

Модуль ``tldw.errors`` — чистый слой без aiogram и сети, поэтому основная
часть кейсов не требует ``tmp_path`` и проходит даже в read-only песочнице.
Проверяются:

- каждый типизированный тип ошибки (не YouTube, нет субтитров, слишком длинное
  видео, AI не ответил и его подтипы) даёт своё сообщение без стектрейса;
- неизвестная ошибка даёт общее сообщение и ``ERROR`` со стектрейсом;
- токены, ключи API и полный транскрипт в лог не попадают.
"""

from __future__ import annotations

import logging

import pytest
from support import FAKE_API_KEY, FAKE_BEARER_TOKEN, FAKE_BOT_TOKEN, FAKE_ENV_KEY

from tldw.adapters.llm import (
    MESSAGE_LLM_BAD_RESPONSE,
    MESSAGE_LLM_TIMEOUT,
    MESSAGE_LLM_UNAVAILABLE,
    LLMError,
    LLMProviderError,
    LLMResponseError,
    LLMTimeoutError,
)
from tldw.adapters.youtube import (
    MESSAGE_NETWORK,
    MESSAGE_NO_TRANSCRIPT,
    MESSAGE_NOT_YOUTUBE,
    MESSAGE_VIDEO_UNAVAILABLE,
    NoTranscriptError,
    NotYouTubeURLError,
    VideoUnavailableError,
    YouTubeNetworkError,
)
from tldw.config import MAX_AUDIO_DURATION_SECONDS
from tldw.errors import (
    MAX_LOG_FIELD_CHARS,
    MESSAGE_AUDIO_TOO_LONG,
    MESSAGE_UNEXPECTED,
    MESSAGE_UNEXPECTED_TEMPLATE,
    REDACTED,
    UserFacingError,
    describe_error,
    format_reason,
    log_failure,
    sanitize_log_text,
)
from tldw.services.summary import MESSAGE_VIDEO_TOO_LONG, VideoTooLongError

#: Длинный «транскрипт», который не должен целиком попасть в лог.
FAKE_TRANSCRIPT = " ".join(f"сегмент-{index}" for index in range(300))


def raised(exc: BaseException) -> BaseException:
    """Поднять исключение и вернуть его с настоящим стектрейсом."""
    try:
        raise exc
    except BaseException as caught:  # noqa: BLE001 - тесту нужен именно объект
        return caught


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (NotYouTubeURLError(), MESSAGE_NOT_YOUTUBE),
        (NoTranscriptError(), MESSAGE_NO_TRANSCRIPT),
        (LLMError(), MESSAGE_LLM_UNAVAILABLE),
        (LLMProviderError(), MESSAGE_LLM_UNAVAILABLE),
        (LLMTimeoutError(), MESSAGE_LLM_TIMEOUT),
        (LLMResponseError(), MESSAGE_LLM_BAD_RESPONSE),
        (YouTubeNetworkError(), MESSAGE_NETWORK),
        (VideoUnavailableError(), MESSAGE_VIDEO_UNAVAILABLE),
    ],
    ids=[
        "not-youtube",
        "no-transcript",
        "llm-unavailable",
        "llm-provider",
        "llm-timeout",
        "llm-bad-response",
        "youtube-network",
        "video-unavailable",
    ],
)
def test_known_error_gives_its_own_message_without_traceback(
    error: UserFacingError, expected: str
) -> None:
    assert isinstance(error, UserFacingError)

    report = describe_error(error)

    assert report.user_message == expected
    assert report.level == logging.WARNING
    assert report.traceback_text is None
    assert "Traceback" not in report.user_message


def test_video_too_long_message_names_duration_and_limit() -> None:
    error = VideoTooLongError(duration_seconds=7201, max_duration_seconds=7200)

    report = describe_error(error)

    assert report.user_message == MESSAGE_VIDEO_TOO_LONG.format(
        duration="02:00:01",
        limit="02:00:00",
    )
    assert "слишком длинное" in report.user_message
    assert "02:00:01" in report.user_message
    assert "02:00:00" in report.user_message
    assert report.level == logging.WARNING
    assert report.traceback_text is None


def test_unknown_error_gets_generic_message_and_error_with_traceback() -> None:
    error = raised(RuntimeError("boom"))

    report = describe_error(error)

    assert report.user_message == MESSAGE_UNEXPECTED
    assert "boom" not in report.user_message
    assert "Traceback" not in report.user_message
    assert report.level == logging.ERROR
    assert report.traceback_text is not None
    assert "Traceback (most recent call last)" in report.traceback_text
    assert "RuntimeError: boom" in report.traceback_text


def test_unknown_error_can_use_a_context_specific_message() -> None:
    """Сценарий шаблона передаёт свой текст для неизвестного сбоя (T-015)."""
    error = raised(RuntimeError("boom"))

    report = describe_error(error, unknown_message=MESSAGE_UNEXPECTED_TEMPLATE)

    assert report.user_message == MESSAGE_UNEXPECTED_TEMPLATE
    assert "видео" not in report.user_message
    assert report.level == logging.ERROR
    assert report.traceback_text is not None


def test_known_error_ignores_context_specific_unknown_message() -> None:
    """Переопределённый текст не подменяет сообщение известной ошибки."""
    report = describe_error(
        NotYouTubeURLError(),
        unknown_message=MESSAGE_UNEXPECTED_TEMPLATE,
    )

    assert report.user_message == MESSAGE_NOT_YOUTUBE


def test_audio_limit_message_is_shared_and_formatted() -> None:
    """Единственный текст лимита 60 секунд подставляется из конфига."""
    message = MESSAGE_AUDIO_TOO_LONG.format(limit=MAX_AUDIO_DURATION_SECONDS)

    assert "60 секунд" in message
    assert "Запишите описание короче" in message


def test_reason_names_exception_type_and_message() -> None:
    assert format_reason(NoTranscriptError()) == (
        f"NoTranscriptError: {MESSAGE_NO_TRANSCRIPT}"
    )
    assert format_reason(raised(RuntimeError("boom"))) == "RuntimeError: boom"


def test_log_failure_writes_known_error_at_warning_without_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="tldw.errors"):
        report = log_failure(NotYouTubeURLError())

    records = [record for record in caplog.records if record.name == "tldw.errors"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "NotYouTubeURLError" in records[0].getMessage()
    assert MESSAGE_NOT_YOUTUBE in records[0].getMessage()
    assert "Traceback" not in records[0].getMessage()
    assert report.level == logging.WARNING


def test_log_failure_writes_unknown_error_at_error_with_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="tldw.errors"):
        report = log_failure(raised(RuntimeError("boom")))

    records = [record for record in caplog.records if record.name == "tldw.errors"]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    message = records[0].getMessage()
    assert "RuntimeError: boom" in message
    assert "Traceback (most recent call last)" in message
    assert report.level == logging.ERROR


def test_log_redacts_telegram_token_api_keys_and_bearer() -> None:
    leaked = (
        f"POST https://api.telegram.org/bot{FAKE_BOT_TOKEN}/sendMessage "
        f"Authorization: Bearer {FAKE_BEARER_TOKEN} LLM_API_KEY={FAKE_ENV_KEY} "
        f"openai key {FAKE_API_KEY} "
        "proxy http://user:supersecret@proxy.local"
    )
    report = describe_error(raised(RuntimeError(leaked)))

    for text in (report.reason, report.traceback_text or ""):
        for secret in (FAKE_BOT_TOKEN, FAKE_BEARER_TOKEN, FAKE_ENV_KEY, FAKE_API_KEY):
            assert secret not in text
        assert "supersecret" not in text
        assert REDACTED in text


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        (f"Authorization: Bearer {FAKE_BEARER_TOKEN}", FAKE_BEARER_TOKEN),
        (f"bearer {FAKE_BEARER_TOKEN}", FAKE_BEARER_TOKEN),
        (f"Authorization: Token {FAKE_BEARER_TOKEN}", FAKE_BEARER_TOKEN),
        (f"Authorization: Basic {FAKE_ENV_KEY}", FAKE_ENV_KEY),
        (
            f"headers={{'Authorization': 'Bearer {FAKE_BEARER_TOKEN}'}}",
            FAKE_BEARER_TOKEN,
        ),
    ],
    ids=["header-bearer", "free-bearer", "header-token", "header-basic", "repr-header"],
)
def test_authorization_credentials_are_redacted_without_sk_prefix(
    message: str,
    secret: str,
) -> None:
    """Схема авторизации не должна съедать шаблон и оставлять сам токен."""
    report = describe_error(raised(RuntimeError(message)))

    for text in (report.reason, report.traceback_text or ""):
        assert secret not in text
        assert REDACTED in text


def test_user_message_does_not_repeat_secrets() -> None:
    """Даже известная ошибка с секретом в тексте не отдаёт его пользователю."""
    error = LLMProviderError(f"provider said {FAKE_API_KEY}")

    report = describe_error(error)

    assert FAKE_API_KEY not in report.user_message
    assert REDACTED in report.user_message


def test_long_known_error_message_falls_back_to_generic() -> None:
    """Транскрипт, попавший в текст известной ошибки, не показывается и не логируется."""
    report = describe_error(LLMError(FAKE_TRANSCRIPT))

    assert report.user_message == MESSAGE_UNEXPECTED
    assert FAKE_TRANSCRIPT not in report.user_message
    assert FAKE_TRANSCRIPT not in report.reason
    assert "фрагмент обрезан" in report.reason


def test_reason_truncates_full_transcript() -> None:
    report = describe_error(raised(RuntimeError(FAKE_TRANSCRIPT)))

    assert FAKE_TRANSCRIPT not in report.reason
    assert "фрагмент обрезан" in report.reason
    assert len(report.reason) < len(FAKE_TRANSCRIPT)
    assert len(report.reason) <= MAX_LOG_FIELD_CHARS + 50


def test_traceback_does_not_contain_full_transcript() -> None:
    report = describe_error(raised(RuntimeError(FAKE_TRANSCRIPT)))

    assert report.traceback_text is not None
    assert FAKE_TRANSCRIPT not in report.traceback_text
    assert "фрагмент обрезан" in report.traceback_text


def test_traceback_collapses_multiline_exception_message() -> None:
    transcript = "\n".join(f"строка {index} " + "x" * 40 for index in range(30))
    report = describe_error(raised(RuntimeError(transcript)))

    assert report.traceback_text is not None
    assert transcript not in report.traceback_text
    assert "фрагмент обрезан" in report.traceback_text


def test_traceback_collapses_indented_multiline_exception_message() -> None:
    """Отступ в четыре пробела — не признак кадра: транскрипт не уходит в лог целиком."""
    transcript = "\n".join(f"    строка {index} " + "x" * 60 for index in range(80))
    assert len(transcript) > 5000

    report = describe_error(raised(RuntimeError(transcript)))

    traceback_text = report.traceback_text or ""
    assert transcript not in traceback_text
    assert "фрагмент обрезан" in traceback_text
    assert "Traceback (most recent call last)" in traceback_text
    # Отступ в четыре пробела не делает строку кадром: все строки транскрипта
    # схлопываются в одно сообщение, а не идут в лог по отдельности.
    assert not any(line.startswith("    строка") for line in traceback_text.splitlines())
    collapsed = [line for line in traceback_text.splitlines() if "строка" in line]
    assert len(collapsed) == 1, "сообщение исключения должно схлопнуться в одну строку"
    assert len(collapsed[0]) <= MAX_LOG_FIELD_CHARS + len("… [фрагмент обрезан]")
    assert len(traceback_text) < len(transcript)


def test_sanitize_log_text_keeps_short_clean_text() -> None:
    assert sanitize_log_text("  короткая   причина  ") == "короткая причина"

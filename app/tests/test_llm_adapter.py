"""Офлайновые тесты LLM-адаптера: промпт, retry, таймаут и разбор JSON.

Все HTTP-вызовы идут через ``httpx.MockTransport``: сеть и реальные ключи не
нужны. Тесты не пишут на диск и не используют ``tmp_path``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from tldw.adapters import (
    LLMClient,
    LLMConfigError,
    LLMError,
    LLMProviderError,
    LLMResponseError,
    LLMTimeoutError,
    SummaryQuote,
    SummaryTimelineEntry,
    VideoSummary,
    build_system_prompt,
    build_user_prompt,
    parse_summary,
)
from tldw.adapters import llm as llm_module
from tldw.adapters.llm import (
    CUSTOM_PROMPT_RESOURCE,
    MAX_FOLLOW_UPS,
    MAX_KEY_IDEAS,
    MAX_QUOTES,
    MAX_TIMELINE_ENTRIES,
    MAX_TLDR,
    MIN_FOLLOW_UPS,
    MIN_KEY_IDEAS,
    MIN_QUOTES,
    MIN_TLDR,
    PROMPT_RESOURCE,
    build_custom_system_prompt,
    build_template_parse_prompt,
    load_note_template,
)
from tldw.config import ConfigError, load_settings
from tldw.errors import UserFacingError
from tldw.services.note import SECTION_HEADINGS

FAKE_API_KEY = "test-llm-key-not-a-secret"
MODEL = "test-model"
BASE_URL = "https://llm.test/v1"
TRANSCRIPT = "00:00 Вступление\n00:15 Первая мысль\n12:34 Практика"


@dataclass
class RecordingSleep:
    """Фейковый сон: запоминает паузы и не тормозит тесты."""

    calls: list[float] = field(default_factory=list)

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def valid_payload() -> dict[str, Any]:
    """Минимальный валидный ответ по всем пяти разделам шаблона."""
    return {
        "tldr": [
            "Первое предложение.",
            "Второе предложение.",
            "Третье предложение.",
        ],
        "key_ideas": [f"Идея номер {index}" for index in range(1, 6)],
        "timeline": [
            {"timecode": "00:00", "title": "Вступление"},
            {"timecode": "12:34", "title": "Практика"},
        ],
        "quotes": [
            {"timecode": "03:21", "text": "Первая цитата."},
            {"timecode": "27:08", "text": "Вторая цитата."},
        ],
        "follow_ups": ["Проверить пример из видео."],
    }


def valid_content(**overrides: Any) -> str:
    payload = valid_payload()
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def chat_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def make_client(
    transport: httpx.BaseTransport,
    *,
    sleep: RecordingSleep | None = None,
    **kwargs: Any,
) -> LLMClient:
    return LLMClient(
        api_key=FAKE_API_KEY,
        model=MODEL,
        base_url=BASE_URL,
        transport=transport,
        sleep=sleep if sleep is not None else RecordingSleep(),
        **kwargs,
    )


def test_success_parses_summary_and_sends_configured_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return chat_response(valid_content())

    client = make_client(httpx.MockTransport(handler))

    summary = client.summarize(TRANSCRIPT, title="Название", channel="Канал")

    assert summary == VideoSummary(
        tldr=("Первое предложение.", "Второе предложение.", "Третье предложение."),
        key_ideas=tuple(f"Идея номер {index}" for index in range(1, 6)),
        timeline=(
            SummaryTimelineEntry("00:00", "Вступление"),
            SummaryTimelineEntry("12:34", "Практика"),
        ),
        quotes=(
            SummaryQuote("Первая цитата.", "03:21"),
            SummaryQuote("Вторая цитата.", "27:08"),
        ),
        follow_ups=("Проверить пример из видео.",),
    )
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://llm.test/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {FAKE_API_KEY}"

    body = json.loads(request.content)
    assert body["model"] == MODEL
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    system_prompt = body["messages"][0]["content"]
    user_prompt = body["messages"][1]["content"]
    assert SECTION_HEADINGS[0] in system_prompt
    assert TRANSCRIPT in user_prompt
    assert "Название видео: Название" in user_prompt
    assert "Канал: Канал" in user_prompt


def test_from_settings_takes_key_model_timeout_and_base_url_from_env() -> None:
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "123456789:test-token-not-a-secret",
            "LLM_API_KEY": FAKE_API_KEY,
            "LLM_MODEL": MODEL,
            "LLM_BASE_URL": BASE_URL,
            "LLM_TIMEOUT_SECONDS": "12.5",
        }
    )
    request_bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(json.loads(request.content))
        assert request.headers["Authorization"] == f"Bearer {FAKE_API_KEY}"
        return chat_response(valid_content())

    client = LLMClient.from_settings(
        settings,
        transport=httpx.MockTransport(handler),
        sleep=RecordingSleep(),
    )

    client.summarize(TRANSCRIPT)

    assert client.model == MODEL
    assert client.timeout == 12.5
    assert request_bodies[0]["model"] == MODEL
    assert FAKE_API_KEY not in repr(client)


def test_prompt_contains_exactly_template_sections_and_json_schema() -> None:
    prompt = build_system_prompt()

    positions = [prompt.index(heading) for heading in SECTION_HEADINGS]
    assert positions == sorted(positions)
    assert all(prompt.count(heading) == 1 for heading in SECTION_HEADINGS)
    assert f"{MIN_TLDR}–{MAX_TLDR}" in prompt
    assert f"{MIN_KEY_IDEAS}–{MAX_KEY_IDEAS}" in prompt
    assert f"{MIN_QUOTES}–{MAX_QUOTES}" in prompt
    assert f"{MIN_FOLLOW_UPS}–{MAX_FOLLOW_UPS}" in prompt
    assert f"до {MAX_TIMELINE_ENTRIES}" in prompt
    assert "JSON" in prompt
    assert "{{NOTE_TEMPLATE}}" not in prompt
    assert load_note_template() in prompt


def test_user_prompt_wraps_transcript_and_optional_metadata() -> None:
    prompt = build_user_prompt("  текст видео  ", title="Название", channel="Канал")

    assert prompt.startswith("Название видео: Название\nКанал: Канал\n")
    assert "<transcript>\nтекст видео\n</transcript>" in prompt
    assert build_user_prompt("текст").startswith("<transcript>\nтекст")


@pytest.mark.parametrize("retryable_status", [429, 500, 502, 503])
def test_retries_429_and_5xx_then_succeeds(retryable_status: int) -> None:
    statuses = [retryable_status, 200]
    calls: list[httpx.Request] = []
    sleep = RecordingSleep()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        status = statuses[len(calls) - 1]
        if status == 200:
            return chat_response(valid_content())
        return httpx.Response(status, json={"error": "temporary"})

    summary = make_client(httpx.MockTransport(handler), sleep=sleep).summarize(TRANSCRIPT)

    assert summary.tldr[0] == "Первое предложение."
    assert len(calls) == 2
    assert sleep.calls == [1.0]


def test_permanent_500_raises_llm_error_with_reason_in_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[httpx.Request] = []
    sleep = RecordingSleep()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, text="upstream body with no secrets")

    with (
        caplog.at_level(logging.WARNING, logger="tldw.adapters.llm"),
        pytest.raises(LLMProviderError),
    ):
        make_client(httpx.MockTransport(handler), sleep=sleep).summarize(TRANSCRIPT)

    assert len(calls) == 3
    assert sleep.calls == [1.0, 2.0]
    assert "HTTP 500" in caplog.text
    assert FAKE_API_KEY not in caplog.text
    assert "upstream body with no secrets" not in caplog.text


def test_timeout_raises_llm_timeout_with_reason_in_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[httpx.Request] = []
    sleep = RecordingSleep()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("read timed out", request=request)

    with (
        caplog.at_level(logging.WARNING, logger="tldw.adapters.llm"),
        pytest.raises(LLMTimeoutError),
    ):
        make_client(httpx.MockTransport(handler), sleep=sleep).summarize(TRANSCRIPT)

    assert len(calls) == 1
    assert sleep.calls == []
    assert "таймаут" in caplog.text.lower()
    assert "ReadTimeout" in caplog.text
    assert FAKE_API_KEY not in caplog.text


def test_invalid_timeout_is_rejected() -> None:
    for bad_timeout in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            LLMClient(api_key=FAKE_API_KEY, model=MODEL, timeout=bad_timeout)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://llm.test:bad/v1",
        "https://[",
        "https://[::1",
        "https://",
        "https:///v1",
        "https://llm.test:0/v1",
        "https://llm.test:65536/v1",
        "https://exa\nmple.test/v1",
        "https://llm.test\r\nX-Injected: 1/v1",
    ],
)
def test_broken_base_url_is_config_error_not_raw_httpx(base_url: str) -> None:
    with pytest.raises(ConfigError) as exc_info:
        LLMClient(api_key=FAKE_API_KEY, model=MODEL, base_url=base_url)

    assert base_url not in str(exc_info.value)


def test_broken_base_url_at_runtime_is_llm_provider_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Если URL испорчен после конструктора, создание клиента обёрнуто."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return chat_response(valid_content())

    client = make_client(httpx.MockTransport(handler))
    client.base_url = f"https://llm.test:bad/v1?api_key={FAKE_API_KEY}"

    with (
        caplog.at_level(logging.WARNING, logger="tldw.adapters.llm"),
        pytest.raises(LLMProviderError) as exc_info,
    ):
        client.summarize(TRANSCRIPT)

    assert requests == [], "до сети дело не доходит"
    assert isinstance(exc_info.value, LLMError)
    assert isinstance(exc_info.value, UserFacingError)
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "InvalidURL" in logged, "в лог попадает только тип исключения"
    assert FAKE_API_KEY not in logged
    assert "api_key" not in logged
    assert "llm.test" not in logged


@pytest.mark.parametrize(
    "error",
    [
        httpx.InvalidURL(f"bad https://llm.test/v1?api_key={FAKE_API_KEY}&q=secret"),
        ValueError(f"bad https://llm.test/v1?api_key={FAKE_API_KEY}&q=secret"),
    ],
    ids=["invalid-url", "value-error"],
)
def test_request_url_error_is_llm_provider_error(
    error: Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise error

    with (
        caplog.at_level(logging.WARNING, logger="tldw.adapters.llm"),
        pytest.raises(LLMProviderError) as exc_info,
    ):
        make_client(httpx.MockTransport(handler)).summarize(TRANSCRIPT)

    assert len(calls) == 1
    assert isinstance(exc_info.value, LLMError)
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert type(error).__name__ in logged
    assert FAKE_API_KEY not in logged
    assert "api_key" not in logged
    assert "q=secret" not in logged
    assert "https://" not in logged


def test_value_error_when_building_client_is_typed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken_client(*args: object, **kwargs: object) -> httpx.Client:
        raise ValueError(f"bad {FAKE_API_KEY}")

    monkeypatch.setattr(llm_module.httpx, "Client", broken_client)

    with (
        caplog.at_level(logging.WARNING, logger="tldw.adapters.llm"),
        pytest.raises(LLMProviderError) as exc_info,
    ):
        make_client(httpx.MockTransport(lambda request: chat_response(valid_content()))).summarize(
            TRANSCRIPT
        )

    assert isinstance(exc_info.value, LLMError)
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "ValueError" in logged
    assert FAKE_API_KEY not in logged


def test_missing_prompt_resource_raises_typed_llm_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    build_system_prompt.cache_clear()
    monkeypatch.setattr(llm_module, "PROMPT_RESOURCE", "missing_prompt.md")

    try:
        with (
            caplog.at_level(logging.ERROR, logger="tldw.adapters.llm"),
            pytest.raises(LLMConfigError) as exc_info,
        ):
            build_system_prompt()
    finally:
        build_system_prompt.cache_clear()

    error = exc_info.value
    assert isinstance(error, LLMError)
    assert isinstance(error, UserFacingError)
    assert "missing_prompt.md" not in str(error)
    assert "AI" in str(error)
    assert "missing_prompt.md" in caplog.text, "имя ресурса видно в логе"
    assert FAKE_API_KEY not in caplog.text


def test_missing_template_marker_raises_typed_llm_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_system_prompt.cache_clear()
    monkeypatch.setattr(llm_module, "TEMPLATE_PLACEHOLDER", "{{MISSING_MARKER}}")

    try:
        with pytest.raises(LLMConfigError):
            build_system_prompt()
    finally:
        build_system_prompt.cache_clear()


def test_missing_custom_prompt_marker_raises_typed_llm_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_module, "CUSTOM_PROMPT_RESOURCE", PROMPT_RESOURCE)

    with pytest.raises(LLMConfigError):
        build_custom_system_prompt(["Раздел"])


def test_missing_template_parse_prompt_marker_raises_typed_llm_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_module, "TEMPLATE_PARSE_PROMPT_RESOURCE", CUSTOM_PROMPT_RESOURCE)

    with pytest.raises(LLMConfigError):
        build_template_parse_prompt(["Раздел"])


def test_missing_prompt_resource_does_not_reach_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return chat_response(valid_content())

    build_system_prompt.cache_clear()
    monkeypatch.setattr(llm_module, "PROMPT_RESOURCE", "missing_prompt.md")

    try:
        with pytest.raises(LLMConfigError):
            make_client(httpx.MockTransport(handler)).summarize(TRANSCRIPT)
    finally:
        build_system_prompt.cache_clear()

    assert requests == [], "неполный промпт не уходит провайдеру"


def test_error_hierarchy_includes_config_error() -> None:
    assert issubclass(LLMConfigError, LLMError)
    assert str(LLMConfigError())


def test_follow_up_limits_are_both_in_public_surface() -> None:
    """P2 T-004: ``MAX_FOLLOW_UPS`` не должен выпадать из ``__all__``."""
    assert {"MIN_FOLLOW_UPS", "MAX_FOLLOW_UPS"} <= set(llm_module.__all__)


def test_connection_error_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(LLMProviderError):
        make_client(httpx.MockTransport(handler)).summarize(TRANSCRIPT)


def test_http_401_is_not_retried() -> None:
    calls: list[httpx.Request] = []
    sleep = RecordingSleep()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, json={"error": "bad key"})

    with pytest.raises(LLMProviderError):
        make_client(httpx.MockTransport(handler), sleep=sleep).summarize(TRANSCRIPT)

    assert len(calls) == 1
    assert sleep.calls == []


def test_broken_json_raises_llm_error_without_partial_note(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response('{"tldr": [')

    with (
        caplog.at_level(logging.WARNING, logger="tldw.adapters.llm"),
        pytest.raises(LLMResponseError),
    ):
        make_client(httpx.MockTransport(handler)).summarize(TRANSCRIPT)

    assert "битый JSON" in caplog.text


def test_empty_response_raises_llm_error(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response("   ")

    with (
        caplog.at_level(logging.WARNING, logger="tldw.adapters.llm"),
        pytest.raises(LLMResponseError),
    ):
        make_client(httpx.MockTransport(handler)).summarize(TRANSCRIPT)

    assert "пустой ответ" in caplog.text


def test_empty_choices_raises_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(LLMResponseError):
        make_client(httpx.MockTransport(handler)).summarize(TRANSCRIPT)


def test_non_json_http_body_raises_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(LLMResponseError):
        make_client(httpx.MockTransport(handler)).summarize(TRANSCRIPT)


def test_empty_transcript_is_rejected_without_http_call() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return chat_response(valid_content())

    with pytest.raises(LLMError):
        make_client(httpx.MockTransport(handler)).summarize("   ")

    assert calls == []


@pytest.mark.parametrize(
    "field",
    ["tldr", "key_ideas", "timeline", "quotes", "follow_ups"],
)
def test_missing_required_field_raises_llm_error(
    field: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = valid_payload()
    payload.pop(field)

    with (
        caplog.at_level(logging.WARNING, logger="tldw.adapters.llm"),
        pytest.raises(LLMResponseError),
    ):
        parse_summary(json.dumps(payload, ensure_ascii=False))

    assert field in caplog.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("tldr", ["Одно.", "Два."], id="tldr-too-short"),
        pytest.param("tldr", [f"Предложение {i}." for i in range(MAX_TLDR + 1)], id="tldr-long"),
        pytest.param(
            "key_ideas",
            [f"Идея {i}" for i in range(MIN_KEY_IDEAS - 1)],
            id="key-ideas-short",
        ),
        pytest.param(
            "key_ideas",
            [f"Идея {i}" for i in range(MAX_KEY_IDEAS + 1)],
            id="key-ideas-long",
        ),
        pytest.param(
            "quotes",
            [{"timecode": "00:01", "text": "Одна цитата."}],
            id="quotes-short",
        ),
        pytest.param(
            "quotes",
            [{"timecode": f"00:0{i}", "text": f"Цитата {i}."} for i in range(MAX_QUOTES + 1)],
            id="quotes-long",
        ),
        pytest.param("timeline", [], id="timeline-empty"),
        pytest.param("follow_ups", [], id="follow-ups-empty"),
        pytest.param("follow_ups", [f"Вопрос {i}" for i in range(11)], id="follow-ups-long"),
    ],
)
def test_section_limits_are_enforced(field: str, value: Any) -> None:
    with pytest.raises(LLMResponseError):
        parse_summary(valid_content(**{field: value}))


def test_section_limits_accept_boundary_values() -> None:
    summary = parse_summary(
        valid_content(
            tldr=[f"Предложение {index}." for index in range(MIN_TLDR)],
            key_ideas=[f"Идея {index}" for index in range(MIN_KEY_IDEAS)],
            quotes=[
                {"timecode": "00:01", "text": "Цитата раз."},
                {"timecode": "00:02", "text": "Цитата два."},
            ],
        )
    )

    assert len(summary.tldr) == MIN_TLDR
    assert len(summary.key_ideas) == MIN_KEY_IDEAS
    assert len(summary.quotes) == MIN_QUOTES


@pytest.mark.parametrize(
    ("section", "field"),
    [
        pytest.param("quotes", "timecode", id="quote-timecode"),
        pytest.param("timeline", "timecode", id="timeline-timecode"),
        pytest.param("quotes", "text", id="quote-text"),
        pytest.param("timeline", "title", id="timeline-title"),
    ],
)
def test_missing_item_fields_are_rejected(
    section: str,
    field: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = valid_payload()
    payload[section][0].pop(field)

    with (
        caplog.at_level(logging.WARNING, logger="tldw.adapters.llm"),
        pytest.raises(LLMResponseError),
    ):
        parse_summary(json.dumps(payload, ensure_ascii=False))

    assert field in caplog.text


@pytest.mark.parametrize("timecode", ["", "12", "1:2:3", "99:99", "abc"])
def test_invalid_timecode_is_rejected(timecode: str) -> None:
    with pytest.raises(LLMResponseError):
        parse_summary(
            valid_content(
                quotes=[
                    {"timecode": timecode, "text": "Цитата раз."},
                    {"timecode": "00:02", "text": "Цитата два."},
                ]
            )
        )


def test_timecodes_are_normalized() -> None:
    summary = parse_summary(
        valid_content(
            timeline=[{"timecode": 65, "title": "Число секунд"}],
            quotes=[
                {"timecode": "1:2:03", "text": "Цитата раз."},
                {"timecode": "0:02", "text": "Цитата два."},
            ],
        )
    )

    assert summary.timeline[0].timecode == "01:05"
    assert summary.quotes[0].timecode == "01:02:03"
    assert summary.quotes[1].timecode == "00:02"


def test_non_string_items_are_rejected() -> None:
    with pytest.raises(LLMResponseError):
        parse_summary(valid_content(tldr=[1, 2, 3]))


def test_json_array_instead_of_object_is_rejected() -> None:
    with pytest.raises(LLMResponseError):
        parse_summary("[]")


def test_code_fenced_json_is_accepted() -> None:
    summary = parse_summary(f"```json\n{valid_content()}\n```")

    assert summary.tldr[0] == "Первое предложение."


def test_extra_fields_are_ignored() -> None:
    summary = parse_summary(valid_content(tags=["llm", "video"]))

    assert summary.tldr[0] == "Первое предложение."


def test_error_hierarchy_and_safe_messages() -> None:
    assert issubclass(LLMTimeoutError, LLMError)
    assert issubclass(LLMProviderError, LLMError)
    assert issubclass(LLMResponseError, LLMError)
    assert str(LLMTimeoutError())
    assert str(LLMProviderError())
    assert str(LLMResponseError())

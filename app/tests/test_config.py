"""Тесты загрузчика конфигурации: env-only, валидация и безопасные ошибки."""

from __future__ import annotations

from pathlib import Path

import pytest

from tldw.config import (
    ALL_ENV_VARS,
    APP_DIR,
    DEFAULT_DATA_DIR,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_MAX_VIDEO_DURATION_SECONDS,
    DEFAULT_SPEECH_BASE_URL,
    DEFAULT_SPEECH_TIMEOUT_SECONDS,
    ConfigError,
    load_env_file,
    load_settings,
    read_env_file,
)


def test_loads_all_values_from_env(fake_env: dict[str, str], tmp_path: Path) -> None:
    env = {
        **fake_env,
        "TELEGRAM_ALLOWED_USER_IDS": "1001, 1002 1003;1002",
        "LLM_BASE_URL": "https://llm.example.test/v1/",
        "LLM_TIMEOUT_SECONDS": "12.5",
        "VIDEO_MAX_DURATION_SECONDS": "3600",
        "LOG_LEVEL": "debug",
    }

    settings = load_settings(env)

    assert settings.telegram_bot_token == fake_env["TELEGRAM_BOT_TOKEN"]
    assert settings.llm_api_key == fake_env["LLM_API_KEY"]
    assert settings.llm_model == "test-model"
    assert settings.allowed_user_ids == (1001, 1002, 1003)
    assert settings.max_video_duration_seconds == 3600
    assert settings.llm_base_url == "https://llm.example.test/v1"
    assert settings.llm_timeout_seconds == 12.5
    assert settings.data_dir == Path(fake_env["DATA_DIR"])
    assert settings.log_level == "DEBUG"


def test_defaults_are_safe(fake_env: dict[str, str]) -> None:
    env = {name: value for name, value in fake_env.items() if name != "DATA_DIR"}

    settings = load_settings(env)

    assert settings.allowed_user_ids == ()
    assert settings.max_video_duration_seconds == DEFAULT_MAX_VIDEO_DURATION_SECONDS == 7200
    assert settings.llm_base_url == DEFAULT_LLM_BASE_URL
    assert settings.llm_timeout_seconds == 60.0
    assert settings.data_dir == DEFAULT_DATA_DIR
    assert settings.log_level == "INFO"


def test_load_settings_reads_process_environment_by_default(
    fake_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ALL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in fake_env.items():
        monkeypatch.setenv(name, value)

    settings = load_settings()

    assert settings.telegram_bot_token == fake_env["TELEGRAM_BOT_TOKEN"]
    assert settings.llm_model == "test-model"
    assert settings.data_dir == Path(fake_env["DATA_DIR"])


@pytest.mark.parametrize("missing", ["TELEGRAM_BOT_TOKEN", "LLM_API_KEY"])
def test_missing_required_secret_gives_clear_error(fake_env: dict[str, str], missing: str) -> None:
    env = {name: value for name, value in fake_env.items() if name != missing}

    with pytest.raises(ConfigError) as exc_info:
        load_settings(env)

    message = str(exc_info.value)
    assert missing in message
    assert "app/.env" in message
    assert "app/.env.example" in message
    # Сообщение не должно раскрывать значение другого секрета.
    other_secret = (
        fake_env["LLM_API_KEY"]
        if missing == "TELEGRAM_BOT_TOKEN"
        else fake_env["TELEGRAM_BOT_TOKEN"]
    )
    assert other_secret not in message


def test_missing_llm_model_gives_clear_error(fake_env: dict[str, str]) -> None:
    env = {name: value for name, value in fake_env.items() if name != "LLM_MODEL"}

    with pytest.raises(ConfigError, match="LLM_MODEL"):
        load_settings(env)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_required_value_is_treated_as_missing(fake_env: dict[str, str], value: str) -> None:
    env = {**fake_env, "TELEGRAM_BOT_TOKEN": value}

    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_settings(env)


@pytest.mark.parametrize(
    "token", ["not-a-token", "123456789", "123456789:", "123456789:with space"]
)
def test_invalid_bot_token_format_is_rejected(fake_env: dict[str, str], token: str) -> None:
    env = {**fake_env, "TELEGRAM_BOT_TOKEN": token}

    with pytest.raises(ConfigError) as exc_info:
        load_settings(env)

    message = str(exc_info.value)
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "формат" in message


@pytest.mark.parametrize("value", ["alice", "10, -5", "12.5"])
def test_non_numeric_allowlist_is_rejected(fake_env: dict[str, str], value: str) -> None:
    env = {**fake_env, "TELEGRAM_ALLOWED_USER_IDS": value}

    with pytest.raises(ConfigError) as exc_info:
        load_settings(env)

    message = str(exc_info.value)
    assert "TELEGRAM_ALLOWED_USER_IDS" in message
    assert "user ID" in message
    assert "запятую" in message


def test_non_positive_allowlist_is_rejected(fake_env: dict[str, str]) -> None:
    env = {**fake_env, "TELEGRAM_ALLOWED_USER_IDS": "0"}

    with pytest.raises(ConfigError, match="положительным"):
        load_settings(env)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_allowlist_means_open_access(fake_env: dict[str, str], value: str) -> None:
    settings = load_settings({**fake_env, "TELEGRAM_ALLOWED_USER_IDS": value})

    assert settings.allowed_user_ids == ()


def test_invalid_allowlist_error_does_not_echo_value(fake_env: dict[str, str]) -> None:
    env = {**fake_env, "TELEGRAM_ALLOWED_USER_IDS": "super-secret-looking-value"}

    with pytest.raises(ConfigError) as exc_info:
        load_settings(env)

    assert "super-secret-looking-value" not in str(exc_info.value)


@pytest.mark.parametrize("value", ["0", "-5", "2 hours", "abc"])
def test_invalid_video_duration_is_rejected(fake_env: dict[str, str], value: str) -> None:
    env = {**fake_env, "VIDEO_MAX_DURATION_SECONDS": value}

    with pytest.raises(ConfigError, match="VIDEO_MAX_DURATION_SECONDS"):
        load_settings(env)


@pytest.mark.parametrize("value", ["0", "-1", "soon"])
def test_invalid_llm_timeout_is_rejected(fake_env: dict[str, str], value: str) -> None:
    env = {**fake_env, "LLM_TIMEOUT_SECONDS": value}

    with pytest.raises(ConfigError, match="LLM_TIMEOUT_SECONDS"):
        load_settings(env)


def test_invalid_llm_base_url_is_rejected(fake_env: dict[str, str]) -> None:
    env = {**fake_env, "LLM_BASE_URL": "api.example.test/v1"}

    with pytest.raises(ConfigError, match="LLM_BASE_URL"):
        load_settings(env)


def test_speech_settings_are_optional_and_parsed(fake_env: dict[str, str]) -> None:
    defaults = load_settings(fake_env)

    assert defaults.speech_api_key == ""
    assert defaults.speech_model == ""
    assert defaults.speech_base_url == DEFAULT_SPEECH_BASE_URL
    assert defaults.speech_timeout_seconds == DEFAULT_SPEECH_TIMEOUT_SECONDS

    env = {
        **fake_env,
        "SPEECH_API_KEY": "test-speech-key-not-a-secret",
        "SPEECH_MODEL": "test-whisper-model",
        "SPEECH_BASE_URL": "https://speech.example.test/v1/",
        "SPEECH_TIMEOUT_SECONDS": "12.5",
    }

    settings = load_settings(env)

    assert settings.speech_api_key == "test-speech-key-not-a-secret"
    assert settings.speech_model == "test-whisper-model"
    assert settings.speech_base_url == "https://speech.example.test/v1"
    assert settings.speech_timeout_seconds == 12.5


@pytest.mark.parametrize(
    ("name", "attribute"),
    [("SPEECH_API_KEY", "speech_api_key"), ("SPEECH_MODEL", "speech_model")],
)
@pytest.mark.parametrize("value", ["", "   "])
def test_blank_speech_value_means_not_configured(
    fake_env: dict[str, str], name: str, attribute: str, value: str
) -> None:
    settings = load_settings({**fake_env, name: value})

    assert getattr(settings, attribute) == ""


@pytest.mark.parametrize("value", ["0", "-1", "soon"])
def test_invalid_speech_timeout_is_rejected(fake_env: dict[str, str], value: str) -> None:
    env = {**fake_env, "SPEECH_TIMEOUT_SECONDS": value}

    with pytest.raises(ConfigError, match="SPEECH_TIMEOUT_SECONDS"):
        load_settings(env)


def test_invalid_speech_base_url_is_rejected(fake_env: dict[str, str]) -> None:
    env = {**fake_env, "SPEECH_BASE_URL": "speech.example.test/v1"}

    with pytest.raises(ConfigError, match="SPEECH_BASE_URL"):
        load_settings(env)


def test_invalid_log_level_is_rejected(fake_env: dict[str, str]) -> None:
    env = {**fake_env, "LOG_LEVEL": "verbose"}

    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        load_settings(env)


def test_relative_data_dir_is_resolved_against_app_dir(fake_env: dict[str, str]) -> None:
    env = {**fake_env, "DATA_DIR": "var/notes"}

    assert load_settings(env).data_dir == APP_DIR / "var/notes"


def test_repr_hides_secrets(fake_env: dict[str, str]) -> None:
    speech_key = "test-speech-key-not-a-secret"
    settings = load_settings(
        {
            **fake_env,
            "SPEECH_API_KEY": speech_key,
            "SPEECH_MODEL": "test-whisper-model",
        }
    )

    text = repr(settings)

    assert fake_env["TELEGRAM_BOT_TOKEN"] not in text
    assert fake_env["LLM_API_KEY"] not in text
    assert speech_key not in text
    assert "test-model" in text
    assert "test-whisper-model" in text


def test_read_env_file_parses_supported_syntax(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "# комментарий",
                "",
                "export TELEGRAM_BOT_TOKEN=123:abc",
                'LLM_API_KEY="quoted value"',
                "LLM_MODEL='single quoted' # inline-комментарий",
                "DATA_DIR=data # inline-комментарий",
                "EMPTY=",
                "WITH_EQUALS=a=b",
            ]
        ),
        encoding="utf-8",
    )

    values = read_env_file(path)

    assert values == {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "LLM_API_KEY": "quoted value",
        "LLM_MODEL": "single quoted",
        "DATA_DIR": "data",
        "EMPTY": "",
        "WITH_EQUALS": "a=b",
    }


def test_read_missing_env_file_returns_empty_dict(tmp_path: Path) -> None:
    assert read_env_file(tmp_path / "missing.env") == {}


def test_read_env_file_reports_bad_line_without_echoing_content(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("ЭТО НЕ ПАРА\n", encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        read_env_file(path)

    message = str(exc_info.value)
    assert "строка 1" in message
    assert "ЭТО НЕ ПАРА" not in message


def test_read_env_file_reports_unclosed_quote(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text('LLM_API_KEY="oops\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="кавычка"):
        read_env_file(path)


def test_load_env_file_does_not_override_real_env(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "TELEGRAM_BOT_TOKEN=from-file\nONLY_FILE=file\n",
        encoding="utf-8",
    )
    environ = {"TELEGRAM_BOT_TOKEN": "from-env"}

    values = load_env_file(path, environ)

    assert values["TELEGRAM_BOT_TOKEN"] == "from-file"
    assert environ["TELEGRAM_BOT_TOKEN"] == "from-env"
    assert environ["ONLY_FILE"] == "file"

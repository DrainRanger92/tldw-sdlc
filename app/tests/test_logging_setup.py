"""Тесты централизованной очистки логов (T-016, P2 T-008).

Раньше ``cli.py`` писал ``log.exception`` в обход ``sanitize_log_text``:
при сбое сборки или остановки секрет, ключ или полный транскрипт могли
попасть в лог. Теперь ``configure_logging`` ставит ``logging.Filter`` на
обработчики, поэтому очистку проходят все записи — свои и сторонние.
Сеть и токены не нужны: секреты-ловушки заведомо фиктивные.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from support import FAKE_API_KEY, FAKE_BEARER_TOKEN, FAKE_BOT_TOKEN, FAKE_ENV_KEY, empty_env_file

from tldw.cli import EXIT_FAILURE, main
from tldw.errors import REDACTED
from tldw.logging_setup import SanitizingFilter, configure_logging

#: Длинный «транскрипт»: не должен попадать в лог целиком.
FAKE_TRANSCRIPT = " ".join(f"сегмент-{index}" for index in range(400))


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Вернуть глобальное логирование после теста (configure_logging меняет root)."""
    root = logging.getLogger()
    handlers = root.handlers[:]
    filters = root.filters[:]
    level = root.level
    disabled = root.disabled
    yield
    root.handlers[:] = handlers
    root.filters[:] = filters
    root.setLevel(level)
    root.disabled = disabled


def test_configure_logging_installs_the_central_filter() -> None:
    configure_logging("INFO")

    handlers = logging.getLogger().handlers
    assert handlers, "configure_logging должен создать обработчик"
    assert all(
        any(isinstance(log_filter, SanitizingFilter) for log_filter in handler.filters)
        for handler in handlers
    )


def test_third_party_logger_does_not_write_secrets_or_full_transcript(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("DEBUG")

    leaked = (
        f"Authorization: Bearer {FAKE_BEARER_TOKEN} "
        f"LLM_API_KEY={FAKE_ENV_KEY} key {FAKE_API_KEY} "
        f"url https://api.telegram.org/bot{FAKE_BOT_TOKEN}/sendMessage "
        f"transcript {FAKE_TRANSCRIPT}"
    )
    try:
        raise RuntimeError(leaked)
    except RuntimeError:
        logging.getLogger("httpx").exception("HTTP request failed")

    logged = capsys.readouterr().err
    for secret in (FAKE_BEARER_TOKEN, FAKE_ENV_KEY, FAKE_API_KEY, FAKE_BOT_TOKEN):
        assert secret not in logged
    assert FAKE_TRANSCRIPT not in logged
    assert "фрагмент обрезан" in logged
    assert REDACTED in logged
    assert "HTTP request failed" in logged


def test_multiline_third_party_transcript_is_collapsed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("DEBUG")
    transcript = "\n".join(f"    строка-{index} " + "x" * 40 for index in range(150))

    logging.getLogger("some.library").error("transcript:\n%s", transcript)

    logged = capsys.readouterr().err
    assert transcript not in logged
    assert "фрагмент обрезан" in logged


def test_cli_build_failure_redacts_secrets_in_log_and_stderr(
    fake_env: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = f"Authorization: Bearer {FAKE_BEARER_TOKEN} key {FAKE_API_KEY}"

    def broken_build(settings):  # noqa: ANN001, ARG001 - сигнатура-заглушка
        raise RuntimeError(f"сборка упала: {secret}")

    monkeypatch.setattr("tldw.cli.build_application", broken_build)

    exit_code = main(
        ["--check-config", "--env-file", str(empty_env_file(tmp_path))],
        environ=fake_env,
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_FAILURE
    for output in (captured.err, captured.out):
        for value in (FAKE_BEARER_TOKEN, FAKE_API_KEY):
            assert value not in output
    assert "Не удалось запустить TLDW" in captured.err
    assert "сборка упала" in captured.err


def test_cli_stop_failure_redacts_secrets_in_log(
    fake_env: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = f"Authorization: Bearer {FAKE_BEARER_TOKEN} LLM_API_KEY={FAKE_ENV_KEY}"

    def broken_runner(application):  # noqa: ANN001, ARG001 - сигнатура-заглушка
        raise RuntimeError(f"остановка упала: {secret} {FAKE_TRANSCRIPT}")

    exit_code = main(
        ["--env-file", str(empty_env_file(tmp_path))],
        environ=fake_env,
        run_application=broken_runner,
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_FAILURE
    for value in (FAKE_BEARER_TOKEN, FAKE_ENV_KEY):
        assert value not in captured.err
    assert FAKE_TRANSCRIPT not in captured.err
    assert "остановка упала" in captured.err
    assert "Traceback (most recent call last)" in captured.err


def test_known_error_is_visible_with_configured_error_level(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """При LOG_LEVEL=ERROR причина известной ошибки остаётся в выводе (T-016)."""
    from tldw.adapters.youtube import NotYouTubeURLError
    from tldw.errors import log_failure

    configure_logging("ERROR")

    log_failure(NotYouTubeURLError(), logger=logging.getLogger("tldw.test.known"))

    logged = capsys.readouterr().err
    assert "NotYouTubeURLError" in logged
    assert "не ссылка на видео YouTube" in logged

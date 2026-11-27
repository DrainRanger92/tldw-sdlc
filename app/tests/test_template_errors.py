"""Тесты понятных ошибок шаблонного сценария и приватных логов (T-009).

Сценарий шаблона не должен ни падать молча, ни раскрывать данные: на каждый
сбой пользователь получает свой русский текст, прежний активный шаблон
остаётся в силе, кандидат и заметка не создаются, а в лог уходят только
причина, стадия, происхождение шаблона и число разделов.

Проверяется:

- каждый известный сбой (LLM не ответил и таймаут, битый и пустой JSON, нет
  изменений, больше 12 разделов, пустой/длинный/повторяющийся заголовок,
  описание длиннее 4000 символов, ошибка записи и сброса) даёт собственное
  сообщение на русском и не меняет активный шаблон;
- неизвестный сбой даёт общее сообщение и ``ERROR`` с очищенным стектрейсом;
- ``log_failure``/``describe_error`` принимают необязательный контекст, но
  старые вызовы пишут прежний текст «Сбой обработки видео»;
- в логах есть происхождение шаблона и число разделов, но нет полного описания,
  тона, JSON кандидата, ключей и аудио — это проверяют секреты-ловушки;
- ошибка сохранения не теряет прежний файл шаблона, ошибка сброса не сообщает
  об успехе, YouTube и LLM в этих сценариях не вызываются.

Сеть и токены не нужны: стенд — общий ``tests/support.py`` (настоящие
``Dispatcher``, обработчики и сервисы, фейковые только внешние границы), а
файловые кейсы работают в ``tmp_path``.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pytest
from aiogram.methods import EditMessageText
from support import (
    FAKE_API_KEY,
    FAKE_BEARER_TOKEN,
    FAKE_BOT_TOKEN,
    FAKE_ENV_KEY,
    FakeTemplateParser,
    FakeTemplateRepository,
    TemplateStand,
    inline_buttons,
    run_template_scenario,
)

from tldw.adapters.llm import (
    MESSAGE_LLM_BAD_RESPONSE,
    MESSAGE_LLM_TIMEOUT,
    MESSAGE_LLM_UNAVAILABLE,
    MESSAGE_TEMPLATE_PATCH_EMPTY,
    MESSAGE_TEMPLATE_PATCH_INVALID,
    LLMProviderError,
    LLMResponseError,
    LLMTimeoutError,
)
from tldw.errors import (
    DEFAULT_FAILURE_CONTEXT,
    MESSAGE_UNEXPECTED,
    REDACTED,
    describe_error,
    log_failure,
)
from tldw.handlers.template import (
    ACTION_EDIT,
    ACTION_RESET,
    ACTION_RESET_CONFIRM,
    ACTION_SAVE,
    BUTTON_SAVE,
    MESSAGE_DESCRIPTION_RETRY,
    MESSAGE_DESCRIPTION_TOO_LONG,
    MESSAGE_RESET_DONE,
    MESSAGE_SAVED,
    MESSAGE_STALE_CANDIDATE,
)
from tldw.services.template import (
    MAX_DESCRIPTION_LENGTH,
    MESSAGE_HEADING_DUPLICATE,
    MESSAGE_HEADING_EMPTY,
    MESSAGE_HEADING_TOO_LONG,
    MESSAGE_PARSE_NO_CHANGES,
    MESSAGE_PARSE_UNCHANGED,
    MESSAGE_SECTIONS_RANGE,
    NoteTemplate,
    TemplateParseError,
    TemplateService,
)
from tldw.storage.templates import (
    MESSAGE_TEMPLATE_DELETE_FAILED,
    MESSAGE_TEMPLATE_STORAGE_FAILED,
    TemplateStore,
)

#: Логгер сценария: по нему фильтруются записи, чужие WARNING не мешают.
LOGGER_NAME = "tldw.handlers.template"

#: Отличимые пометки пользовательских данных: их не должно быть ни в логе, ни
#: в ответах об ошибке. Значения — ловушки, а не секреты.
DESCRIPTION_MARKER = "описание-ловушка-9f3a"
TONE_MARKER = "тон-ловушка-7b1c"
AUDIO_MARKER = "audio-bytes-deadbeef"
DESCRIPTION = (
    f"Разделы: {DESCRIPTION_MARKER}, Коротко. Тон: {TONE_MARKER}. "
    f"Аудио: {AUDIO_MARKER}"
)
#: Кириллица в сообщении: доказательство, что текст написан для пользователя.
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

#: Пользовательский шаблон, заранее сохранённый у пользователя 42.
SAVED_TEMPLATE = NoteTemplate(
    sections=("Коротко", "Что попробовать"),
    tone="строго по делу",
)
#: Кандидат успешного разбора: два раздела, как у сохранённого шаблона.
CANDIDATE_SECTIONS = ("Коротко", "Примеры кода", "Что попробовать")


def raised(exc: BaseException) -> BaseException:
    """Поднять исключение и вернуть его с настоящим стектрейсом."""
    try:
        raise exc
    except BaseException as caught:  # noqa: BLE001 - тесту нужен именно объект
        return caught


def failure_records(
    caplog: pytest.LogCaptureFixture,
    *,
    level: int | None = None,
) -> list[logging.LogRecord]:
    """Записи логгера сценария, при желании — только заданного уровня."""
    return [
        record
        for record in caplog.records
        if record.name == LOGGER_NAME and (level is None or record.levelno == level)
    ]


def assert_no_private_data(log_text: str) -> None:
    """В логе нет описания, тона, аудио, JSON кандидата и фиктивных секретов."""
    for marker in (DESCRIPTION_MARKER, TONE_MARKER, AUDIO_MARKER):
        assert marker not in log_text, "пользовательские данные не пишутся в лог"
    for secret in (FAKE_BOT_TOKEN, FAKE_BEARER_TOKEN, FAKE_ENV_KEY, FAKE_API_KEY):
        assert secret not in log_text, "секрет не должен попадать в лог"
    for json_fragment in ('"sections"', '"heading"', '"tone"'):
        assert json_fragment not in log_text, "JSON кандидата не пишется в лог"


#: Известные сбои разбора и валидации: (параметры фейкового парсера, текст).
KNOWN_FAILURES = [
    pytest.param(
        {"error": LLMProviderError()},
        MESSAGE_LLM_UNAVAILABLE,
        id="llm-no-answer",
    ),
    pytest.param(
        {"error": LLMTimeoutError()},
        MESSAGE_LLM_TIMEOUT,
        id="llm-timeout",
    ),
    pytest.param(
        {"error": LLMResponseError()},
        MESSAGE_LLM_BAD_RESPONSE,
        id="llm-bad-response",
    ),
    pytest.param(
        {"error": TemplateParseError(MESSAGE_TEMPLATE_PATCH_INVALID)},
        MESSAGE_TEMPLATE_PATCH_INVALID,
        id="broken-json",
    ),
    pytest.param(
        {"error": TemplateParseError(MESSAGE_TEMPLATE_PATCH_EMPTY)},
        MESSAGE_TEMPLATE_PATCH_EMPTY,
        id="empty-json",
    ),
    pytest.param(
        {"sections": None, "tone": None},
        MESSAGE_PARSE_NO_CHANGES,
        id="no-changes",
    ),
    pytest.param(
        {"sections": SAVED_TEMPLATE.sections, "tone": SAVED_TEMPLATE.tone},
        MESSAGE_PARSE_UNCHANGED,
        id="unchanged",
    ),
    pytest.param(
        {"sections": tuple(f"Раздел {index}" for index in range(13)), "tone": None},
        MESSAGE_SECTIONS_RANGE,
        id="too-many-sections",
    ),
    pytest.param(
        {"sections": ("", "Другой раздел"), "tone": None},
        MESSAGE_HEADING_EMPTY,
        id="empty-heading",
    ),
    pytest.param(
        {"sections": ("я" * 81,), "tone": None},
        MESSAGE_HEADING_TOO_LONG,
        id="long-heading",
    ),
    pytest.param(
        {"sections": ("Итог", "итог"), "tone": None},
        MESSAGE_HEADING_DUPLICATE,
        id="duplicate-heading",
    ),
]


@pytest.mark.parametrize(("parser_kwargs", "expected"), KNOWN_FAILURES)
def test_each_known_failure_shows_own_message_and_keeps_active_template(
    parser_kwargs: dict[str, object],
    expected: str,
) -> None:
    """Сбой разбора или валидации: свой текст, прежний шаблон, без кандидата."""
    assert CYRILLIC_RE.search(expected), "сообщение должно быть на русском"
    parser = FakeTemplateParser(**parser_kwargs)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)

        answer = stand.last_text
        assert expected in answer, f"пользователь должен увидеть свой текст: {answer!r}"
        assert MESSAGE_DESCRIPTION_RETRY in answer
        assert "Traceback" not in answer
        # Кандидат не создан: «Сохранить» нечего сохранять.
        await stand.click(ACTION_SAVE)
        assert stand.last_text == MESSAGE_STALE_CANDIDATE

    stand = run_template_scenario(
        scenario,
        parser=parser,
        user_template=SAVED_TEMPLATE,
    )

    assert stand.saves == [], "активный шаблон не меняется при сбое"
    assert stand.repository is not None
    assert stand.repository.records[42] == (SAVED_TEMPLATE.sections, SAVED_TEMPLATE.tone)
    assert stand.templates.get_active(42).template == SAVED_TEMPLATE
    assert stand.parser.calls and stand.parser.calls[-1][0] == DESCRIPTION


class FailAfterLoadsRepository(FakeTemplateRepository):
    """Хранилище, которое отдаёт шаблон несколько раз, а затем падает при чтении."""

    def __init__(self, *, fail_after: int) -> None:
        super().__init__()
        self._fail_after = fail_after

    def load(self, user_id: int) -> tuple[tuple[str, ...], str] | None:
        self.loads.append(user_id)
        if len(self.loads) > self._fail_after:
            raise OSError("диск недоступен")
        return self.records.get(user_id)


def test_failed_parse_discards_previous_candidate() -> None:
    """Неудачная правка отбрасывает прежнего кандидата: его нельзя сохранить."""
    parser = FakeTemplateParser(sections=("Новый раздел",), tone="новый тон")

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)
        assert "1. Новый раздел" in stand.last_text

        parser.error = TemplateParseError(MESSAGE_PARSE_NO_CHANGES)
        await stand.send("Переформулированное описание")
        assert MESSAGE_PARSE_NO_CHANGES in stand.last_text

        # «Сохранить» после сбоя не должно сохранить прежнего кандидата.
        await stand.click(ACTION_SAVE)
        assert stand.last_text == MESSAGE_STALE_CANDIDATE

    stand = run_template_scenario(scenario, parser=parser, user_template=SAVED_TEMPLATE)

    assert stand.saves == []
    assert stand.templates.get_active(42).template == SAVED_TEMPLATE


def test_context_read_error_does_not_hide_original_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Если контекст прочитать нельзя, пользователь всё равно видит свой текст."""
    repository = FailAfterLoadsRepository(fail_after=2)
    parser = FakeTemplateParser(error=TemplateParseError(MESSAGE_PARSE_NO_CHANGES))

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        with caplog.at_level(logging.DEBUG):
            await stand.send(DESCRIPTION)
        assert MESSAGE_PARSE_NO_CHANGES in stand.last_text
        assert "Traceback" not in stand.last_text

    run_template_scenario(
        scenario,
        parser=parser,
        repository=repository,
        user_template=SAVED_TEMPLATE,
    )

    warnings = failure_records(caplog, level=logging.WARNING)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert message.startswith("Сбой разбора шаблона: TemplateParseError")
    assert MESSAGE_PARSE_NO_CHANGES in message
    assert_no_private_data(caplog.text)


def test_too_long_description_is_rejected_without_llm_and_without_logging_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Описание длиннее 4000 символов: свой текст, без вызова LLM и без утечки."""
    too_long = f"{DESCRIPTION_MARKER} " + "я" * MAX_DESCRIPTION_LENGTH
    parser = FakeTemplateParser(sections=CANDIDATE_SECTIONS, tone=None)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        with caplog.at_level(logging.DEBUG):
            await stand.send(too_long)

        assert stand.last_text == MESSAGE_DESCRIPTION_TOO_LONG.format(
            length=len(too_long.strip()),
            limit=MAX_DESCRIPTION_LENGTH,
        )
        assert "Traceback" not in stand.last_text
        # Ввод продолжается: следующее описание разбирается как обычно.
        await stand.send(DESCRIPTION)

    stand = run_template_scenario(
        scenario,
        parser=parser,
        user_template=SAVED_TEMPLATE,
    )

    assert len(stand.parser.calls) == 1, "длинное описание не уходит в разбор"
    assert stand.parser.calls[0][0] == DESCRIPTION
    assert_no_private_data(caplog.text)
    assert stand.templates.get_active(42).template == SAVED_TEMPLATE


def test_known_failure_logs_warning_with_reason_origin_and_section_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Известный сбой — ``WARNING`` с причиной, стадией, происхождением и счётчиком."""
    parser = FakeTemplateParser(error=LLMTimeoutError())

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        with caplog.at_level(logging.DEBUG):
            await stand.send(DESCRIPTION)

    run_template_scenario(scenario, parser=parser, user_template=SAVED_TEMPLATE)

    warnings = failure_records(caplog, level=logging.WARNING)
    assert len(warnings) == 1, "известный сбой логируется одним предупреждением"
    message = warnings[0].getMessage()
    assert message.startswith("Сбой разбора шаблона (пользовательский, разделов: 2)")
    assert "LLMTimeoutError" in message
    assert MESSAGE_LLM_TIMEOUT in message
    assert "Traceback" not in message, "у известной ошибки нет стектрейса"
    assert failure_records(caplog, level=logging.ERROR) == []
    assert_no_private_data(caplog.text)


def test_unknown_failure_gives_generic_message_and_cleaned_error_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Неизвестный сбой: общее сообщение и ``ERROR`` с очищенным стектрейсом."""
    leaked = (
        f"boom {FAKE_BOT_TOKEN} Authorization: Bearer {FAKE_BEARER_TOKEN} "
        f"LLM_API_KEY={FAKE_ENV_KEY} {FAKE_API_KEY}"
    )
    parser = FakeTemplateParser(error=RuntimeError(leaked))

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        with caplog.at_level(logging.DEBUG):
            await stand.send(DESCRIPTION)
        assert stand.last_text == f"{MESSAGE_UNEXPECTED}\n\n{MESSAGE_DESCRIPTION_RETRY}"
        await stand.click(ACTION_SAVE)
        assert stand.last_text == MESSAGE_STALE_CANDIDATE

    stand = run_template_scenario(
        scenario,
        parser=parser,
        user_template=SAVED_TEMPLATE,
    )

    errors = failure_records(caplog, level=logging.ERROR)
    assert len(errors) == 1, "неизвестный сбой логируется один раз на уровне ERROR"
    message = errors[0].getMessage()
    assert message.startswith("Неизвестный сбой разбора шаблона (пользовательский, разделов: 2)")
    assert "RuntimeError: boom" in message
    assert "Traceback (most recent call last)" in message
    for secret in (FAKE_BOT_TOKEN, FAKE_BEARER_TOKEN, FAKE_ENV_KEY, FAKE_API_KEY):
        assert secret not in message
    assert REDACTED in message
    assert_no_private_data(caplog.text)
    assert stand.saves == []
    assert stand.templates.get_active(42).template == SAVED_TEMPLATE


def test_successful_scenario_logs_origin_and_count_without_private_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Успешный сценарий: в логе происхождение и число разделов, без данных."""
    parser = FakeTemplateParser(sections=CANDIDATE_SECTIONS, tone=TONE_MARKER)

    async def scenario(stand: TemplateStand) -> None:
        with caplog.at_level(logging.DEBUG):
            await stand.send("/template")
            await stand.click(ACTION_EDIT)
            await stand.send(DESCRIPTION)
            await stand.click(ACTION_SAVE)

    run_template_scenario(scenario, parser=parser, user_template=SAVED_TEMPLATE)

    records = [record for record in caplog.records if record.name == LOGGER_NAME]
    assert records, "сценарий шаблона должен оставлять записи в логе"
    messages = "\n".join(record.getMessage() for record in records)
    assert "пользовательский" in messages
    assert f"разделов: {len(SAVED_TEMPLATE.sections)}" in messages
    assert f"разделов: {len(CANDIDATE_SECTIONS)}" in messages
    for heading in CANDIDATE_SECTIONS:
        assert heading not in messages, "заголовки разделов в лог не пишутся"
    assert_no_private_data(caplog.text)
    assert "Traceback" not in caplog.text


def test_save_error_keeps_previous_template_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ошибка записи: понятный текст, старый файл шаблона цел, кандидат не сохранён."""
    store = TemplateStore(tmp_path / "data")
    target = store.templates_dir / f"{42}.json"
    parser = FakeTemplateParser(sections=("Новый раздел",), tone="новый тон")

    async def scenario(stand: TemplateStand) -> None:
        stand.templates.save(42, SAVED_TEMPLATE)
        previous = target.read_text(encoding="utf-8")

        def broken_replace(source: str | Path, destination: str | Path) -> None:
            raise OSError("диск переполнен")

        monkeypatch.setattr(os, "replace", broken_replace)

        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)
        await stand.click(ACTION_SAVE)

        assert stand.last_text == MESSAGE_TEMPLATE_STORAGE_FAILED
        edits = [request for request in stand.requests if isinstance(request, EditMessageText)]
        assert BUTTON_SAVE in inline_buttons(edits[-1].reply_markup), (
            "после ошибки записи попытку можно повторить"
        )
        assert stand.templates.get_active(42).template == SAVED_TEMPLATE
        assert stand.source.calls == [], "YouTube не вызывается"
        assert stand.summarizer.calls == [], "LLM не вызывается"
        assert target.read_text(encoding="utf-8") == previous, (
            "прежний файл шаблона не теряется"
        )
        assert [item.name for item in store.templates_dir.iterdir()] == [f"{42}.json"]

        # Кандидат не потерян: после восстановления записи «Сохранить» работает.
        monkeypatch.undo()
        await stand.click(ACTION_SAVE)
        assert stand.last_text.startswith(MESSAGE_SAVED)

    run_template_scenario(scenario, templates=TemplateService(store), parser=parser)

    assert store.load(42) == (("Новый раздел",), "новый тон")
    assert not list((tmp_path / "data").rglob("*.md")), "заметка не создаётся"


def test_save_error_log_has_stage_origin_and_count_without_private_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ошибка записи логируется как сбой сохранения шаблона, без описания и тона."""
    store = TemplateStore(tmp_path / "data")
    parser = FakeTemplateParser(sections=("Новый раздел",), tone=TONE_MARKER)

    async def scenario(stand: TemplateStand) -> None:
        stand.templates.save(42, SAVED_TEMPLATE)

        def broken_replace(source: str | Path, destination: str | Path) -> None:
            raise OSError("диск переполнен")

        monkeypatch.setattr(os, "replace", broken_replace)

        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        with caplog.at_level(logging.DEBUG):
            await stand.send(DESCRIPTION)
            await stand.click(ACTION_SAVE)

    run_template_scenario(scenario, templates=TemplateService(store), parser=parser)

    warnings = failure_records(caplog, level=logging.WARNING)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert message.startswith("Сбой сохранения шаблона (пользовательский, разделов: 2)")
    assert "TemplateStorageError" in message
    assert MESSAGE_TEMPLATE_STORAGE_FAILED in message
    assert "Traceback" not in message
    assert_no_private_data(caplog.text)
    assert store.load(42) == (SAVED_TEMPLATE.sections, SAVED_TEMPLATE.tone)


def test_reset_error_keeps_template_and_reports_no_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ошибка сброса: понятный текст вместо успеха, файл и шаблон на месте."""
    store = TemplateStore(tmp_path / "data")
    target = store.templates_dir / f"{42}.json"

    async def scenario(stand: TemplateStand) -> None:
        stand.templates.save(42, SAVED_TEMPLATE)

        def broken_unlink(self: Path, missing_ok: bool = False) -> None:
            raise OSError("нет прав")

        monkeypatch.setattr(Path, "unlink", broken_unlink)

        await stand.send("/template")
        await stand.click(ACTION_RESET)
        with caplog.at_level(logging.DEBUG):
            await stand.click(ACTION_RESET_CONFIRM)

        answer = stand.last_text
        assert answer == MESSAGE_TEMPLATE_DELETE_FAILED
        assert MESSAGE_RESET_DONE not in answer
        assert "✅" not in answer, "об успехе сообщать нельзя"
        assert "Traceback" not in answer
        assert stand.templates.get_active(42).template == SAVED_TEMPLATE
        assert stand.source.calls == [], "YouTube не вызывается"
        assert stand.summarizer.calls == [], "LLM не вызывается"

    run_template_scenario(scenario, templates=TemplateService(store))

    assert target.is_file(), "прежний файл шаблона не теряется"
    assert store.load(42) == (SAVED_TEMPLATE.sections, SAVED_TEMPLATE.tone)
    warnings = failure_records(caplog, level=logging.WARNING)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert message.startswith("Сбой сброса шаблона (пользовательский, разделов: 2)")
    assert "TemplateStorageError" in message
    assert MESSAGE_TEMPLATE_DELETE_FAILED in message
    assert_no_private_data(caplog.text)


# --- Контракт ``errors``: необязательный контекст и прежние тексты ----------


def test_log_failure_without_context_keeps_previous_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Старый вызов ``log_failure`` пишет прежний текст «Сбой обработки видео»."""
    error = TemplateParseError("причина разбора")

    with caplog.at_level(logging.WARNING, logger="tldw.errors"):
        report = log_failure(error)

    records = [record for record in caplog.records if record.name == "tldw.errors"]
    assert len(records) == 1
    assert records[0].getMessage() == "Сбой обработки видео: TemplateParseError: причина разбора"
    assert report.context == DEFAULT_FAILURE_CONTEXT
    assert report.level == logging.WARNING
    assert report.traceback_text is None


def test_log_failure_context_names_template_and_is_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Контекст говорит о шаблоне, очищается от секретов и не ломает строку."""
    context = (
        f"разбора шаблона (встроенный, разделов: 5) {FAKE_API_KEY}\nвторая строка"
    )

    with caplog.at_level(logging.WARNING, logger="tldw.errors"):
        report = log_failure(TemplateParseError("нет изменений"), context=context)

    records = [record for record in caplog.records if record.name == "tldw.errors"]
    assert len(records) == 1
    message = records[0].getMessage()
    assert message.startswith("Сбой разбора шаблона (встроенный, разделов: 5)")
    assert "TemplateParseError: нет изменений" in message
    assert FAKE_API_KEY not in message
    assert REDACTED in message
    assert "\n" not in message, "контекст остаётся одной строкой"
    assert report.context.startswith("разбора шаблона (встроенный, разделов: 5)")
    assert report.level == logging.WARNING


def test_unknown_failure_with_context_is_error_with_cleaned_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Неизвестный сбой с контекстом: ``ERROR``, стектрейс и вырезанные секреты."""
    error = raised(RuntimeError(f"boom Authorization: Bearer {FAKE_BEARER_TOKEN}"))

    with caplog.at_level(logging.ERROR, logger="tldw.errors"):
        report = log_failure(error, context="сохранения шаблона (пользовательский, разделов: 2)")

    records = [record for record in caplog.records if record.name == "tldw.errors"]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    message = records[0].getMessage()
    assert message.startswith(
        "Неизвестный сбой сохранения шаблона (пользовательский, разделов: 2)"
    )
    assert "RuntimeError: boom" in message
    assert "Traceback (most recent call last)" in message
    assert FAKE_BEARER_TOKEN not in message
    assert REDACTED in message
    assert report.level == logging.ERROR
    assert report.traceback_text is not None


def test_empty_context_falls_back_to_generic_failure_text() -> None:
    """Пустой контекст — не «пусто», а прежняя запись об обработке видео."""

    report = describe_error(TemplateParseError("причина"), context="  :  ")

    assert report.context == DEFAULT_FAILURE_CONTEXT
    assert report.user_message == "причина"

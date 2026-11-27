"""Тесты Telegram-сценария шаблона заметки (T-007).

Сеть и токены не нужны: апдейты проходят через настоящие ``Dispatcher``,
``TemplateHandler``, ``TemplateService`` и роутер, подменены только внешние
границы — сессия Telegram (``FakeSession``), разбор описания
(``FakeTemplateParser`` вместо LLM), YouTube и LLM-конспект. Проверяются:

- ``/template`` и кнопка «Шаблон» открывают показ активного шаблона в любой
  момент и не вызывают LLM/YouTube;
- показ «встроенного» и пользовательского шаблона, нумерация разделов и тон;
- полный FSM-сценарий «Изменить → описание → кандидат → Сохранить», разбор в
  рабочем потоке, «Исправить» и «Отмена» без изменения активного шаблона;
- сброс только после подтверждения, идемпотентно и без LLM;
- allowlist до показа, до изменения и до любого callback;
- ссылка во время ввода описания не запускает обработку видео (порядок
  регистрации обработчиков), устаревшая кнопка не сохраняет шаблон дважды,
  кандидат не переживает перезапуск;
- в ответах и логах нет секретов и полного описания шаблона.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from support import (
    FAKE_API_KEY,
    FAKE_BEARER_TOKEN,
    FAKE_BOT_TOKEN,
    FAKE_ENV_KEY,
    VIDEO_URL,
    FakeSession,
    FakeSummarizer,
    FakeTemplateParser,
    FakeTemplateRepository,
    FakeVideoSource,
    TemplateStand,
    expected_numbering,
    inline_buttons,
    make_callback_update,
    make_harness,
    make_summary,
    make_update,
    make_video,
    reply_buttons,
    run_template_scenario,
    sent_messages,
    template_action,
    texts,
)

from tldw.errors import (
    MESSAGE_UNEXPECTED,
    MESSAGE_UNEXPECTED_TEMPLATE,
    REDACTED,
    TemplateParseError,
)
from tldw.handlers import (
    MESSAGE_ACCEPTED,
    MESSAGE_ACCESS_DENIED,
    MESSAGE_NEED_LINK,
    TEMPLATE_BUTTON_TEXT,
    TemplateHandler,
    create_router,
)
from tldw.handlers.template import (
    ACTION_CANCEL,
    ACTION_CLOSE,
    ACTION_EDIT,
    ACTION_FIX,
    ACTION_RESET,
    ACTION_RESET_CONFIRM,
    ACTION_SAVE,
    BUTTON_CANCEL,
    BUTTON_CLOSE,
    BUTTON_EDIT,
    BUTTON_FIX,
    BUTTON_RESET,
    BUTTON_RESET_CONFIRM,
    BUTTON_SAVE,
    MESSAGE_ASK_DESCRIPTION,
    MESSAGE_CLOSED,
    MESSAGE_DESCRIPTION_EMPTY,
    MESSAGE_DESCRIPTION_RETRY,
    MESSAGE_LINK_WHILE_CANDIDATE,
    MESSAGE_LINK_WHILE_DESCRIPTION,
    MESSAGE_ORIGIN_BUILTIN,
    MESSAGE_ORIGIN_USER,
    MESSAGE_PARSING,
    MESSAGE_RESET_ASK,
    MESSAGE_RESET_DONE,
    MESSAGE_SAVED,
    MESSAGE_STALE_CALLBACK,
    MESSAGE_STALE_CANDIDATE,
    MESSAGE_TONE_MISSING,
    MESSAGE_VIEW_ACTIONS,
    MESSAGE_VIEW_TITLE,
)
from tldw.services.summary import SummaryService
from tldw.services.template import (
    BUILTIN_SECTIONS,
    MAX_DESCRIPTION_LENGTH,
    NoteTemplate,
    TemplateService,
)
from tldw.storage.templates import TemplateStore

#: Описание, которое «разбирает» фейковый парсер: текст важен только как запись вызова.
DESCRIPTION = "Разделы: Коротко, Примеры кода. Тон: короче, с акцентом на код"
#: Пользовательский шаблон, заранее сохранённый в фейковом хранилище.
SAVED_TEMPLATE = NoteTemplate(sections=("Коротко", "Что попробовать"), tone="строго по делу")


class FailingEditSession(FakeSession):
    """Сессия, у которой редактирование сообщения падает как в Telegram."""

    def __init__(self) -> None:
        super().__init__()
        self.edits_attempted = 0

    async def make_request(
        self,
        bot: Bot,
        method: Any,
        timeout: int | None = None,
    ) -> Any:
        if isinstance(method, EditMessageText):
            self.edits_attempted += 1
            raise TelegramBadRequest(method=method, message="message is not modified")
        return await super().make_request(bot, method, timeout)


# --- Показ активного шаблона -------------------------------------------------


def test_command_shows_builtin_template_without_llm_and_youtube() -> None:
    """``/template`` показывает встроенный шаблон: нумерация, тон, действия."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")

    stand = run_template_scenario(scenario)

    assert len(stand.texts) == 2
    view = stand.texts[0]
    assert MESSAGE_VIEW_TITLE in view
    assert MESSAGE_ORIGIN_BUILTIN in view
    assert expected_numbering(BUILTIN_SECTIONS) in view
    assert MESSAGE_TONE_MISSING in view
    assert stand.texts[1] == MESSAGE_VIEW_ACTIONS

    assert inline_buttons(sent_messages(stand.requests)[1].reply_markup) == [
        BUTTON_EDIT,
        BUTTON_RESET,
        BUTTON_CLOSE,
    ]
    assert reply_buttons(sent_messages(stand.requests)[0].reply_markup) == [
        TEMPLATE_BUTTON_TEXT
    ]

    assert stand.parser.calls == [], "показ шаблона не должен вызывать LLM"
    assert stand.source.calls == [], "показ шаблона не должен обращаться к YouTube"
    assert stand.summarizer.calls == []


def test_text_button_opens_the_same_view_as_command() -> None:
    """Кнопка «Шаблон» обрабатывается как команда и даёт тот же экран."""

    def view_for(entry: str) -> list[str]:
        async def scenario(stand: TemplateStand) -> None:
            await stand.send(entry)

        return run_template_scenario(scenario).texts

    assert view_for(TEMPLATE_BUTTON_TEXT) == view_for("/template")


def test_view_shows_user_template_sections_in_order_and_tone() -> None:
    """Пользовательский шаблон показывается разделами по порядку и тоном."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")

    stand = run_template_scenario(scenario, user_template=SAVED_TEMPLATE)

    view = stand.texts[0]
    assert MESSAGE_ORIGIN_USER in view
    assert MESSAGE_ORIGIN_BUILTIN not in view
    assert expected_numbering(SAVED_TEMPLATE.sections) in view
    assert SAVED_TEMPLATE.tone in view
    assert MESSAGE_TONE_MISSING not in view
    assert stand.parser.calls == []


def test_open_scenario_in_the_middle_of_editing_discards_candidate() -> None:
    """``/template`` открывается в любой момент и не сохраняет кандидата."""
    parser = FakeTemplateParser(sections=("Черновик",), tone=None)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)
        assert "1. Черновик" in stand.last_text
        await stand.send("/template")

    stand = run_template_scenario(scenario, parser=parser)

    assert MESSAGE_ORIGIN_BUILTIN in stand.texts[-2]
    assert stand.saves == []


# --- Полный сценарий изменения ----------------------------------------------


def test_edit_description_candidate_then_save() -> None:
    """Полный сценарий: изменение, кандидат, сохранение только по «Сохранить»."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        assert stand.last_text == MESSAGE_ASK_DESCRIPTION

        await stand.send(f"  {DESCRIPTION}  ")
        assert stand.last_text and MESSAGE_PARSING in stand.texts

        candidate = stand.last_text
        assert "1. Коротко" in candidate
        assert "2. Примеры кода" in candidate
        assert "короче" in candidate
        assert "{" not in candidate and "sections" not in candidate
        assert inline_buttons(sent_messages(stand.requests)[-1].reply_markup) == [
            BUTTON_SAVE,
            BUTTON_FIX,
            BUTTON_CANCEL,
        ]
        assert stand.saves == [], "до «Сохранить» шаблон не сохраняется"
        assert stand.templates.get_active(42).is_builtin

        await stand.click(ACTION_SAVE)

    stand = run_template_scenario(scenario)

    assert stand.parser.calls == [(DESCRIPTION, BUILTIN_SECTIONS, "")]
    assert stand.saves == [(42, (("Коротко", "Примеры кода"), "короче"))]
    assert MESSAGE_SAVED in stand.last_text
    assert expected_numbering(("Коротко", "Примеры кода")) in stand.last_text
    active = stand.templates.get_active(42)
    assert not active.is_builtin
    assert active.sections == ("Коротко", "Примеры кода")
    assert active.tone == "короче"


def test_template_text_is_sent_as_plain_data_not_markup() -> None:
    """Заголовки и тон — данные: разметка не парсится и не ломает сообщение."""
    parser = FakeTemplateParser(sections=("<b>Код</b> & примеры",), tone="A < B & C")

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)

    stand = run_template_scenario(scenario, parser=parser)

    candidate = stand.last_text
    assert "1. <b>Код</b> & примеры" in candidate
    assert "A < B & C" in candidate
    messages_with_text = [
        request
        for request in stand.requests
        if isinstance(request, SendMessage) and request.text
    ]
    assert all(request.parse_mode is None for request in messages_with_text), (
        "динамический текст шаблона должен уходить без HTML-разбора"
    )


def test_description_is_parsed_in_a_worker_thread() -> None:
    """Разбор описания уходит в рабочий поток и не блокирует event loop."""
    loop_threads: list[int] = []

    async def scenario(stand: TemplateStand) -> None:
        loop_threads.append(threading.get_ident())
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)

    stand = run_template_scenario(scenario)

    assert stand.parser.thread_ids, "парсер не был вызван"
    assert all(thread_id != loop_threads[0] for thread_id in stand.parser.thread_ids), (
        "разбор должен выполняться вне потока event loop (asyncio.to_thread)"
    )


def test_parser_receives_current_active_template() -> None:
    """Разбор получает текущий активный шаблон: правка тона сохраняет разделы."""
    parser = FakeTemplateParser(sections=None, tone="совсем коротко")

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send("Пиши совсем коротко")

    stand = run_template_scenario(scenario, parser=parser, user_template=SAVED_TEMPLATE)

    assert parser.calls == [("Пиши совсем коротко", SAVED_TEMPLATE.sections, SAVED_TEMPLATE.tone)]
    assert expected_numbering(SAVED_TEMPLATE.sections) in stand.last_text
    assert "совсем коротко" in stand.last_text
    assert stand.saves == []


def test_fix_discards_candidate_and_new_description_replaces_it() -> None:
    """«Исправить» возвращает к вводу, новое описание заменяет кандидата."""
    parser = FakeTemplateParser(sections=("Первый",), tone=None)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)
        assert "1. Первый" in stand.last_text

        await stand.click(ACTION_FIX)
        assert stand.last_text == MESSAGE_ASK_DESCRIPTION
        assert stand.saves == []

        parser.sections = ("Второй", "Третий")
        parser.tone = ""
        await stand.send("Разделы: Второй, Третий")
        assert "1. Второй" in stand.last_text
        assert "2. Третий" in stand.last_text
        assert "1. Первый" not in stand.last_text
        assert stand.saves == []

        await stand.click(ACTION_SAVE)

    stand = run_template_scenario(scenario, parser=parser)

    assert len(stand.parser.calls) == 2
    assert stand.saves == [(42, (("Второй", "Третий"), ""))]


def test_new_description_while_candidate_shown_replaces_it() -> None:
    """Новое текстовое сообщение при показанном кандидате заменяет его."""
    parser = FakeTemplateParser(sections=("Первый",), tone=None)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)
        assert "1. Первый" in stand.last_text

        parser.sections = ("Второй",)
        await stand.send("Разделы: Второй")
        assert "1. Второй" in stand.last_text
        assert "1. Первый" not in stand.last_text
        assert stand.saves == []

        await stand.click(ACTION_SAVE)

    stand = run_template_scenario(scenario, parser=parser)

    assert len(stand.parser.calls) == 2
    assert stand.saves == [(42, (("Второй",), ""))]


def test_link_while_candidate_shown_does_not_start_video() -> None:
    """Ссылка при показанном кандидате тоже не запускает обработку видео."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)
        await stand.send(VIDEO_URL)

    stand = run_template_scenario(scenario)

    assert stand.last_text == MESSAGE_LINK_WHILE_CANDIDATE
    assert MESSAGE_ACCEPTED not in stand.texts
    assert stand.source.calls == [] and stand.summarizer.calls == []
    assert stand.saves == []


def test_inaccessible_callback_message_gets_a_new_message() -> None:
    """Если сообщение с кнопкой недоступно, ответ уходит новым сообщением."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)
        await stand.harness.send_callback(template_action(ACTION_SAVE), inaccessible=True)

    stand = run_template_scenario(scenario)

    assert stand.saves == [(42, (("Коротко", "Примеры кода"), "короче"))]
    assert MESSAGE_SAVED in stand.last_text
    assert isinstance(stand.requests[-1], SendMessage)


def test_failed_message_edit_falls_back_to_a_new_message() -> None:
    """Сбой редактирования сообщения не теряет шаг сценария."""
    session = FailingEditSession()
    parser = FakeTemplateParser(sections=("Коротко",), tone=None)
    handler = TemplateHandler(TemplateService(FakeTemplateRepository()), parser)
    harness = make_harness(
        SummaryService(FakeVideoSource(), FakeSummarizer()),
        template_handler=handler,
        session=session,
    )

    async def scenario() -> None:
        try:
            await harness.send("/template")
            await harness.send_callback(template_action(ACTION_EDIT))
        finally:
            await harness.close()

    asyncio.run(scenario())

    assert session.edits_attempted == 1
    assert texts(session.requests)[-1] == MESSAGE_ASK_DESCRIPTION


def test_cancel_closes_scenario_without_changes() -> None:
    """«Отмена» закрывает сценарий, активный шаблон остаётся прежним."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)
        await stand.click(ACTION_CANCEL)

    stand = run_template_scenario(scenario, user_template=SAVED_TEMPLATE)

    assert stand.last_text == MESSAGE_CLOSED
    assert stand.saves == []
    assert stand.deletes == []
    assert stand.templates.get_active(42).template == SAVED_TEMPLATE


def test_cancel_closes_and_scenario_state_is_released() -> None:
    """После «Отмены» тексты снова уходят обработчику ссылок."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.click(ACTION_CANCEL)
        await stand.send("просто текст без ссылки")

    stand = run_template_scenario(scenario)

    assert stand.texts[-2] == MESSAGE_CLOSED
    assert stand.last_text == MESSAGE_NEED_LINK
    assert stand.source.calls == []


def test_close_closes_view_without_changes() -> None:
    """«Закрыть» закрывает просмотр и ничего не меняет."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_CLOSE)

    stand = run_template_scenario(scenario, user_template=SAVED_TEMPLATE)

    assert stand.last_text == MESSAGE_CLOSED
    assert stand.saves == []
    assert stand.deletes == []
    assert stand.templates.get_active(42).template == SAVED_TEMPLATE


@pytest.mark.parametrize("action", [ACTION_FIX, ACTION_CANCEL, "unknown"])
def test_stale_callbacks_get_a_clear_message(action: str) -> None:
    """Устаревшая кнопка без активного шага отвечает понятным сообщением."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.click(action)

    stand = run_template_scenario(scenario)

    assert stand.last_text == MESSAGE_STALE_CALLBACK
    assert stand.saves == [] and stand.deletes == []


def test_description_state_is_isolated_per_user() -> None:
    """Ожидание описания у одного пользователя не блокирует ссылку другого."""
    source = FakeVideoSource(video=make_video())
    summarizer = FakeSummarizer(summary=make_summary())

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template", user_id=42)
        await stand.click(ACTION_EDIT, user_id=42)
        await stand.send(VIDEO_URL, user_id=77)
        await stand.harness.handler.wait_idle()

    stand = run_template_scenario(scenario, source=source, summarizer=summarizer)

    assert MESSAGE_ACCEPTED in stand.texts
    assert MESSAGE_LINK_WHILE_DESCRIPTION not in stand.texts
    assert source.calls == [VIDEO_URL]
    assert stand.parser.calls == []


# --- Сброс ------------------------------------------------------------------


def test_reset_requires_explicit_confirmation_and_calls_no_llm() -> None:
    """«Сбросить» сначала спрашивает подтверждение, текст не запрашивает."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_RESET)
        assert stand.last_text == MESSAGE_RESET_ASK
        confirmation = [
            request
            for request in stand.requests
            if isinstance(request, EditMessageText)
        ][-1]
        assert inline_buttons(confirmation.reply_markup) == [
            BUTTON_RESET_CONFIRM,
            BUTTON_CANCEL,
        ]
        assert stand.deletes == [], "до подтверждения шаблон не удаляется"

        await stand.click(ACTION_RESET_CONFIRM)

    stand = run_template_scenario(scenario, user_template=SAVED_TEMPLATE)

    assert stand.deletes == [42]
    assert stand.last_text.startswith(MESSAGE_RESET_DONE)
    assert MESSAGE_ORIGIN_BUILTIN in stand.last_text
    assert expected_numbering(BUILTIN_SECTIONS) in stand.last_text
    assert MESSAGE_TONE_MISSING in stand.last_text
    assert stand.parser.calls == [], "сброс не должен вызывать LLM"
    assert MESSAGE_ASK_DESCRIPTION not in stand.texts, "сброс не просит текст"
    assert stand.templates.get_active(42).is_builtin


def test_reset_is_idempotent_for_user_without_template() -> None:
    """Сброс без пользовательского шаблона проходит и не срабатывает дважды."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_RESET)
        await stand.click(ACTION_RESET_CONFIRM)
        assert stand.last_text.startswith(MESSAGE_RESET_DONE)

        await stand.click(ACTION_RESET_CONFIRM)

    stand = run_template_scenario(scenario)

    assert stand.deletes == [42]
    assert stand.last_text == MESSAGE_STALE_CALLBACK
    assert stand.saves == []


def test_reset_cancel_keeps_template() -> None:
    """«Отмена» в подтверждении сброса оставляет пользовательский шаблон."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_RESET)
        await stand.click(ACTION_CANCEL)

    stand = run_template_scenario(scenario, user_template=SAVED_TEMPLATE)

    assert stand.deletes == []
    assert stand.last_text == MESSAGE_CLOSED
    assert stand.templates.get_active(42).template == SAVED_TEMPLATE


# --- Доступ по allowlist ----------------------------------------------------


def test_stranger_cannot_open_template() -> None:
    """Посторонний не видит шаблон: отказ до показа."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template", user_id=42)

    stand = run_template_scenario(scenario, allowed_user_ids=(1001,))

    assert stand.texts == [MESSAGE_ACCESS_DENIED]
    assert stand.parser.calls == []
    assert sent_messages(stand.requests)[0].reply_markup is None


@pytest.mark.parametrize("action", [ACTION_EDIT, ACTION_SAVE, ACTION_RESET_CONFIRM])
def test_stranger_callback_is_denied_without_side_effects(action: str) -> None:
    """Чужой callback получает отказ и не читает/не меняет шаблон."""
    repository = FakeTemplateRepository()
    repository.records[42] = (("Чужой раздел",), "чужой тон")

    async def scenario(stand: TemplateStand) -> None:
        await stand.click(action, user_id=77)

    stand = run_template_scenario(scenario, repository=repository, allowed_user_ids=(1001,))

    answers = [
        request
        for request in stand.requests
        if isinstance(request, AnswerCallbackQuery)
    ]
    assert [answer.text for answer in answers] == [MESSAGE_ACCESS_DENIED]
    assert answers[0].show_alert is True
    assert not sent_messages(stand.requests)
    assert not [r for r in stand.requests if isinstance(r, EditMessageText)]
    assert stand.saves == [] and stand.deletes == []
    assert repository.records[42] == (("Чужой раздел",), "чужой тон")
    assert stand.parser.calls == []


def test_allowed_user_id_is_not_denied() -> None:
    """Разрешённый ID пользуется сценарием; allowlist не мешает."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template", user_id=42)

    stand = run_template_scenario(scenario, allowed_user_ids=(42, 1001))

    assert MESSAGE_ORIGIN_BUILTIN in stand.texts[0]
    assert MESSAGE_ACCESS_DENIED not in stand.texts


# --- Ввод описания: лимиты, ссылка и ошибки ---------------------------------


def test_link_while_waiting_for_description_does_not_start_video() -> None:
    """Ссылка во время ввода описания не запускает обработку видео."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(VIDEO_URL)

        assert stand.last_text == MESSAGE_LINK_WHILE_DESCRIPTION
        assert MESSAGE_ACCEPTED not in stand.texts
        assert stand.source.calls == []
        assert stand.summarizer.calls == []
        assert stand.parser.calls == []

        # Состояние не потеряно: описание всё ещё можно прислать.
        await stand.send(DESCRIPTION)
        assert "1. Коротко" in stand.last_text

    stand = run_template_scenario(scenario)

    assert len(stand.parser.calls) == 1


@pytest.mark.parametrize("text", ["   ", "\n\t  \n"], ids=["spaces", "whitespace"])
def test_whitespace_description_is_rejected(text: str) -> None:
    """Пустое описание просит прислать текст и не вызывает LLM."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(text)

    stand = run_template_scenario(scenario)

    assert stand.last_text == MESSAGE_DESCRIPTION_EMPTY
    assert stand.parser.calls == []
    assert stand.saves == []


def test_too_long_description_is_rejected_and_input_continues() -> None:
    """Описание длиннее 4000 символов отвергается без вызова LLM."""
    too_long = "я" * (MAX_DESCRIPTION_LENGTH + 1)
    parser = FakeTemplateParser(sections=("Коротко",), tone=None)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(too_long)
        assert str(MAX_DESCRIPTION_LENGTH) in stand.last_text
        assert stand.parser.calls == []

        await stand.send("Разделы: Коротко")
        assert "1. Коротко" in stand.last_text

    run_template_scenario(scenario, parser=parser)

    assert len(parser.calls) == 1
    assert parser.calls[0][0] == "Разделы: Коротко"


def test_parse_error_keeps_active_template_and_allows_retry() -> None:
    """Ошибка разбора не меняет шаблон и возвращает к вводу."""
    error = TemplateParseError(
        "В описании не нашлось изменений разделов или тона. Опишите, что изменить."
    )
    parser = FakeTemplateParser(error=error)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)

        answer = stand.last_text
        assert str(error) in answer
        assert MESSAGE_DESCRIPTION_RETRY in answer
        assert "Traceback" not in answer
        assert stand.templates.get_active(42).template == SAVED_TEMPLATE
        assert stand.saves == []

        parser.error = None
        parser.sections = ("Новый",)
        await stand.send("Разделы: Новый")
        assert "1. Новый" in stand.last_text
        await stand.click(ACTION_SAVE)

    stand = run_template_scenario(scenario, parser=parser, user_template=SAVED_TEMPLATE)

    assert len(parser.calls) == 2
    assert stand.saves == [(42, (("Новый",), SAVED_TEMPLATE.tone))]


def test_unknown_parse_error_hides_details_and_keeps_template(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Неизвестный сбой: общее сообщение, очищенный лог, шаблон прежний."""
    leaked = (
        f"boom {FAKE_BOT_TOKEN} Authorization: Bearer {FAKE_BEARER_TOKEN} "
        f"LLM_API_KEY={FAKE_ENV_KEY} {FAKE_API_KEY}"
    )
    parser = FakeTemplateParser(error=RuntimeError(leaked))

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        with caplog.at_level(logging.ERROR, logger="tldw.handlers.template"):
            await stand.send(DESCRIPTION)

    stand = run_template_scenario(scenario, parser=parser, user_template=SAVED_TEMPLATE)

    assert stand.texts[-1] == (
        f"{MESSAGE_UNEXPECTED_TEMPLATE}\n\n{MESSAGE_DESCRIPTION_RETRY}"
    )
    assert MESSAGE_UNEXPECTED not in stand.texts[-1], "неизвестный сбой — про шаблон"
    assert "Traceback" not in stand.texts[-1]
    for secret in (FAKE_BOT_TOKEN, FAKE_BEARER_TOKEN, FAKE_ENV_KEY, FAKE_API_KEY):
        assert secret not in caplog.text, "секрет не должен попадать в лог"
        assert secret not in stand.texts[-1]
    assert REDACTED in caplog.text
    errors = [
        record
        for record in caplog.records
        if record.name == "tldw.handlers.template" and record.levelno == logging.ERROR
    ]
    assert errors, "неизвестный сбой должен логироваться на уровне ERROR"
    assert DESCRIPTION not in caplog.text, "описание шаблона не пишется в лог"
    assert stand.saves == []
    assert stand.templates.get_active(42).template == SAVED_TEMPLATE


def test_answers_and_logs_contain_no_description_or_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Успешный сценарий не пишет в лог и ответы описание и секреты."""
    description = f"Разделы: Коротко. Тон: повторяй {FAKE_BOT_TOKEN} и {FAKE_API_KEY}"

    async def scenario(stand: TemplateStand) -> None:
        with caplog.at_level(logging.DEBUG, logger="tldw.handlers.template"):
            await stand.send("/template")
            await stand.click(ACTION_EDIT)
            await stand.send(description)
            await stand.click(ACTION_SAVE)

    stand = run_template_scenario(scenario)

    for answer in stand.texts:
        assert description not in answer
        assert FAKE_BOT_TOKEN not in answer
        assert FAKE_API_KEY not in answer
    assert description not in caplog.text
    assert FAKE_BOT_TOKEN not in caplog.text
    assert FAKE_API_KEY not in caplog.text


# --- Устаревшие кнопки и перезапуск -----------------------------------------


def test_repeated_save_does_not_save_twice() -> None:
    """Повторная кнопка «Сохранить» не сохраняет шаблон дважды."""

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)
        await stand.click(ACTION_SAVE)
        assert MESSAGE_SAVED in stand.last_text

        await stand.click(ACTION_SAVE)

    stand = run_template_scenario(scenario)

    assert len(stand.saves) == 1
    assert stand.last_text == MESSAGE_STALE_CANDIDATE
    assert stand.templates.get_active(42).sections == ("Коротко", "Примеры кода")


def test_candidate_does_not_survive_restart_and_active_template_remains() -> None:
    """Кандидат живёт только в FSM: после перезапуска активен прежний шаблон."""
    repository = FakeTemplateRepository()
    parser = FakeTemplateParser(sections=("Новый раздел",), tone="новый тон")

    async def make_candidate(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send(DESCRIPTION)
        assert "1. Новый раздел" in stand.last_text

    first = run_template_scenario(
        make_candidate,
        parser=parser,
        repository=repository,
        user_template=SAVED_TEMPLATE,
    )
    assert first.saves == []
    assert repository.records[42] == (SAVED_TEMPLATE.sections, SAVED_TEMPLATE.tone)

    async def after_restart(stand: TemplateStand) -> None:
        # Новая сессия не помнит кандидата: сохранение просит открыть сценарий.
        await stand.click(ACTION_SAVE)
        assert stand.last_text == MESSAGE_STALE_CANDIDATE

        # Активный шаблон остался прежним, кандидат в него не попал.
        await stand.send("/template")
        assert expected_numbering(SAVED_TEMPLATE.sections) in stand.texts[-2]
        assert "Новый раздел" not in stand.texts[-2]

    second = run_template_scenario(
        after_restart,
        parser=FakeTemplateParser(sections=("Новый раздел",), tone="новый тон"),
        repository=repository,
    )

    assert second.saves == []
    active = second.templates.get_active(42)
    assert active.sections == SAVED_TEMPLATE.sections
    assert active.tone == SAVED_TEMPLATE.tone


def test_saved_template_persists_in_store_but_candidate_does_not(tmp_path: Path) -> None:
    """Настоящее хранилище: подтверждённый шаблон переживает перезапуск."""
    data_dir = tmp_path / "data"
    templates = TemplateService(TemplateStore(data_dir))
    parser = FakeTemplateParser(sections=("Коротко", "Примеры кода"), tone="короче")
    source = FakeVideoSource()
    summarizer = FakeSummarizer()
    handler = TemplateHandler(templates, parser)
    harness = make_harness(
        SummaryService(source, summarizer),
        template_handler=handler,
    )

    async def scenario() -> None:
        try:
            await harness.send("/template")
            await harness.send_callback(template_action(ACTION_EDIT))
            await harness.send(DESCRIPTION)

            # Кандидат не сохранён: файла шаблона ещё нет.
            assert TemplateStore(data_dir).load(42) is None

            await harness.send_callback(template_action(ACTION_SAVE))
        finally:
            await harness.close()

    asyncio.run(scenario())

    restarted = TemplateService(TemplateStore(data_dir))
    active = restarted.get_active(42)
    assert not active.is_builtin
    assert active.sections == ("Коротко", "Примеры кода")
    assert active.tone == "короче"
    assert not list((data_dir / "templates").glob("*.tmp")), "временных файлов не остаётся"


# --- Проводка роутера -------------------------------------------------------


def test_router_registers_template_handler_before_link_handler() -> None:
    """``create_router`` подключает шаблон раньше ссылок: видео не запускается."""
    repository = FakeTemplateRepository()
    templates = TemplateService(repository)
    parser = FakeTemplateParser(sections=("Коротко",), tone=None)
    source = FakeVideoSource()
    summarizer = FakeSummarizer()
    session = FakeSession()
    bot = Bot(token=FAKE_BOT_TOKEN, session=session)
    router = create_router(
        SummaryService(source, summarizer),
        template_service=templates,
        template_parser=parser,
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    async def scenario() -> None:
        try:
            await dispatcher.feed_update(bot, make_update("/template"))
            await dispatcher.feed_update(bot, make_callback_update(template_action(ACTION_EDIT)))
            await dispatcher.feed_update(bot, make_update(VIDEO_URL))
        finally:
            await bot.session.close()

    asyncio.run(scenario())

    answers = texts(session.requests)
    assert MESSAGE_LINK_WHILE_DESCRIPTION in answers
    assert MESSAGE_ACCEPTED not in answers
    assert source.calls == [] and summarizer.calls == []
    assert parser.calls == []


def test_router_without_template_dependencies_keeps_only_link_handler() -> None:
    """Старый вызов ``create_router(service)`` по-прежнему валиден."""
    router = create_router(SummaryService(FakeVideoSource(), FakeSummarizer()))

    assert len(router.message.handlers) == 1
    assert len(router.callback_query.handlers) == 0


def test_router_requires_both_template_dependencies() -> None:
    """Одна зависимость без второй — ошибка сборки, а не молчаливый пропуск."""
    service = SummaryService(FakeVideoSource(), FakeSummarizer())

    with pytest.raises(ValueError, match="template_service"):
        create_router(service, template_service=TemplateService(FakeTemplateRepository()))

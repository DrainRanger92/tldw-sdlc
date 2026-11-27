"""Тесты голосового ввода в Telegram-сценарии шаблона (T-012).

Сеть и токены не нужны: апдейт с голосовым проходит через настоящие
``Dispatcher``, ``TemplateHandler`` и ``TemplateService``; подменены только
внешние границы — сессия Telegram (``FakeSession`` с содержимым «скачанного»
аудио), распознавание речи (``FakeSpeechTranscriber``), разбор описания
(``FakeTemplateParser``), YouTube и LLM-конспект.

Проверяется контракт T-012 и ADR-015:

- allowlist проверяется до скачивания и распознавания, длительность — до
  скачивания, распознавание — одна попытка на сообщение;
- распознанный текст идёт тем же путём «разбор → показ кандидата →
  Сохранить/Исправить/Отмена», автоматического сохранения нет;
- без настроенного адаптера бот предлагает текстовый ввод, а голос вне шага
  изменения получает подсказку;
- ошибка распознавания или скачивания оставляет прежний активный шаблон;
- аудио и полный текст распознавания не попадают в лог, аудио не пишется на
  диск (стенд без ``DATA_DIR`` — проверка на уровне логов и вызовов).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import GetFile
from support import (
    FakeSession,
    FakeSpeechTranscriber,
    FakeSummarizer,
    FakeTemplateParser,
    FakeTemplateRepository,
    FakeVideoSource,
    TemplateStand,
    expected_numbering,
    inline_buttons,
    make_harness,
    run_template_scenario,
    sent_messages,
    template_action,
    texts,
)

from tldw.adapters.speech import SpeechProviderError
from tldw.config import MAX_AUDIO_DURATION_SECONDS
from tldw.handlers import MESSAGE_ACCESS_DENIED
from tldw.handlers.template import (
    ACTION_CANCEL,
    ACTION_EDIT,
    ACTION_FIX,
    ACTION_SAVE,
    BUTTON_CANCEL,
    BUTTON_FIX,
    BUTTON_SAVE,
    MESSAGE_ASK_DESCRIPTION,
    MESSAGE_CANDIDATE_TITLE,
    MESSAGE_CLOSED,
    MESSAGE_DESCRIPTION_TOO_LONG,
    MESSAGE_PARSING,
    MESSAGE_SAVED,
    MESSAGE_STALE_CANDIDATE,
    MESSAGE_VOICE_DOWNLOAD_FAILED,
    MESSAGE_VOICE_EMPTY,
    MESSAGE_VOICE_NOT_EXPECTED,
    MESSAGE_VOICE_RETRY,
    MESSAGE_VOICE_TOO_LONG,
    MESSAGE_VOICE_UNAVAILABLE,
    TemplateHandler,
    TemplateStates,
)
from tldw.services.summary import SummaryService
from tldw.services.template import (
    BUILTIN_SECTIONS,
    MAX_DESCRIPTION_LENGTH,
    NoteTemplate,
    TemplateService,
)

#: Байты «скачанного» голосового: маркер позволяет искать утечку в логе.
AUDIO = b"FAKE-VOICE-AUDIO-BYTES-NOT-A-SECRET"
#: Текст, который «распознаёт» фейковая граница.
VOICE_TEXT = "Разделы: Коротко, Примеры кода. Тон: короче, с акцентом на код"
#: Пользовательский шаблон, заранее сохранённый в фейковом хранилище.
SAVED_TEMPLATE = NoteTemplate(sections=("Коротко", "Что попробовать"), tone="строго по делу")


def get_file_requests(stand: TemplateStand) -> list[Any]:
    """Запросы ``getFile`` — то есть попытки скачать аудио."""
    return [request for request in stand.requests if isinstance(request, GetFile)]


async def prime_state(stand: TemplateStand, state: str, *, user_id: int = 42) -> None:
    """Поставить состояние FSM пользователю до апдейта (тест порядка проверок)."""
    key = StorageKey(bot_id=stand.harness.bot.id, chat_id=user_id, user_id=user_id)
    await stand.harness.dispatcher.storage.set_state(key=key, state=state)


class BrokenAudioSession(FakeSession):
    """Сессия, у которой скачивание аудио падает как при сетевом сбое."""

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        if False:  # pragma: no cover - нужно, чтобы метод остался асинхронным
            yield b""
        raise OSError("не удалось скачать голосовое")


# --- Полный путь: голос → кандидат → сохранение ------------------------------


def test_voice_shows_candidate_and_saves_only_on_confirm() -> None:
    """Голос проходит путь «разбор → кандидат»; сохраняет только «Сохранить»."""
    speech = FakeSpeechTranscriber(text=VOICE_TEXT)
    parser = FakeTemplateParser(sections=("Коротко", "Примеры кода"), tone="короче")

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        assert stand.last_text == MESSAGE_ASK_DESCRIPTION

        await stand.send_voice(duration=15)

        assert MESSAGE_PARSING in stand.texts
        candidate = stand.last_text
        assert MESSAGE_CANDIDATE_TITLE in candidate
        assert "1. Коротко" in candidate
        assert "2. Примеры кода" in candidate
        assert "короче" in candidate
        assert inline_buttons(sent_messages(stand.requests)[-1].reply_markup) == [
            BUTTON_SAVE,
            BUTTON_FIX,
            BUTTON_CANCEL,
        ]
        assert stand.saves == [], "голос не должен сохранять шаблон автоматически"
        assert stand.templates.get_active(42).is_builtin

        await stand.click(ACTION_SAVE)

    stand = run_template_scenario(scenario, speech=speech, parser=parser, audio=AUDIO)

    assert speech.calls == [(AUDIO, 15.0)], "ровно одна попытка распознавания"
    assert parser.calls == [(VOICE_TEXT, BUILTIN_SECTIONS, "")]
    assert stand.saves == [(42, (("Коротко", "Примеры кода"), "короче"))]
    assert MESSAGE_SAVED in stand.last_text
    assert expected_numbering(("Коротко", "Примеры кода")) in stand.last_text


def test_voice_downloads_audio_once_and_transcribes_in_a_worker_thread() -> None:
    """Аудио скачивается один раз, распознавание идёт вне потока event loop."""
    speech = FakeSpeechTranscriber(text=VOICE_TEXT)
    loop_threads: list[int] = []

    async def scenario(stand: TemplateStand) -> None:
        loop_threads.append(threading.get_ident())
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send_voice(duration=7)

    stand = run_template_scenario(scenario, speech=speech, audio=AUDIO)

    assert len(get_file_requests(stand)) == 1, "аудио должно скачиваться один раз"
    assert len(speech.calls) == 1
    assert speech.thread_ids and speech.thread_ids[0] != loop_threads[0], (
        "распознавание должно выполняться вне потока event loop (asyncio.to_thread)"
    )


def test_voice_while_candidate_shown_replaces_candidate() -> None:
    """Голос при показанном кандидате заменяет его, как и новое описание."""
    speech = FakeSpeechTranscriber(text=VOICE_TEXT)
    parser = FakeTemplateParser(sections=("Первый",), tone=None)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send("Разделы: Первый")
        assert "1. Первый" in stand.last_text

        speech.text = "Разделы: Второй, Третий"
        parser.sections = ("Второй", "Третий")
        parser.tone = ""
        await stand.send_voice(duration=9)
        assert "1. Второй" in stand.last_text
        assert "1. Первый" not in stand.last_text
        assert stand.saves == []

        await stand.click(ACTION_SAVE)

    stand = run_template_scenario(scenario, speech=speech, parser=parser, audio=AUDIO)

    assert len(speech.calls) == 1
    assert len(parser.calls) == 2
    assert stand.saves == [(42, (("Второй", "Третий"), ""))]


def test_voice_candidate_can_be_fixed_and_cancelled_without_saving() -> None:
    """«Исправить» и «Отмена» работают для голосового кандидата как для текста."""
    speech = FakeSpeechTranscriber(text=VOICE_TEXT)
    parser = FakeTemplateParser(sections=("Первый",), tone=None)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send_voice(duration=8)
        assert "1. Первый" in stand.last_text

        await stand.click(ACTION_FIX)
        assert stand.last_text == MESSAGE_ASK_DESCRIPTION
        assert stand.saves == []

        parser.sections = ("Второй",)
        await stand.send_voice(duration=8)
        assert "1. Второй" in stand.last_text
        assert "1. Первый" not in stand.last_text
        assert stand.saves == []

        await stand.click(ACTION_CANCEL)

    stand = run_template_scenario(scenario, speech=speech, parser=parser, audio=AUDIO)

    assert len(speech.calls) == 2, "по одной попытке на каждое сообщение"
    assert stand.last_text == MESSAGE_CLOSED
    assert stand.saves == []
    assert stand.templates.get_active(42).is_builtin


# --- Лимит длительности ------------------------------------------------------


def test_voice_at_the_limit_is_accepted() -> None:
    """Ровно 60 секунд — допустимая длительность: распознавание выполняется."""
    speech = FakeSpeechTranscriber(text=VOICE_TEXT)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send_voice(duration=60)

    stand = run_template_scenario(scenario, speech=speech, audio=AUDIO)

    assert speech.calls == [(AUDIO, 60.0)]
    assert MESSAGE_CANDIDATE_TITLE in stand.last_text


def test_voice_longer_than_limit_is_rejected_before_download() -> None:
    """61 секунда: отказ до скачивания и распознавания, сценарий не сломан."""
    speech = FakeSpeechTranscriber(text=VOICE_TEXT)
    parser = FakeTemplateParser(sections=("Коротко",), tone=None)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send_voice(duration=61)

        assert stand.last_text == MESSAGE_VOICE_TOO_LONG.format(limit=MAX_AUDIO_DURATION_SECONDS)
        assert get_file_requests(stand) == [], "длинное аудио нельзя скачивать"
        assert speech.calls == []
        assert parser.calls == []
        assert stand.saves == []

        # Состояние ожидания сохранено: текстовое описание всё ещё принимается.
        await stand.send("Разделы: Коротко")
        assert "1. Коротко" in stand.last_text

    stand = run_template_scenario(scenario, speech=speech, parser=parser, audio=AUDIO)

    assert len(parser.calls) == 1
    assert stand.saves == []


# --- Ненастроенный адаптер ---------------------------------------------------


def test_unconfigured_adapter_offers_text_input() -> None:
    """Не настроенный адаптер: бот предлагает текст, аудио не тратится."""
    speech = FakeSpeechTranscriber(text=VOICE_TEXT, configured=False)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send_voice(duration=10)

        assert stand.last_text == MESSAGE_VOICE_UNAVAILABLE
        assert inline_buttons(sent_messages(stand.requests)[-1].reply_markup) == [BUTTON_CANCEL]
        assert get_file_requests(stand) == []
        assert speech.calls == []

        # Текстовый сценарий работает как обычно.
        await stand.send("Разделы: Коротко")
        assert "1. Коротко" in stand.last_text
        await stand.click(ACTION_SAVE)

    stand = run_template_scenario(scenario, speech=speech, audio=AUDIO)

    assert stand.saves == [(42, (("Коротко", "Примеры кода"), "короче"))]


def test_absent_adapter_offers_text_input_without_network() -> None:
    """Без подключённого адаптера голос не скачивается и не распознаётся."""
    parser = FakeTemplateParser(sections=("Коротко",), tone=None)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send_voice(duration=10)

        assert stand.last_text == MESSAGE_VOICE_UNAVAILABLE
        assert get_file_requests(stand) == []

        await stand.send("Разделы: Коротко")
        assert "1. Коротко" in stand.last_text

    stand = run_template_scenario(scenario, speech=None, parser=parser, audio=AUDIO)

    assert stand.parser.calls == [("Разделы: Коротко", BUILTIN_SECTIONS, "")]


# --- Доступ и состояние ------------------------------------------------------


def test_stranger_voice_is_denied_before_download_and_recognition() -> None:
    """Посторонний получает отказ: ни скачивания, ни распознавания, ни разбора.

    Состояние ожидания описания выставлено заранее: allowlist всё равно
    проверяется первой строкой, до состояния и скачивания.
    """
    speech = FakeSpeechTranscriber(text=VOICE_TEXT)

    async def scenario(stand: TemplateStand) -> None:
        await prime_state(stand, TemplateStates.waiting_description.state)
        await stand.send_voice(duration=10)

    stand = run_template_scenario(
        scenario,
        speech=speech,
        audio=AUDIO,
        allowed_user_ids=(1001,),
    )

    assert stand.texts == [MESSAGE_ACCESS_DENIED]
    assert get_file_requests(stand) == []
    assert speech.calls == []
    assert stand.parser.calls == []
    assert stand.saves == []


def test_voice_outside_description_state_gets_a_hint() -> None:
    """Голос вне шага изменения не скачивается, а подсказывает, куда идти."""
    speech = FakeSpeechTranscriber(text=VOICE_TEXT)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send_voice(duration=10)

    stand = run_template_scenario(scenario, speech=speech, audio=AUDIO)

    assert stand.last_text == MESSAGE_VOICE_NOT_EXPECTED
    assert get_file_requests(stand) == []
    assert speech.calls == []
    assert stand.saves == []


# --- Ошибки распознавания и скачивания ---------------------------------------


def test_recognition_error_keeps_previous_template_and_returns_to_input() -> None:
    """Сбой распознавания: прежний шаблон активен, кандидата нет, одна попытка."""
    error = SpeechProviderError()
    speech = FakeSpeechTranscriber(error=error)
    parser = FakeTemplateParser(sections=("Новый",), tone=None)

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send_voice(duration=12)

        answer = stand.last_text
        assert str(error) in answer
        assert MESSAGE_VOICE_RETRY in answer
        assert "Traceback" not in answer
        assert stand.templates.get_active(42).template == SAVED_TEMPLATE
        assert stand.saves == []

        # Кандидата нет: «Сохранить» после сбоя ничего не пишет.
        await stand.click(ACTION_SAVE)
        assert stand.last_text == MESSAGE_STALE_CANDIDATE
        assert stand.saves == []

        # Сценарий не сломан: его можно открыть заново и прислать текст.
        speech.error = None
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send("Разделы: Новый")
        assert "1. Новый" in stand.last_text
        await stand.click(ACTION_SAVE)

    stand = run_template_scenario(
        scenario,
        speech=speech,
        parser=parser,
        audio=AUDIO,
        user_template=SAVED_TEMPLATE,
    )

    assert len(speech.calls) == 1, "распознавание — одна попытка на сообщение"
    assert stand.saves == [(42, (("Новый",), SAVED_TEMPLATE.tone))]
    assert stand.templates.get_active(42).sections == ("Новый",)


def test_download_failure_keeps_state_and_offers_retry() -> None:
    """Сбой скачивания: понятное сообщение, шаблон прежний, текст работает."""
    speech = FakeSpeechTranscriber(text=VOICE_TEXT)
    parser = FakeTemplateParser(sections=("Коротко",), tone=None)
    handler = TemplateHandler(
        TemplateService(FakeTemplateRepository()),
        parser,
        speech=speech,
    )
    session = BrokenAudioSession(audio=AUDIO)
    harness = make_harness(
        SummaryService(FakeVideoSource(), FakeSummarizer()),
        template_handler=handler,
        session=session,
    )

    async def scenario() -> None:
        try:
            await harness.send("/template")
            await harness.send_callback(template_action(ACTION_EDIT))
            await harness.send_voice(duration=10)
            assert texts(session.requests)[-1] == MESSAGE_VOICE_DOWNLOAD_FAILED

            await harness.send("Разделы: Коротко")
            assert "1. Коротко" in texts(session.requests)[-1]
        finally:
            await harness.close()

    asyncio.run(scenario())

    assert speech.calls == []
    assert parser.calls == [("Разделы: Коротко", BUILTIN_SECTIONS, "")]


# --- Распознанный текст: лимиты и пустой результат ---------------------------


def test_recognized_text_longer_than_description_limit_is_rejected() -> None:
    """Слишком длинный распознанный текст отвергается общим лимитом описания."""
    speech = FakeSpeechTranscriber(text="я" * (MAX_DESCRIPTION_LENGTH + 1))

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send_voice(duration=59)

    stand = run_template_scenario(scenario, speech=speech, audio=AUDIO)

    assert stand.last_text == MESSAGE_DESCRIPTION_TOO_LONG.format(
        length=MAX_DESCRIPTION_LENGTH + 1,
        limit=MAX_DESCRIPTION_LENGTH,
    )
    assert stand.parser.calls == []
    assert stand.saves == []


def test_empty_recognition_result_is_reported_without_parsing() -> None:
    """Пустой результат распознавания: просьба повторить, разбора нет."""
    speech = FakeSpeechTranscriber(text="   ")

    async def scenario(stand: TemplateStand) -> None:
        await stand.send("/template")
        await stand.click(ACTION_EDIT)
        await stand.send_voice(duration=5)

    stand = run_template_scenario(scenario, speech=speech, audio=AUDIO)

    assert stand.last_text == MESSAGE_VOICE_EMPTY
    assert stand.parser.calls == []
    assert stand.saves == []


# --- Приватность логов --------------------------------------------------------


def test_logs_contain_no_audio_and_no_recognized_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """В логе нет ни аудио, ни полного текста распознавания — только счётчики."""
    recognized = f"{VOICE_TEXT} {AUDIO.decode('ascii')}"
    speech = FakeSpeechTranscriber(text=recognized)

    async def scenario(stand: TemplateStand) -> None:
        with caplog.at_level(logging.DEBUG, logger="tldw.handlers.template"):
            await stand.send("/template")
            await stand.click(ACTION_EDIT)
            await stand.send_voice(duration=11)

    stand = run_template_scenario(scenario, speech=speech, audio=AUDIO)

    assert stand.parser.calls == [(recognized, BUILTIN_SECTIONS, "")]
    assert recognized not in caplog.text, "полный текст распознавания не пишется в лог"
    assert AUDIO.decode("ascii") not in caplog.text, "аудио не пишется в лог"
    assert "распознавание голосового описания" in caplog.text, (
        "в логе должна остаться безопасная пометка о распознавании"
    )

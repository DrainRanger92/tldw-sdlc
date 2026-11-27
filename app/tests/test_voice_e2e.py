"""Сквозной голосовой сценарий шаблона заметки (T-012).

Тест поднимает **настоящую** цепочку приложения без сети и токенов:

``Dispatcher`` → ``TemplateHandler`` / ``LinkHandler`` → ``TemplateService`` →
``TemplateStore`` (файлы в ``DATA_DIR``) → ``SummaryService`` → рендер ``.md``

Подменены только внешние границы: сессия Telegram (``FakeSession`` с байтами
«скачанного» аудио), распознавание речи (``FakeSpeechTranscriber``), источник
видео YouTube и LLM (разбор описания и конспект). Поэтому проверяется не только
ответ бота, но и то, что голосовое описание проходит путь «распознавание →
разбор → показ кандидата → Сохранить», сохранённый шаблон применяется к
следующей ссылке, а аудио не остаётся в ``DATA_DIR``.

Сценарии:

* полный путь ``/template`` → «Изменить» → голос → кандидат → «Сохранить» →
  ссылка → ``.md`` с пользовательскими разделами;
* негативные: чужой ID (отказ до скачивания), аудио длиннее 60 секунд (отказ до
  скачивания) и сбой распознавания не создают ни шаблон, ни заметку, а прежний
  шаблон остаётся активным;
* без ``SPEECH_*`` настоящий адаптер не настроен: бот предлагает текст, аудио не
  скачивается, а текстовый сценарий работает как обычно.

Хелперы заметки и фейкового конспекта переиспользуются из
``tests/test_template_e2e.py``: второго каркаса не заводим.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest
from aiogram.methods import GetFile
from support import (
    VIDEO_URL,
    AppHarness,
    FakeSpeechTranscriber,
    FakeTemplateParser,
    app_harness,
    expected_numbering,
    last_inline_buttons,
)
from test_template_e2e import (
    CUSTOM_SECTIONS,
    CUSTOM_TONE,
    NOTE_FILENAME,
    USER_A,
    USER_B,
    NoteCapture,
    assert_custom_sections,
    build_summary_fakes,
)

from tldw.adapters.speech import SpeechProviderError
from tldw.config import MAX_AUDIO_DURATION_SECONDS, Settings, load_settings
from tldw.errors import MESSAGE_AUDIO_TOO_LONG
from tldw.handlers import MESSAGE_ACCESS_DENIED
from tldw.handlers.template import (
    ACTION_EDIT,
    ACTION_SAVE,
    BUTTON_CANCEL,
    BUTTON_FIX,
    BUTTON_SAVE,
    MESSAGE_CANDIDATE_TITLE,
    MESSAGE_SAVED,
    MESSAGE_STALE_CANDIDATE,
    MESSAGE_VOICE_RETRY,
    MESSAGE_VOICE_UNAVAILABLE,
)
from tldw.services.template import BUILTIN_SECTIONS, NoteTemplate, TemplateService
from tldw.storage import TemplateStore
from tldw.storage.templates import TEMPLATES_DIR_NAME

#: Байты «скачанного» голосового: не должны оказаться ни в ``DATA_DIR``, ни в логе.
AUDIO = b"FAKE-VOICE-AUDIO-BYTES-NOT-A-SECRET"
#: Текст, который «распознаёт» фейковая граница распознавания.
VOICE_TEXT = "Разделы: Коротко, Примеры кода, Что попробовать. Тон: короче, с акцентом на код"
#: Совпадает с текстом, который пользователь мог бы прислать руками.
DESCRIPTION = VOICE_TEXT
#: Расширения аудиофайлов: после голосового сценария в ``DATA_DIR`` их быть не должно.
_AUDIO_SUFFIXES = frozenset({".oga", ".ogg", ".mp3", ".m4a", ".wav", ".opus"})


def get_file_requests(app: AppHarness) -> list[Any]:
    """Запросы ``getFile`` — попытки скачать аудио из Telegram."""
    return [request for request in app.requests if isinstance(request, GetFile)]


def audio_files(data_dir: Path) -> list[Path]:
    """Аудиофайлы в каталоге данных: их не должно появляться после голоса."""
    return [path for path in data_dir.rglob("*") if path.suffix.lower() in _AUDIO_SUFFIXES]


def data_files(data_dir: Path) -> list[str]:
    """Все файлы ``DATA_DIR``: аудио не должно оставить даже временных файлов."""
    return sorted(
        path.relative_to(data_dir).as_posix()
        for path in data_dir.rglob("*")
        if path.is_file()
    )


def template_files(settings: Settings, user_id: int = USER_A) -> list[Path]:
    """Файлы шаблонов пользователя в ``DATA_DIR`` (их не должно быть до сохранения)."""
    directory = settings.data_dir / TEMPLATES_DIR_NAME
    return sorted(directory.glob(f"{user_id}.json"))


# --- Полный сценарий: голос → кандидат → сохранение → ссылка ----------------


def test_voice_description_is_applied_to_the_next_link(
    fake_env: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Голос → кандидат → «Сохранить» → ссылка даёт ``.md`` по шаблону."""
    settings = load_settings(fake_env)
    speech = FakeSpeechTranscriber(text=VOICE_TEXT)
    parser = FakeTemplateParser(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE)
    source, summarizer, service = build_summary_fakes(settings)
    capture = NoteCapture()

    async def scenario() -> None:
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
            speech_transcriber=speech,
            audio=AUDIO,
            on_request=capture,
        ) as app:
            await app.send("/template")
            await app.click(ACTION_EDIT)
            with caplog.at_level(logging.DEBUG, logger="tldw.handlers.template"):
                await app.send_voice(duration=20)

            # Кандидат показан, но автоматически не сохранён.
            candidate = app.last_text
            assert MESSAGE_CANDIDATE_TITLE in candidate
            assert expected_numbering(CUSTOM_SECTIONS) in candidate
            assert CUSTOM_TONE in candidate
            assert last_inline_buttons(app.requests) == [
                BUTTON_SAVE,
                BUTTON_FIX,
                BUTTON_CANCEL,
            ]
            assert TemplateStore(settings.data_dir).load(USER_A) is None
            assert template_files(settings) == []
            assert speech.calls == [(AUDIO, 20.0)], "одна попытка распознавания"
            assert parser.calls == [(VOICE_TEXT, BUILTIN_SECTIONS, "")]
            assert source.calls == [] and summarizer.calls == [], (
                "голосовое описание не должно тратить YouTube/LLM-конспект"
            )

            # Аудио живёт только в памяти: в DATA_DIR нет ни файла, ни заметки.
            assert get_file_requests(app), "аудио должно скачиваться после проверок"
            assert data_files(settings.data_dir) == [], "аудио не должно попасть в DATA_DIR"
            assert audio_files(settings.data_dir) == []
            assert list(settings.data_dir.rglob("*.md")) == []

            # Подтверждение: только «Сохранить» пишет файл шаблона.
            await app.click(ACTION_SAVE)
            assert MESSAGE_SAVED in app.last_text
            assert TemplateStore(settings.data_dir).load(USER_A) == (
                CUSTOM_SECTIONS,
                CUSTOM_TONE,
            )
            assert audio_files(settings.data_dir) == [], "аудио не должно сохраняться"

            # Ссылка: настоящий обработчик, сервис, рендер и запись файла.
            await app.send_link(VIDEO_URL)

    asyncio.run(scenario())

    assert capture.filenames == [NOTE_FILENAME]
    assert capture.paths[0].parent == settings.data_dir
    assert source.calls == [VIDEO_URL]
    assert summarizer.templates == [(CUSTOM_SECTIONS, CUSTOM_TONE)]
    assert_custom_sections(capture.markdowns[0])
    assert audio_files(settings.data_dir) == []
    assert VOICE_TEXT not in caplog.text, "полный текст распознавания не пишется в лог"
    assert AUDIO.decode("ascii") not in caplog.text, "аудио не пишется в лог"


# --- Негативные сценарии ----------------------------------------------------


def test_recognition_failure_keeps_previous_template_and_creates_no_note(
    fake_env: dict[str, str],
) -> None:
    """Сбой распознавания: прежний шаблон остаётся, кандидата и заметки нет."""
    settings = load_settings(fake_env)
    store = TemplateStore(settings.data_dir)
    TemplateService(store).save(USER_A, NoteTemplate(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE))
    template_path = settings.data_dir / TEMPLATES_DIR_NAME / f"{USER_A}.json"
    before = template_path.read_text(encoding="utf-8")
    speech = FakeSpeechTranscriber(error=SpeechProviderError())
    parser = FakeTemplateParser(sections=("Не должно разбираться",), tone=None)
    source, summarizer, service = build_summary_fakes(settings)
    capture = NoteCapture()

    async def scenario() -> None:
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
            speech_transcriber=speech,
            audio=AUDIO,
            on_request=capture,
        ) as app:
            await app.send("/template")
            await app.click(ACTION_EDIT)
            await app.send_voice(duration=12)

            answer = app.last_text
            assert str(SpeechProviderError()) in answer
            assert MESSAGE_VOICE_RETRY in answer
            assert "Traceback" not in answer
            assert template_path.read_text(encoding="utf-8") == before
            assert store.load(USER_A) == (CUSTOM_SECTIONS, CUSTOM_TONE)
            assert speech.calls == [(AUDIO, 12.0)], "ровно одна попытка"
            assert parser.calls == [], "сбой распознавания не доходит до разбора"
            assert list(settings.data_dir.rglob("*.md")) == []
            assert audio_files(settings.data_dir) == []

            # Кандидата нет: устаревшая кнопка не сохраняет ничего.
            await app.click(ACTION_SAVE)
            assert MESSAGE_STALE_CANDIDATE in app.last_text
            assert template_path.read_text(encoding="utf-8") == before

            # Прежний шаблон остался активным и применяется к следующей ссылке.
            await app.send_link(VIDEO_URL)

    asyncio.run(scenario())

    assert summarizer.templates == [(CUSTOM_SECTIONS, CUSTOM_TONE)]
    assert source.calls == [VIDEO_URL]
    assert_custom_sections(capture.markdowns[0])


def test_stranger_voice_creates_no_template_and_no_note(fake_env: dict[str, str]) -> None:
    """Чужой ID получает отказ до скачивания: ни шаблона, ни заметки."""
    settings = load_settings({**fake_env, "TELEGRAM_ALLOWED_USER_IDS": str(USER_A)})
    speech = FakeSpeechTranscriber(text=VOICE_TEXT)
    parser = FakeTemplateParser(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE)
    source, summarizer, service = build_summary_fakes(settings)

    async def scenario() -> None:
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
            speech_transcriber=speech,
            audio=AUDIO,
        ) as app:
            await app.send_voice(duration=10, user_id=USER_B)

            assert app.texts == [MESSAGE_ACCESS_DENIED]
            assert get_file_requests(app) == [], "чужое аудио нельзя скачивать"
            assert speech.calls == [], "чужое аудио нельзя распознавать"
            assert parser.calls == []
            assert template_files(settings, USER_B) == []
            assert TemplateStore(settings.data_dir).load(USER_B) is None
            assert list(settings.data_dir.rglob("*.md")) == []
            assert source.calls == [] and summarizer.calls == []

    asyncio.run(scenario())


def test_voice_longer_than_limit_is_rejected_and_creates_no_note(
    fake_env: dict[str, str],
) -> None:
    """Аудио длиннее 60 секунд: отказ до скачивания, шаблон и заметка не создаются."""
    settings = load_settings(fake_env)
    speech = FakeSpeechTranscriber(text=VOICE_TEXT)
    parser = FakeTemplateParser(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE)
    source, summarizer, service = build_summary_fakes(settings)
    capture = NoteCapture()

    async def scenario() -> None:
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
            speech_transcriber=speech,
            audio=AUDIO,
            on_request=capture,
        ) as app:
            await app.send("/template")
            await app.click(ACTION_EDIT)
            await app.send_voice(duration=61)

            assert app.last_text == MESSAGE_AUDIO_TOO_LONG.format(
                limit=MAX_AUDIO_DURATION_SECONDS
            )
            assert get_file_requests(app) == [], "длинное аудио нельзя скачивать"
            assert speech.calls == []
            assert parser.calls == []
            assert TemplateStore(settings.data_dir).load(USER_A) is None
            assert template_files(settings) == []
            assert list(settings.data_dir.rglob("*.md")) == []
            assert audio_files(settings.data_dir) == []

            # Сценарий не сломан: текстовое описание сохраняется и применяется.
            await app.send(DESCRIPTION)
            assert MESSAGE_CANDIDATE_TITLE in app.last_text
            await app.click(ACTION_SAVE)
            await app.send_link(VIDEO_URL)

    asyncio.run(scenario())

    assert parser.calls == [(DESCRIPTION, BUILTIN_SECTIONS, "")]
    assert summarizer.templates == [(CUSTOM_SECTIONS, CUSTOM_TONE)]
    assert source.calls == [VIDEO_URL]
    assert_custom_sections(capture.markdowns[0])


def test_unconfigured_speech_adapter_offers_text_input(fake_env: dict[str, str]) -> None:
    """Без ``SPEECH_*``: бот предлагает текст, аудио не скачивается, заметка есть."""
    settings = load_settings(fake_env)
    assert settings.speech_api_key == "" and settings.speech_model == ""
    parser = FakeTemplateParser(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE)
    source, summarizer, service = build_summary_fakes(settings)
    capture = NoteCapture()

    async def scenario() -> None:
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
            audio=AUDIO,
            on_request=capture,
        ) as app:
            await app.send("/template")
            await app.click(ACTION_EDIT)
            await app.send_voice(duration=10)

            assert app.last_text == MESSAGE_VOICE_UNAVAILABLE
            assert get_file_requests(app) == [], "без адаптера аудио не скачивается"
            assert parser.calls == []
            assert data_files(settings.data_dir) == [], "аудио не должно попасть в DATA_DIR"

            # Текстовый сценарий работает как обычно: описание → кандидат → ссылка.
            await app.send(DESCRIPTION)
            assert MESSAGE_CANDIDATE_TITLE in app.last_text
            await app.click(ACTION_SAVE)
            await app.send_link(VIDEO_URL)

    asyncio.run(scenario())

    assert parser.calls == [(DESCRIPTION, BUILTIN_SECTIONS, "")]
    assert summarizer.templates == [(CUSTOM_SECTIONS, CUSTOM_TONE)]
    assert source.calls == [VIDEO_URL]
    assert_custom_sections(capture.markdowns[0])
    assert audio_files(settings.data_dir) == []

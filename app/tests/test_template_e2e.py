"""Сквозной текстовый сценарий шаблона заметки (T-010).

Тест поднимает **настоящую** цепочку приложения без сети и токенов:

``Dispatcher`` → ``TemplateHandler`` / ``LinkHandler`` → ``TemplateService`` →
``TemplateStore`` (файлы в ``DATA_DIR``) → ``SummaryService`` → рендер ``.md``

Подменены только внешние границы: сессия Telegram (``FakeSession``), источник
видео YouTube и LLM (разбор описания и конспект) — общие фейки из
``support.py``. Поэтому проверяется не только ответ бота, но и то, что
сохранённый шаблон действительно переживает перезапуск приложения, лежит в
``<DATA_DIR>/templates/<user_id>.json`` и применяется к следующей ссылке:
разделы идут в заданном порядке, frontmatter остаётся прежним (7 полей).

Сценарии:

* полный путь ``/template`` → «Изменить» → описание → кандидат → «Сохранить» →
  ссылка → ``.md`` с пользовательскими разделами;
* сброс: после «Сбросить» и подтверждения следующая ссылка даёт заметку по
  встроенным 5 секциям;
* изоляция: шаблон пользователя A не применяется к заметке B;
* перезапуск: сохранённый шаблон переживает новый экземпляр приложения, а
  неподтверждённый кандидат — нет;
* негативные: ошибка разбора, ссылка во время ввода описания, устаревшая
  кнопка и ошибка записи не меняют активный шаблон, не создают ``.md`` и не
  тратят YouTube/LLM там, где не должны.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiogram.methods import SendDocument, SendMessage
from support import (
    VIDEO_CHANNEL,
    VIDEO_DURATION_SECONDS,
    VIDEO_TITLE,
    VIDEO_URL,
    AppHarness,
    FakeSummarizer,
    FakeTemplateParser,
    FakeVideoSource,
    app_harness,
    expected_numbering,
    frontmatter_keys,
    inline_buttons,
    last_inline_buttons,
    make_summary,
    make_video,
    parse_frontmatter,
    request_types,
    sent_messages,
)

from tldw.config import Settings, load_settings
from tldw.errors import TemplateParseError
from tldw.handlers import MESSAGE_ACCEPTED, MESSAGE_DONE, MESSAGE_PROCESSING
from tldw.handlers.template import (
    ACTION_CANCEL,
    ACTION_EDIT,
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
    MESSAGE_CANDIDATE_TITLE,
    MESSAGE_CLOSED,
    MESSAGE_DESCRIPTION_RETRY,
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
)
from tldw.services.note import FRONTMATTER_KEYS, NOTE_STATUS, SECTION_HEADINGS
from tldw.services.summary import SummaryService
from tldw.services.template import BUILTIN_SECTIONS, NoteTemplate, TemplateService
from tldw.storage import MESSAGE_TEMPLATE_STORAGE_FAILED, TemplateStore
from tldw.storage.templates import TEMPLATES_DIR_NAME

USER_A = 42
USER_B = 1001
#: Описание, которое «разбирает» фейковая граница LLM.
DESCRIPTION = "Разделы: Коротко, Примеры кода, Что попробовать. Тон: короче, с акцентом на код"
#: Пользовательский шаблон: порядок и тон, отличные от встроенных.
CUSTOM_SECTIONS = ("Коротко", "Примеры кода", "Что попробовать")
CUSTOM_TONE = "короче, с акцентом на код"
#: Имя файла заметки — slug заголовка видео, одинаковый для всех сценариев.
NOTE_FILENAME = "как-учиться-эффективно.md"


@dataclass
class NoteCapture:
    """Колбэк ``on_request``: запоминает ``.md`` в момент отправки документа."""

    markdowns: list[str] = field(default_factory=list)
    filenames: list[str] = field(default_factory=list)
    paths: list[Path] = field(default_factory=list)

    def __call__(self, method: Any) -> None:
        if not isinstance(method, SendDocument):
            return
        path = Path(method.document.path)
        self.markdowns.append(path.read_text(encoding="utf-8"))
        self.filenames.append(method.document.filename)
        self.paths.append(path)


def build_summary_fakes(
    settings: Settings,
) -> tuple[FakeVideoSource, FakeSummarizer, SummaryService]:
    """Фейковые YouTube/LLM и настоящий ``SummaryService`` поверх них."""
    source = FakeVideoSource(video=make_video())
    summarizer = FakeSummarizer(summary=make_summary(), sections_from_request=True)
    return source, summarizer, SummaryService.from_settings(settings, source, summarizer)


async def save_template_via_ui(app: AppHarness) -> None:
    """Провести пользователя по сценарию сохранения шаблона (шаги без проверок)."""
    await app.send("/template")
    await app.click(ACTION_EDIT)
    await app.send(DESCRIPTION)
    await app.click(ACTION_SAVE)


def assert_custom_sections(markdown: str) -> str:
    """Проверить ``.md``: неизменный frontmatter и разделы шаблона по порядку."""
    frontmatter, body = parse_frontmatter(markdown)

    # Frontmatter остаётся прежним: ровно 7 полей в прежнем порядке.
    assert frontmatter_keys(markdown) == list(FRONTMATTER_KEYS)
    assert len(frontmatter) == len(FRONTMATTER_KEYS)
    assert frontmatter["title"] == VIDEO_TITLE
    assert frontmatter["source"] == VIDEO_URL
    assert frontmatter["channel"] == VIDEO_CHANNEL
    assert frontmatter["duration"] == VIDEO_DURATION_SECONDS
    assert frontmatter["tags"] == []
    assert frontmatter["status"] == NOTE_STATUS

    # Разделы — ровно пользовательские, в заданном порядке, без встроенных.
    assert body.count("## ") == len(CUSTOM_SECTIONS)
    positions = [body.index(f"## {heading}") for heading in CUSTOM_SECTIONS]
    assert positions == sorted(positions), "разделы должны идти в порядке шаблона"
    for heading in CUSTOM_SECTIONS:
        assert f"- Первый пункт раздела «{heading}»" in body
        assert f"- Второй пункт раздела «{heading}»" in body
    for heading in SECTION_HEADINGS:
        assert heading not in body, f"встроенная секция {heading!r} не должна попасть в шаблон"
    assert markdown.endswith("\n") and not markdown.endswith("\n\n")
    return body


def assert_builtin_sections(markdown: str) -> str:
    """Проверить ``.md`` встроенного пути: 5 секций по порядку и прежний frontmatter."""
    frontmatter, body = parse_frontmatter(markdown)
    assert frontmatter_keys(markdown) == list(FRONTMATTER_KEYS)
    assert len(frontmatter) == len(FRONTMATTER_KEYS)
    assert frontmatter["source"] == VIDEO_URL
    assert frontmatter["status"] == NOTE_STATUS

    positions = [body.index(heading) for heading in SECTION_HEADINGS]
    assert positions == sorted(positions), "секции должны идти в порядке встроенного шаблона"
    assert body.count("## ") == len(SECTION_HEADINGS)
    for heading in CUSTOM_SECTIONS:
        assert f"## {heading}" not in body, "пользовательский раздел не должен применяться"
    return body


# --- Полный сценарий: шаблон → ссылка → .md ---------------------------------


def test_saved_template_is_applied_to_the_next_link(fake_env: dict[str, str]) -> None:
    """``/template`` → описание → «Сохранить» → ссылка даёт ``.md`` по шаблону."""
    settings = load_settings(fake_env)
    parser = FakeTemplateParser(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE)
    source, summarizer, service = build_summary_fakes(settings)
    capture = NoteCapture()

    async def scenario() -> list[Any]:
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
            on_request=capture,
        ) as app:
            # Показ: встроенный шаблон и действия, без LLM и YouTube.
            await app.send("/template")
            view = app.texts[0]
            assert MESSAGE_ORIGIN_BUILTIN in view
            assert expected_numbering(BUILTIN_SECTIONS) in view
            assert MESSAGE_TONE_MISSING in view
            assert app.texts[1] == MESSAGE_VIEW_ACTIONS
            assert inline_buttons(sent_messages(app.requests)[1].reply_markup) == [
                BUTTON_EDIT,
                BUTTON_RESET,
                BUTTON_CLOSE,
            ]
            assert parser.calls == [] and source.calls == [] and summarizer.calls == []

            # Изменение: описание уходит в разбор, показывается кандидат.
            await app.click(ACTION_EDIT)
            assert app.last_text == MESSAGE_ASK_DESCRIPTION
            await app.send(f"  {DESCRIPTION}  ")
            assert MESSAGE_PARSING in app.texts
            candidate = app.last_text
            assert MESSAGE_CANDIDATE_TITLE in candidate
            assert expected_numbering(CUSTOM_SECTIONS) in candidate
            assert CUSTOM_TONE in candidate
            assert inline_buttons(sent_messages(app.requests)[-1].reply_markup) == [
                BUTTON_SAVE,
                BUTTON_FIX,
                BUTTON_CANCEL,
            ]
            assert TemplateStore(settings.data_dir).load(USER_A) is None, (
                "до «Сохранить» шаблон не сохраняется"
            )

            # Подтверждение: только «Сохранить» пишет файл шаблона.
            await app.click(ACTION_SAVE)
            assert MESSAGE_SAVED in app.last_text
            assert MESSAGE_ORIGIN_USER in app.last_text
            assert TemplateStore(settings.data_dir).load(USER_A) == (
                CUSTOM_SECTIONS,
                CUSTOM_TONE,
            )
            assert source.calls == [] and summarizer.calls == [], (
                "сценарий шаблона не должен тратить YouTube/LLM"
            )
            assert list(settings.data_dir.rglob("*.md")) == [], (
                "сценарий шаблона не должен создавать заметку"
            )

            # Ссылка: настоящий обработчик, сервис, рендер и запись файла.
            link_start = len(app.requests)
            await app.send_link(VIDEO_URL)
            return app.requests[link_start:]

    link_requests = asyncio.run(scenario())

    # Пользователь увидел «принял → обрабатываю → документ → готово».
    assert request_types(link_requests) == [
        SendMessage,
        SendMessage,
        SendDocument,
        SendMessage,
    ]
    assert link_requests[0].text == MESSAGE_ACCEPTED
    assert link_requests[1].text == MESSAGE_PROCESSING
    assert link_requests[3].text == MESSAGE_DONE

    # Разбор описания — ровно один вызов границы LLM; ссылка — один YouTube.
    assert parser.calls == [(DESCRIPTION, BUILTIN_SECTIONS, "")]
    assert source.calls == [VIDEO_URL]
    assert summarizer.templates == [(CUSTOM_SECTIONS, CUSTOM_TONE)]

    # Документ существовал в момент отправки и лежит в DATA_DIR.
    assert capture.filenames == [NOTE_FILENAME]
    assert capture.paths[0].parent == settings.data_dir
    assert capture.paths[0].is_file()
    assert sorted(path.name for path in settings.data_dir.glob("*.md")) == [NOTE_FILENAME]
    assert list(settings.data_dir.rglob("*.tmp")) == []
    assert_custom_sections(capture.markdowns[0])


# --- Сброс ------------------------------------------------------------------


def test_reset_after_confirmation_returns_builtin_sections(fake_env: dict[str, str]) -> None:
    """После «Сбросить» + подтверждения ссылка даёт заметку по встроенным 5 секциям."""
    settings = load_settings(fake_env)
    store = TemplateStore(settings.data_dir)
    TemplateService(store).save(USER_A, NoteTemplate(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE))
    parser = FakeTemplateParser(sections=("Не должно разбираться",), tone=None)
    source, summarizer, service = build_summary_fakes(settings)
    capture = NoteCapture()
    template_path = settings.data_dir / TEMPLATES_DIR_NAME / f"{USER_A}.json"

    async def scenario() -> None:
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
            on_request=capture,
        ) as app:
            await app.send("/template")
            view = app.texts[0]
            assert MESSAGE_ORIGIN_USER in view
            assert expected_numbering(CUSTOM_SECTIONS) in view
            assert CUSTOM_TONE in view

            # Сброс спрашивает подтверждение и ничего не удаляет до него.
            await app.click(ACTION_RESET)
            assert MESSAGE_RESET_ASK in app.last_text
            assert last_inline_buttons(app.requests) == [
                BUTTON_RESET_CONFIRM,
                BUTTON_CANCEL,
            ]
            assert store.load(USER_A) == (CUSTOM_SECTIONS, CUSTOM_TONE)

            await app.click(ACTION_RESET_CONFIRM)
            assert MESSAGE_RESET_DONE in app.last_text
            assert MESSAGE_ORIGIN_BUILTIN in app.last_text
            assert store.load(USER_A) is None
            assert not template_path.exists()
            assert parser.calls == [], "сброс не должен вызывать разбор LLM"
            assert source.calls == [] and summarizer.calls == []

            await app.send_link(VIDEO_URL)

    asyncio.run(scenario())

    assert source.calls == [VIDEO_URL]
    assert summarizer.templates == [(None, "")]
    assert_builtin_sections(capture.markdowns[0])
    assert sorted(path.name for path in settings.data_dir.glob("*.md")) == [NOTE_FILENAME]


# --- Изоляция пользователей -------------------------------------------------


def test_template_of_user_a_is_not_applied_to_user_b(fake_env: dict[str, str]) -> None:
    """Сохранённый шаблон A применяется к A и не попадает в заметку B."""
    settings = load_settings({**fake_env, "TELEGRAM_ALLOWED_USER_IDS": f"{USER_A} {USER_B}"})
    parser = FakeTemplateParser(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE)
    source, summarizer, service = build_summary_fakes(settings)
    capture = NoteCapture()

    async def scenario() -> tuple[str, str]:
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
            on_request=capture,
        ) as app:
            await save_template_via_ui(app)
            assert TemplateStore(settings.data_dir).load(USER_A) == (
                CUSTOM_SECTIONS,
                CUSTOM_TONE,
            )

            # B видит встроенный шаблон, а не шаблон A.
            await app.send("/template", user_id=USER_B)
            assert MESSAGE_ORIGIN_BUILTIN in app.texts[-2]
            assert MESSAGE_ORIGIN_USER not in app.texts[-2]
            assert expected_numbering(BUILTIN_SECTIONS) in app.texts[-2]

            await app.send_link(VIDEO_URL, user_id=USER_A)
            note_a = capture.markdowns[-1]
            await app.send_link(VIDEO_URL, user_id=USER_B)
            note_b = capture.markdowns[-1]
            return note_a, note_b

    note_a, note_b = asyncio.run(scenario())

    assert len(capture.markdowns) == 2
    assert_custom_sections(note_a)
    assert_builtin_sections(note_b)
    assert summarizer.templates == [(CUSTOM_SECTIONS, CUSTOM_TONE), (None, "")]
    assert source.calls == [VIDEO_URL, VIDEO_URL]
    assert sorted(path.name for path in (settings.data_dir / TEMPLATES_DIR_NAME).iterdir()) == [
        f"{USER_A}.json"
    ], "шаблон B не должен появиться"


# --- Перезапуск -------------------------------------------------------------


def test_saved_template_survives_restart(fake_env: dict[str, str]) -> None:
    """Сохранённый шаблон читается новым экземпляром приложения и хранилища."""
    settings = load_settings(fake_env)
    parser = FakeTemplateParser(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE)
    source, summarizer, service = build_summary_fakes(settings)
    capture = NoteCapture()

    async def scenario() -> None:
        # Первый «запуск»: пользователь сохраняет шаблон и приложение закрывается.
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
        ) as app:
            await save_template_via_ui(app)
        assert TemplateStore(settings.data_dir).load(USER_A) == (CUSTOM_SECTIONS, CUSTOM_TONE)

        # Второй «запуск»: новые диспетчер, хранилище и FSM, тот же DATA_DIR.
        parser_after_restart = FakeTemplateParser()
        source_after_restart, summarizer_after_restart, service_after_restart = (
            build_summary_fakes(settings)
        )
        async with app_harness(
            settings,
            summary_service=service_after_restart,
            template_parser=parser_after_restart,
            on_request=capture,
        ) as app:
            await app.send("/template")
            view = app.texts[0]
            assert MESSAGE_ORIGIN_USER in view
            assert expected_numbering(CUSTOM_SECTIONS) in view
            assert CUSTOM_TONE in view
            assert parser_after_restart.calls == [], "шаблон читается из файла, а не разбирается"

            await app.send_link(VIDEO_URL)

        assert source_after_restart.calls == [VIDEO_URL]
        assert summarizer_after_restart.templates == [(CUSTOM_SECTIONS, CUSTOM_TONE)]
        assert parser.calls == [(DESCRIPTION, BUILTIN_SECTIONS, "")]

    asyncio.run(scenario())

    assert_custom_sections(capture.markdowns[0])


def test_unconfirmed_candidate_does_not_survive_restart(fake_env: dict[str, str]) -> None:
    """Неподтверждённый кандидат живёт только в FSM и после перезапуска исчезает."""
    settings = load_settings(fake_env)
    parser = FakeTemplateParser(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE)
    source, summarizer, service = build_summary_fakes(settings)
    capture = NoteCapture()

    async def scenario() -> None:
        # Первый «запуск»: кандидат показан, но «Сохранить» не нажато.
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
        ) as app:
            await app.send("/template")
            await app.click(ACTION_EDIT)
            await app.send(DESCRIPTION)
            assert MESSAGE_CANDIDATE_TITLE in app.last_text
            assert TemplateStore(settings.data_dir).load(USER_A) is None

        # Второй «запуск»: кандидата нет, активный шаблон по-прежнему встроенный.
        source_after_restart, summarizer_after_restart, service_after_restart = (
            build_summary_fakes(settings)
        )
        async with app_harness(
            settings,
            summary_service=service_after_restart,
            template_parser=FakeTemplateParser(),
            on_request=capture,
        ) as app:
            await app.click(ACTION_SAVE)
            assert MESSAGE_STALE_CANDIDATE in app.last_text
            assert TemplateStore(settings.data_dir).load(USER_A) is None

            await app.send_link(VIDEO_URL)

        assert summarizer_after_restart.templates == [(None, "")]
        assert source_after_restart.calls == [VIDEO_URL]

    asyncio.run(scenario())

    assert_builtin_sections(capture.markdowns[0])
    assert list((settings.data_dir / TEMPLATES_DIR_NAME).glob("*.json")) == []


# --- Негативные сценарии ----------------------------------------------------


def test_parse_error_keeps_previous_template_and_creates_no_note(
    fake_env: dict[str, str],
) -> None:
    """Ошибка разбора: понятное сообщение, прежний шаблон и никакой заметки."""
    settings = load_settings(fake_env)
    store = TemplateStore(settings.data_dir)
    TemplateService(store).save(USER_A, NoteTemplate(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE))
    template_path = settings.data_dir / TEMPLATES_DIR_NAME / f"{USER_A}.json"
    error_text = "В описании не нашлось изменений разделов или тона."
    parser = FakeTemplateParser(error=TemplateParseError(error_text))
    source, summarizer, service = build_summary_fakes(settings)
    capture = NoteCapture()

    async def scenario() -> None:
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
            on_request=capture,
        ) as app:
            await app.send("/template")
            await app.click(ACTION_EDIT)
            await app.send(DESCRIPTION)

            assert error_text in app.last_text, "пользователь должен увидеть причину"
            assert MESSAGE_DESCRIPTION_RETRY in app.last_text
            assert "Traceback" not in app.last_text
            assert store.load(USER_A) == (CUSTOM_SECTIONS, CUSTOM_TONE)
            before = template_path.read_text(encoding="utf-8")

            # Кандидата нет: «Сохранить» после ошибки ничего не пишет.
            await app.click(ACTION_SAVE)
            assert MESSAGE_STALE_CANDIDATE in app.last_text
            assert template_path.read_text(encoding="utf-8") == before
            assert source.calls == [] and summarizer.calls == []
            assert list(settings.data_dir.rglob("*.md")) == []

            # Прежний шаблон остался активным и применяется к ссылке.
            await app.send_link(VIDEO_URL)

    asyncio.run(scenario())

    assert len(parser.calls) == 1
    assert summarizer.templates == [(CUSTOM_SECTIONS, CUSTOM_TONE)]
    assert_custom_sections(capture.markdowns[0])


def test_link_while_waiting_for_description_does_not_process_video(
    fake_env: dict[str, str],
) -> None:
    """Ссылка во время ввода описания не тратит YouTube/LLM и не меняет шаблон."""
    settings = load_settings(fake_env)
    store = TemplateStore(settings.data_dir)
    saved = NoteTemplate(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE)
    TemplateService(store).save(USER_A, saved)
    parser = FakeTemplateParser(sections=("Новый", "Набор"), tone="строго")
    source, summarizer, service = build_summary_fakes(settings)

    async def scenario() -> None:
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
        ) as app:
            await app.send("/template")
            await app.click(ACTION_EDIT)
            await app.send(VIDEO_URL)

            assert app.last_text == MESSAGE_LINK_WHILE_DESCRIPTION
            assert MESSAGE_ACCEPTED not in app.texts, "видео не должно обрабатываться"
            assert parser.calls == [] and source.calls == [] and summarizer.calls == []
            assert list(settings.data_dir.rglob("*.md")) == []
            active = TemplateService(store).get_active(USER_A)
            assert active.sections == CUSTOM_SECTIONS
            assert active.tone == CUSTOM_TONE, "активный шаблон должен остаться прежним"

            # Сценарий не сломан: описание после ссылки всё ещё принимается.
            await app.send(DESCRIPTION)
            assert MESSAGE_CANDIDATE_TITLE in app.last_text
            assert store.load(USER_A) == (CUSTOM_SECTIONS, CUSTOM_TONE)

    asyncio.run(scenario())

    assert source.calls == [] and summarizer.calls == []


def test_stale_buttons_do_not_change_saved_template(fake_env: dict[str, str]) -> None:
    """Повторная и устаревшая кнопка не сохраняет и не сбрасывает шаблон дважды."""
    settings = load_settings(fake_env)
    parser = FakeTemplateParser(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE)
    source, summarizer, service = build_summary_fakes(settings)
    store = TemplateStore(settings.data_dir)
    template_path = settings.data_dir / TEMPLATES_DIR_NAME / f"{USER_A}.json"

    async def scenario() -> None:
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
        ) as app:
            await save_template_via_ui(app)
            payload = template_path.read_text(encoding="utf-8")
            # Повторная запись того же JSON не изменила бы текст, поэтому
            # проверяем сам файл: атомарная замена меняет inode и mtime.
            saved_stat = template_path.stat()

            # Повторное «Сохранить» после очистки FSM.
            await app.click(ACTION_SAVE)
            assert MESSAGE_STALE_CANDIDATE in app.last_text
            assert template_path.read_text(encoding="utf-8") == payload
            assert (template_path.stat().st_ino, template_path.stat().st_mtime_ns) == (
                saved_stat.st_ino,
                saved_stat.st_mtime_ns,
            ), "устаревшая кнопка не должна перезаписывать файл шаблона"

            # «Да, сбросить» без предварительного «Сбросить».
            await app.click(ACTION_RESET_CONFIRM)
            assert MESSAGE_STALE_CALLBACK in app.last_text
            assert template_path.read_text(encoding="utf-8") == payload
            assert store.load(USER_A) == (CUSTOM_SECTIONS, CUSTOM_TONE)
            assert len(list((settings.data_dir / TEMPLATES_DIR_NAME).glob("*.json"))) == 1
            assert list(settings.data_dir.rglob("*.md")) == [], (
                "кнопки шаблона не должны создавать заметку"
            )

            await app.send_link(VIDEO_URL)

    asyncio.run(scenario())

    assert summarizer.templates == [(CUSTOM_SECTIONS, CUSTOM_TONE)]
    assert source.calls == [VIDEO_URL]


def test_storage_write_error_keeps_builtin_template_and_no_note(
    fake_env: dict[str, str],
) -> None:
    """Ошибка записи: понятное сообщение, встроенный шаблон, заметка не по шаблону.

    Ошибка настоящая: место каталога шаблонов занято файлом, поэтому
    ``TemplateStore.save`` не может создать каталог и поднимает
    ``TemplateStorageError``. Хранилище при этом остаётся настоящим.
    """
    settings = load_settings(fake_env)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    blocker = settings.data_dir / TEMPLATES_DIR_NAME
    blocker.write_text("не каталог", encoding="utf-8")
    parser = FakeTemplateParser(sections=CUSTOM_SECTIONS, tone=CUSTOM_TONE)
    source, summarizer, service = build_summary_fakes(settings)
    capture = NoteCapture()

    async def scenario() -> None:
        async with app_harness(
            settings,
            summary_service=service,
            template_parser=parser,
            on_request=capture,
        ) as app:
            await app.send("/template")
            await app.click(ACTION_EDIT)
            await app.send(DESCRIPTION)
            assert MESSAGE_CANDIDATE_TITLE in app.last_text

            await app.click(ACTION_SAVE)
            assert app.last_text == MESSAGE_TEMPLATE_STORAGE_FAILED
            assert "Traceback" not in app.last_text
            assert blocker.is_file(), "сбой записи не должен подменять каталог"
            assert blocker.read_text(encoding="utf-8") == "не каталог"
            assert TemplateService(TemplateStore(settings.data_dir)).get_active(USER_A).is_builtin
            assert source.calls == [] and summarizer.calls == []
            assert list(settings.data_dir.rglob("*.md")) == []

            # Кандидат отброшен, ссылка обрабатывается по встроенному шаблону.
            await app.click(ACTION_CANCEL)
            assert app.last_text == MESSAGE_CLOSED
            await app.send_link(VIDEO_URL)

    asyncio.run(scenario())

    assert summarizer.templates == [(None, "")]
    assert source.calls == [VIDEO_URL]
    assert_builtin_sections(capture.markdowns[0])

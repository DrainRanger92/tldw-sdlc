"""Общий каркас офлайновых тестов Telegram-слоя TLDW.

Модуль не собирается pytest'ом (имя без префикса ``test_``), но его
импортируют тест обработчика ссылки, тест сценария шаблона, тест ошибок
сценария и сквозные тесты «ссылка → ``.md``» / «``/template`` → ``.md``»:
записывающая сессия aiogram, стенд диспетчера, стенд шаблонного сценария
(``TemplateStand``), стенд собранного приложения (``AppHarness``), фейковые
адаптеры, фейковый разбор описания шаблона, билдеры видео/конспекта и разбор
frontmatter живут здесь, а не в нескольких копиях.

Сеть и токены не нужны: апдейт проходит через настоящий ``Dispatcher``,
а ``FakeSession`` записывает методы Telegram вместо вызова API. Токен бота и
прочие секреты ниже — явно фиктивные и не являются секретами (их значения
входят в allow-list проверки ``test_readme.py``).
"""

from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Coroutine, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.base import BaseSession
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, Update

from tldw.adapters.llm import (
    SummaryQuote,
    SummarySection,
    SummaryTimelineEntry,
    TemplatePatchDraft,
    VideoSummary,
)
from tldw.adapters.youtube import Transcript, TranscriptSegment, Video, VideoMetadata
from tldw.bootstrap import Application, build_application
from tldw.config import Settings
from tldw.handlers import LinkHandler, SummaryProcessor, TemplateHandler, TemplateParser
from tldw.handlers.template import TemplateAction
from tldw.services.note import Note
from tldw.services.summary import SummaryResult, SummaryService, TranscriptText
from tldw.services.template import NoteTemplate, TemplateService

#: Фиктивные секреты: форма как у настоящих, значения — заведомо ненастоящие.
FAKE_BOT_TOKEN = "123456789:TEST-fake-token-not-a-secret"
FAKE_API_KEY = "sk-fake-key-0123456789"
FAKE_ENV_KEY = "fake-llm-key-value"
#: Bearer-токен без префикса ``sk-``: шаблон ключей OpenAI его не ловит,
#: поэтому проверяется именно очистка заголовка ``Authorization``.
FAKE_BEARER_TOKEN = "mysecretvalue12345"
#: Разрешённые значения для проверки «в коде и тестах нет настоящих секретов».
FAKE_SECRETS = frozenset({FAKE_BOT_TOKEN, FAKE_API_KEY, FAKE_ENV_KEY, FAKE_BEARER_TOKEN})

#: Ссылка и метаданные видео сценария «ссылка → ``.md``».
VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO_ID = "dQw4w9WgXcQ"
VIDEO_TITLE = "Как учиться эффективно"
VIDEO_CHANNEL = "Академия обучения"
VIDEO_DURATION_SECONDS = 754


def parse_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    """Разобрать YAML frontmatter и вернуть его вместе с телом заметки.

    Общий помощник сквозных тестов: заметка обязана начинаться с frontmatter,
    а ``yaml.safe_load`` проверяет его корректность.
    """
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", markdown, flags=re.DOTALL)
    assert match is not None, "заметка должна начинаться с YAML frontmatter"
    return yaml.safe_load(match.group(1)), match.group(2)


def frontmatter_keys(markdown: str) -> list[str]:
    """Ключи frontmatter в том порядке, в каком они записаны в файле."""
    raw = markdown.split("---\n", 2)[1]
    return [
        line.split(":", 1)[0]
        for line in raw.splitlines()
        if line and not line.startswith(" ")
    ]


class FakeTemplateRepository:
    """Хранилище шаблонов в памяти, которое записывает вызовы сервиса.

    Общий фейк для тестов домена шаблона и разбора описания: обмен идёт
    примитивами ``(разделы, тон)``, как у :class:`tldw.services.TemplateRepository`.
    """

    def __init__(self) -> None:
        self.records: dict[int, tuple[tuple[str, ...], str]] = {}
        self.loads: list[int] = []
        self.saves: list[tuple[int, tuple[tuple[str, ...], str]]] = []
        self.deletes: list[int] = []

    def load(self, user_id: int) -> tuple[tuple[str, ...], str] | None:
        self.loads.append(user_id)
        return self.records.get(user_id)

    def save(self, user_id: int, record: tuple[tuple[str, ...], str]) -> None:
        self.saves.append((user_id, record))
        self.records[user_id] = record

    def delete(self, user_id: int) -> None:
        self.deletes.append(user_id)
        self.records.pop(user_id, None)


class FakeTemplateParser:
    """Фейковый разбор описания шаблона: заданный патч или ошибка и запись вызовов.

    Структурно реализует ``tldw.handlers.template.TemplateParser`` и возвращает
    ``TemplatePatchDraft`` адаптера LLM, поэтому тест проверяет тот же контракт
    «примитивы + None значит не менять», что и настоящий клиент. ``thread_ids``
    позволяет убедиться, что разбор уходит в рабочий поток.
    """

    def __init__(
        self,
        *,
        sections: tuple[str, ...] | None = None,
        tone: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.sections = sections
        self.tone = tone
        self.error = error
        self.calls: list[tuple[str, tuple[str, ...], str]] = []
        self.thread_ids: list[int] = []

    def parse_template(
        self,
        text: str,
        *,
        current_sections: Sequence[str],
        current_tone: str = "",
    ) -> TemplatePatchDraft:
        """Записать вызов и вернуть заданный патч или поднять заданную ошибку."""
        self.calls.append((text, tuple(current_sections), current_tone))
        self.thread_ids.append(threading.get_ident())
        if self.error is not None:
            raise self.error
        return TemplatePatchDraft(sections=self.sections, tone=self.tone)


class FakeSession(BaseSession):
    """Сессия aiogram, которая записывает методы вместо вызова Telegram API."""

    def __init__(self, on_request: Callable[[Any], None] | None = None) -> None:
        super().__init__()
        self.requests: list[Any] = []
        self._on_request = on_request

    async def make_request(self, bot: Bot, method: Any, timeout: int | None = None) -> Any:
        if self._on_request is not None:
            self._on_request(method)
        self.requests.append(method)
        return True

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield b""

    async def close(self) -> None:
        """Сетевых ресурсов у фейковой сессии нет."""


def make_update(
    text: str,
    *,
    update_id: int = 1,
    user_id: int = 42,
    chat_id: int | None = None,
    with_sender: bool = True,
) -> Update:
    """Собрать апдейт с текстовым сообщением; чат приватный по умолчанию."""
    message: dict[str, Any] = {
        "message_id": 10,
        "date": 0,
        "chat": {"id": user_id if chat_id is None else chat_id, "type": "private"},
        "text": text,
    }
    if with_sender:
        message["from"] = {"id": user_id, "is_bot": False, "first_name": "Тест"}
    return Update.model_validate({"update_id": update_id, "message": message})


def make_callback_update(
    data: str,
    *,
    update_id: int = 2,
    user_id: int = 42,
    chat_id: int | None = None,
    message_id: int = 10,
    inaccessible: bool = False,
) -> Update:
    """Собрать апдейт с нажатием inline-кнопки под сообщением бота.

    ``date`` сообщения по умолчанию ненулевая: aiogram по нулевой дате считает
    сообщение недоступным (``InaccessibleMessage``), и проверить клавиатуры не
    выйдет. ``inaccessible=True`` даёт именно такой апдейт — для проверки
    запасного пути «сообщение недоступно, отправляем новое».
    """
    return Update.model_validate(
        {
            "update_id": update_id,
            "callback_query": {
                "id": f"callback-{update_id}",
                "from": {"id": user_id, "is_bot": False, "first_name": "Тест"},
                "chat_instance": "test-chat-instance",
                "data": data,
                "message": {
                    "message_id": message_id,
                    "date": 0 if inaccessible else 1_700_000_000,
                    "chat": {"id": user_id if chat_id is None else chat_id, "type": "private"},
                    "from": {"id": 999999, "is_bot": True, "first_name": "TLDW"},
                    "text": "Шаблон",
                },
            },
        }
    )


@dataclass
class Harness:
    """Собранный фейковый бот: диспетчер, обработчики и журнал методов."""

    bot: Bot
    dispatcher: Dispatcher
    handler: LinkHandler
    session: FakeSession
    template_handler: TemplateHandler | None = None

    async def send(
        self,
        text: str,
        *,
        user_id: int = 42,
        with_sender: bool = True,
    ) -> None:
        await self.dispatcher.feed_update(
            self.bot,
            make_update(text, user_id=user_id, with_sender=with_sender),
        )

    async def send_callback(
        self,
        data: str,
        *,
        user_id: int = 42,
        inaccessible: bool = False,
    ) -> None:
        """Нажать inline-кнопку с данными сценария шаблона."""
        await self.dispatcher.feed_update(
            self.bot,
            make_callback_update(data, user_id=user_id, inaccessible=inaccessible),
        )

    async def close(self) -> None:
        await self.bot.session.close()


def make_harness(
    service: SummaryProcessor,
    *,
    allowed_user_ids: tuple[int, ...] = (),
    on_request: Callable[[Any], None] | None = None,
    template_handler: TemplateHandler | None = None,
    session: FakeSession | None = None,
    template_service: TemplateService | None = None,
) -> Harness:
    """Собрать стенд с настоящими обработчиками и фейковой сессией.

    Обработчик шаблона регистрируется раньше обработчика ссылок — так же, как в
    :func:`tldw.handlers.router.create_router`, поэтому порядок из продакшна
    проверяется в тестах сценария шаблона. ``template_service`` проводит
    активный шаблон в обработчик ссылки (T-008), как это делает роутер.
    """
    session = session if session is not None else FakeSession(on_request)
    bot = Bot(token=FAKE_BOT_TOKEN, session=session)
    router = Router(name="test-tldw")
    if template_handler is not None:
        template_handler.register(router)
    handler = LinkHandler(
        service,
        allowed_user_ids=allowed_user_ids,
        template_service=template_service,
    )
    handler.register(router)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return Harness(
        bot=bot,
        dispatcher=dispatcher,
        handler=handler,
        session=session,
        template_handler=template_handler,
    )


def run_message(
    service: SummaryProcessor,
    text: str,
    *,
    allowed_user_ids: tuple[int, ...] = (),
    user_id: int = 42,
    with_sender: bool = True,
    on_request: Callable[[Any], None] | None = None,
    template_service: TemplateService | None = None,
) -> list[Any]:
    """Пропустить сообщение через диспетчер и дождаться фоновой обработки."""

    async def scenario() -> list[Any]:
        harness = make_harness(
            service,
            allowed_user_ids=allowed_user_ids,
            on_request=on_request,
            template_service=template_service,
        )
        try:
            await harness.send(text, user_id=user_id, with_sender=with_sender)
            await harness.handler.wait_idle()
        finally:
            await harness.close()
        return harness.session.requests

    return asyncio.run(scenario())


def request_types(requests: list[Any]) -> list[type]:
    return [type(request) for request in requests]


# --- Стенд собранного приложения (сквозной сценарий T-010) -------------------


@dataclass
class AppHarness:
    """Собранное ``build_application`` приложение с фейковой сессией Telegram.

    Здесь настоящие ``Dispatcher``, роутер и обработчики, ``TemplateService``,
    ``TemplateStore`` в ``DATA_DIR``, ``SummaryService`` и рендер заметки;
    подменены только внешние границы: сессия Telegram (``FakeSession``) и
    переданные тестом фейки YouTube/LLM. Стенд нужен для сценариев, где
    шаблон, сохранённый в одном «запуске», проверяется ссылкой и переживает
    перезапуск: новый экземпляр — новый ``AppHarness`` с тем же ``Settings``.

    ``link_handler`` — обработчик ссылки из собранного роутера: через него
    тест дожидается фоновой обработки видео (:meth:`send_link`).
    """

    application: Application
    settings: Settings
    session: FakeSession
    link_handler: LinkHandler
    _original_session: BaseSession
    _update_id: int = field(default=0, repr=False)

    @property
    def requests(self) -> list[Any]:
        """Все методы Telegram, записанные фейковой сессией."""
        return self.session.requests

    @property
    def texts(self) -> list[str]:
        """Тексты отправленных и изменённых сообщений по порядку."""
        return texts(self.requests)

    @property
    def last_text(self) -> str:
        """Последний показанный текст."""
        return self.texts[-1]

    def _next_update_id(self) -> int:
        """Уникальный ``update_id``: апдейты одного стенда не повторяются."""
        self._update_id += 1
        return self._update_id

    async def send(self, text: str, *, user_id: int = 42) -> None:
        """Отправить текстовое сообщение через настоящий диспетчер."""
        await self.application.dispatcher.feed_update(
            self.application.bot,
            make_update(text, update_id=self._next_update_id(), user_id=user_id),
        )

    async def click(self, action: str, *, user_id: int = 42) -> None:
        """Нажать inline-кнопку сценария шаблона."""
        await self.application.dispatcher.feed_update(
            self.application.bot,
            make_callback_update(
                template_action(action),
                update_id=self._next_update_id(),
                user_id=user_id,
            ),
        )

    async def send_link(self, text: str, *, user_id: int = 42) -> None:
        """Отправить ссылку и дождаться фоновой обработки видео."""
        await self.send(text, user_id=user_id)
        await self.link_handler.wait_idle()

    async def close(self) -> None:
        """Закрыть фейковую сессию и сессию, созданную при сборке бота."""
        await self.application.close()
        await self._original_session.close()


def _find_link_handler(dispatcher: Dispatcher) -> LinkHandler:
    """Найти обработчик ссылки среди message-обработчиков собранного роутера."""
    for router in dispatcher.sub_routers:
        for registered in router.message.handlers:
            owner = getattr(registered.callback, "__self__", None)
            if isinstance(owner, LinkHandler):
                return owner
    raise AssertionError("build_application не зарегистрировал обработчик ссылки")


def build_app_harness(
    settings: Settings,
    *,
    summary_service: SummaryProcessor | None = None,
    template_service: TemplateService | None = None,
    template_parser: TemplateParser | None = None,
    on_request: Callable[[Any], None] | None = None,
) -> AppHarness:
    """Собрать приложение из настроек и подменить его сессию Telegram.

    :param summary_service: сервис конспекта с фейковыми YouTube/LLM (тест).
    :param template_service: сервис шаблонов; ``None`` — настоящий
        ``TemplateStore`` в ``settings.data_dir``.
    :param template_parser: фейковый разбор описания (граница LLM).
    :param on_request: колбэк на каждый метод Telegram — позволяет проверить
        файл заметки в момент отправки.
    """
    application = build_application(
        settings,
        summary_service=summary_service,
        template_service=template_service,
        template_parser=template_parser,
    )
    original_session = application.bot.session
    session = FakeSession(on_request)
    application.bot.session = session
    return AppHarness(
        application=application,
        settings=settings,
        session=session,
        link_handler=_find_link_handler(application.dispatcher),
        _original_session=original_session,
    )


@asynccontextmanager
async def app_harness(settings: Settings, **kwargs: Any) -> AsyncIterator[AppHarness]:
    """Открыть стенд приложения на время сценария и закрыть его в конце."""
    harness = build_app_harness(settings, **kwargs)
    try:
        yield harness
    finally:
        await harness.close()


@dataclass
class FakeVideoSource:
    """Фейковый источник видео: отдаёт заданное видео или поднимает ошибку."""

    video: Video | None = None
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str) -> Video:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        assert self.video is not None, "тест не задал видео для фейкового источника"
        return self.video


@dataclass
class FakeSummarizer:
    """Фейковый суммаризатор: отдаёт заданный конспект или ошибку.

    ``calls`` хранит прежнюю тройку ``(транскрипт, title, channel)``, поэтому
    старые тесты не меняются. ``templates`` записывает необязательные разделы и
    тон пользовательского шаблона: на встроенном пути сервис их не передаёт,
    и там остаётся ``(None, "")``.

    ``sections_from_request=True`` — конспект собирается из запрошенных
    разделов: так проверяется пользовательский шаблон, дошедший до генерации.
    """

    summary: VideoSummary | None = None
    error: Exception | None = None
    sections_from_request: bool = False
    calls: list[tuple[str, str, str]] = field(default_factory=list)
    templates: list[tuple[tuple[str, ...] | None, str]] = field(default_factory=list)

    def summarize(
        self,
        transcript: str,
        *,
        title: str = "",
        channel: str = "",
        sections: Sequence[str] | None = None,
        tone: str = "",
    ) -> VideoSummary:
        self.calls.append((transcript, title, channel))
        self.templates.append((None if sections is None else tuple(sections), tone))
        if self.error is not None:
            raise self.error
        if self.sections_from_request and sections is not None:
            return make_template_summary(sections)
        assert self.summary is not None, "тест не задал конспект для фейкового LLM"
        return self.summary


def make_video(
    *,
    url: str = VIDEO_URL,
    title: str = VIDEO_TITLE,
    channel: str = VIDEO_CHANNEL,
    duration_seconds: int = VIDEO_DURATION_SECONDS,
) -> Video:
    """Видео с метаданными и транскриптом для сквозного шаблона заметки."""
    return Video(
        url=url,
        metadata=VideoMetadata(
            video_id=VIDEO_ID,
            title=title,
            channel=channel,
            duration_seconds=duration_seconds,
        ),
        transcript=Transcript(
            segments=(
                TranscriptSegment("Вступление и план видео", 0),
                TranscriptSegment("Метод интервальных повторений", 12),
                TranscriptSegment("Сон закрепляет память", 65),
            ),
            language="ru",
            source="fake",
        ),
    )


def make_summary() -> VideoSummary:
    """Конспект, заполняющий все пять секций полного шаблона заметки."""
    return VideoSummary(
        tldr=(
            "Видео объясняет, как учиться быстрее и не бросать начатое.",
            "Главный вывод: короткие регулярные занятия эффективнее марафонов.",
            "Материал полезен студентам и всем, кто учится самостоятельно.",
        ),
        key_ideas=tuple(f"Идея номер {index}: конкретный приём из видео" for index in range(1, 6)),
        timeline=(
            SummaryTimelineEntry("00:00", "Вступление"),
            SummaryTimelineEntry("00:12", "Метод интервальных повторений"),
        ),
        quotes=(
            SummaryQuote("Повторение — мать учения.", "00:12"),
            SummaryQuote("Сон закрепляет память.", "01:05"),
        ),
        follow_ups=(
            "Проверить метод интервальных повторений на этой неделе.",
            "Составить план повторений на месяц.",
        ),
    )


def make_result(tmp_path: Path, *, filename: str = "kak-uchitsya.md") -> SummaryResult:
    """Результат сервиса конспекта с уже записанным файлом заметки."""
    path = tmp_path / filename
    markdown = "# Заметка\n"
    path.write_text(markdown, encoding="utf-8")
    return SummaryResult(
        note=Note(title="Как учиться"),
        markdown=markdown,
        filename=filename,
        transcript=TranscriptText(),
        path=path,
    )


def make_template_summary(sections: Sequence[str]) -> VideoSummary:
    """Конспект под пользовательский шаблон: по два пункта на каждый раздел."""
    return VideoSummary(
        sections=tuple(
            SummarySection(
                heading=heading,
                items=(f"Первый пункт раздела «{heading}»", f"Второй пункт раздела «{heading}»"),
            )
            for heading in sections
        )
    )


# --- Стенд Telegram-сценария шаблона -----------------------------------------


def template_action(action: str) -> str:
    """Callback-данные сценария шаблона для нажатия кнопки в тесте."""
    return TemplateAction(action=action).pack()


def texts(requests: list[Any]) -> list[str]:
    """Тексты отправленных и изменённых сообщений по порядку."""
    return [
        request.text
        for request in requests
        if isinstance(request, (SendMessage, EditMessageText)) and request.text
    ]


def sent_messages(requests: list[Any]) -> list[SendMessage]:
    """Только новые сообщения (без редактирования старых)."""
    return [request for request in requests if isinstance(request, SendMessage)]


def inline_buttons(markup: Any) -> list[str]:
    """Подписи inline-кнопок в порядке клавиатуры."""
    assert isinstance(markup, InlineKeyboardMarkup), f"ожидалась inline-клавиатура: {markup!r}"
    return [button.text for row in markup.inline_keyboard for button in row]


def last_inline_buttons(requests: list[Any]) -> list[str]:
    """Подписи inline-кнопок последнего нового или изменённого сообщения.

    Сценарий шаблона правит сообщение на месте (``EditMessageText``), поэтому
    искать клавиатуру только среди новых ``SendMessage`` нельзя.
    """
    for request in reversed(requests):
        if isinstance(request, (SendMessage, EditMessageText)) and request.reply_markup is not None:
            return inline_buttons(request.reply_markup)
    raise AssertionError("среди запросов нет сообщения с inline-клавиатурой")


def reply_buttons(markup: Any) -> list[str]:
    """Подписи кнопок текстовой клавиатуры в порядке клавиатуры."""
    assert isinstance(markup, ReplyKeyboardMarkup), f"ожидалась текстовая клавиатура: {markup!r}"
    return [button.text for row in markup.keyboard for button in row]


def expected_numbering(sections: tuple[str, ...]) -> str:
    """Разделы с номерами так, как их показывает бот."""
    return "\n".join(f"{index}. {heading}" for index, heading in enumerate(sections, start=1))


@dataclass
class TemplateStand:
    """Стенд сценария шаблона: настоящие обработчики, фейковые внешние границы.

    ``repository`` — фейковое хранилище в памяти; ``None`` означает файловый
    стенд, где сервис собран поверх настоящего ``TemplateStore``, а журнал
    сохранений и сбросов ведёт само хранилище.
    """

    harness: Harness
    templates: TemplateService
    parser: FakeTemplateParser
    repository: FakeTemplateRepository | None
    source: FakeVideoSource
    summarizer: FakeSummarizer

    @property
    def requests(self) -> list[Any]:
        """Все методы Telegram, записанные фейковой сессией."""
        return self.harness.session.requests

    @property
    def saves(self) -> list[tuple[int, tuple[tuple[str, ...], str]]]:
        """Сохранения шаблона, дошедшие до фейкового хранилища."""
        return [] if self.repository is None else self.repository.saves

    @property
    def deletes(self) -> list[int]:
        """Сбросы шаблона, дошедшие до фейкового хранилища."""
        return [] if self.repository is None else self.repository.deletes

    @property
    def texts(self) -> list[str]:
        """Тексты сообщений сценария по порядку."""
        return texts(self.harness.session.requests)

    @property
    def last_text(self) -> str:
        """Последний показанный текст сценария."""
        return self.texts[-1]

    async def send(self, text: str, *, user_id: int = 42) -> None:
        """Отправить текстовое сообщение пользователя."""
        await self.harness.send(text, user_id=user_id)

    async def click(self, action: str, *, user_id: int = 42) -> None:
        """Нажать inline-кнопку сценария с заданным действием."""
        await self.harness.send_callback(template_action(action), user_id=user_id)


def make_template_stand(
    *,
    parser: FakeTemplateParser | None = None,
    repository: FakeTemplateRepository | None = None,
    templates: TemplateService | None = None,
    user_template: NoteTemplate | None = None,
    allowed_user_ids: tuple[int, ...] = (),
    source: FakeVideoSource | None = None,
    summarizer: FakeSummarizer | None = None,
) -> TemplateStand:
    """Собрать стенд: сервис шаблонов, фейковый разбор и обработчики.

    Обработчик шаблона регистрируется раньше обработчика ссылок (как в
    ``create_router``), а обработка видео остаётся на настоящем
    ``SummaryService`` с фейковыми источником и суммаризатором — так видно, что
    сценарий шаблона не тратит YouTube/LLM.

    :param templates: готовый сервис шаблонов — например, поверх настоящего
        ``TemplateStore``; тогда ``repository`` не нужен и остаётся ``None``.
    :param user_template: заранее сохранённый шаблон пользователя 42.
    """
    if templates is not None and repository is not None:
        raise ValueError("передайте либо templates, либо repository, не оба")
    if templates is None:
        repository = repository if repository is not None else FakeTemplateRepository()
        templates = TemplateService(repository)
        if user_template is not None:
            # Заранее сохранённый шаблон кладём прямо в хранилище: журнал saves
            # остаётся честным счётом сохранений сценария.
            repository.records[42] = (user_template.sections, user_template.tone)
    elif user_template is not None:
        # Файловый стенд: шаблон сохраняет сам сервис, как в продакшне.
        templates.save(42, user_template)
    parser = (
        parser
        if parser is not None
        else FakeTemplateParser(sections=("Коротко", "Примеры кода"), tone="короче")
    )
    source = source if source is not None else FakeVideoSource()
    summarizer = summarizer if summarizer is not None else FakeSummarizer()
    handler = TemplateHandler(templates, parser, allowed_user_ids=allowed_user_ids)
    harness = make_harness(
        SummaryService(source, summarizer),
        allowed_user_ids=allowed_user_ids,
        template_handler=handler,
    )
    return TemplateStand(
        harness=harness,
        templates=templates,
        parser=parser,
        repository=repository,
        source=source,
        summarizer=summarizer,
    )


def run_template_scenario(
    scenario: Callable[[TemplateStand], Coroutine[Any, Any, None]],
    **stand_kwargs: Any,
) -> TemplateStand:
    """Прогнать асинхронный сценарий на стенде и вернуть стенд с журналом."""

    async def main() -> TemplateStand:
        stand = make_template_stand(**stand_kwargs)
        try:
            await scenario(stand)
        finally:
            await stand.harness.close()
        return stand

    return asyncio.run(main())


__all__ = [
    "FAKE_API_KEY",
    "FAKE_BEARER_TOKEN",
    "FAKE_BOT_TOKEN",
    "FAKE_ENV_KEY",
    "FAKE_SECRETS",
    "AppHarness",
    "FakeSession",
    "FakeSummarizer",
    "FakeTemplateParser",
    "FakeTemplateRepository",
    "FakeVideoSource",
    "Harness",
    "TemplateStand",
    "VIDEO_CHANNEL",
    "VIDEO_DURATION_SECONDS",
    "VIDEO_ID",
    "VIDEO_TITLE",
    "VIDEO_URL",
    "app_harness",
    "build_app_harness",
    "expected_numbering",
    "frontmatter_keys",
    "inline_buttons",
    "last_inline_buttons",
    "make_callback_update",
    "make_harness",
    "make_result",
    "make_summary",
    "make_template_stand",
    "make_template_summary",
    "make_update",
    "make_video",
    "parse_frontmatter",
    "reply_buttons",
    "request_types",
    "run_message",
    "run_template_scenario",
    "sent_messages",
    "template_action",
    "texts",
]

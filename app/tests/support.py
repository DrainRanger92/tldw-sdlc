"""Общий каркас офлайновых тестов Telegram-слоя TLDW.

Модуль не собирается pytest'ом (имя без префикса ``test_``), но его
импортируют тест обработчика ссылки и сквозной тест «ссылка → ``.md``»:
записывающая сессия aiogram, стенд диспетчера, фейковые адаптеры и билдеры
видео/конспекта живут здесь, а не в двух копиях.

Сеть и токены не нужны: апдейт проходит через настоящий ``Dispatcher``,
а ``FakeSession`` записывает методы Telegram вместо вызова API. Токен бота и
прочие секреты ниже — явно фиктивные и не являются секретами (их значения
входят в allow-list проверки ``test_readme.py``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.base import BaseSession
from aiogram.types import Update

from tldw.adapters.llm import SummaryQuote, SummaryTimelineEntry, VideoSummary
from tldw.adapters.youtube import Transcript, TranscriptSegment, Video, VideoMetadata
from tldw.handlers import LinkHandler, SummaryProcessor

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
    with_sender: bool = True,
) -> Update:
    """Собрать апдейт с текстовым сообщением в приватном чате."""
    message: dict[str, Any] = {
        "message_id": 10,
        "date": 0,
        "chat": {"id": 42, "type": "private"},
        "text": text,
    }
    if with_sender:
        message["from"] = {"id": user_id, "is_bot": False, "first_name": "Тест"}
    return Update.model_validate({"update_id": update_id, "message": message})


@dataclass
class Harness:
    """Собранный фейковый бот: диспетчер, обработчик и журнал методов."""

    bot: Bot
    dispatcher: Dispatcher
    handler: LinkHandler
    session: FakeSession

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

    async def close(self) -> None:
        await self.bot.session.close()


def make_harness(
    service: SummaryProcessor,
    *,
    allowed_user_ids: tuple[int, ...] = (),
    on_request: Callable[[Any], None] | None = None,
) -> Harness:
    """Собрать стенд с настоящим ``LinkHandler`` и фейковой сессией."""
    session = FakeSession(on_request)
    bot = Bot(token=FAKE_BOT_TOKEN, session=session)
    handler = LinkHandler(service, allowed_user_ids=allowed_user_ids)
    router = Router(name="test-tldw")
    handler.register(router)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return Harness(bot=bot, dispatcher=dispatcher, handler=handler, session=session)


def run_message(
    service: SummaryProcessor,
    text: str,
    *,
    allowed_user_ids: tuple[int, ...] = (),
    user_id: int = 42,
    with_sender: bool = True,
    on_request: Callable[[Any], None] | None = None,
) -> list[Any]:
    """Пропустить сообщение через диспетчер и дождаться фоновой обработки."""

    async def scenario() -> list[Any]:
        harness = make_harness(
            service,
            allowed_user_ids=allowed_user_ids,
            on_request=on_request,
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
    """Фейковый суммаризатор: отдаёт заданный конспект или ошибку."""

    summary: VideoSummary | None = None
    error: Exception | None = None
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    def summarize(self, transcript: str, *, title: str = "", channel: str = "") -> VideoSummary:
        self.calls.append((transcript, title, channel))
        if self.error is not None:
            raise self.error
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


__all__ = [
    "FAKE_API_KEY",
    "FAKE_BEARER_TOKEN",
    "FAKE_BOT_TOKEN",
    "FAKE_ENV_KEY",
    "FAKE_SECRETS",
    "FakeSession",
    "FakeSummarizer",
    "FakeVideoSource",
    "Harness",
    "VIDEO_CHANNEL",
    "VIDEO_DURATION_SECONDS",
    "VIDEO_ID",
    "VIDEO_TITLE",
    "VIDEO_URL",
    "make_harness",
    "make_summary",
    "make_update",
    "make_video",
    "request_types",
    "run_message",
]

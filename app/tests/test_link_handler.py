"""Тесты Telegram-обработчика ссылки на фейковом боте.

Сеть и токены не нужны: aiogram-сессия подменена записывающей ``FakeSession``,
сервис конспекта — фейком (кроме одного интеграционного кейса с настоящим
``SummaryService`` и каталогом данных в ``tmp_path``). Проверяются:

- порядок ответов «принял → обрабатываю → документ → готово»;
- тяжёлый вызов сервиса выполняется в рабочем потоке и не блокирует loop;
- текст без ссылки даёт подсказку про YouTube, а из нескольких ссылок
  обрабатывается первая;
- ``.md`` лежит в ``DATA_DIR`` до отправки и уходит документом с именем
  из шаблона заметки.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendDocument, SendMessage
from aiogram.types import FSInputFile, Update

from tldw.adapters.llm import SummaryQuote, SummaryTimelineEntry, VideoSummary
from tldw.adapters.youtube import (
    NotYouTubeURLError,
    Transcript,
    TranscriptSegment,
    Video,
    VideoMetadata,
)
from tldw.config import load_settings
from tldw.handlers import (
    MESSAGE_ACCEPTED,
    MESSAGE_DONE,
    MESSAGE_FAILED,
    MESSAGE_NEED_LINK,
    MESSAGE_NO_FILE,
    MESSAGE_PROCESSING,
    LinkHandler,
    SummaryProcessor,
    create_router,
    extract_first_url,
)
from tldw.services.note import Note, note_filename
from tldw.services.summary import SummaryResult, SummaryService, TranscriptText

FAKE_BOT_TOKEN = "123456789:TEST-fake-token-not-a-secret"
YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO_ID = "dQw4w9WgXcQ"


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


@dataclass
class Harness:
    """Собранный фейковый бот: диспетчер, обработчик и журнал методов."""

    bot: Bot
    dispatcher: Dispatcher
    handler: LinkHandler
    session: FakeSession

    async def send(self, text: str) -> None:
        await self.dispatcher.feed_update(self.bot, make_update(text))

    async def close(self) -> None:
        await self.bot.session.close()


def make_update(text: str, *, update_id: int = 1) -> Update:
    """Собрать апдейт с текстовым сообщением в приватном чате."""
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": 10,
                "date": 0,
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Тест"},
                "text": text,
            },
        }
    )


def make_harness(
    service: SummaryProcessor,
    *,
    on_request: Callable[[Any], None] | None = None,
) -> Harness:
    session = FakeSession(on_request)
    bot = Bot(token=FAKE_BOT_TOKEN, session=session)
    handler = LinkHandler(service)
    router = Router(name="test-tldw")
    handler.register(router)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return Harness(bot=bot, dispatcher=dispatcher, handler=handler, session=session)


def run_message(
    service: SummaryProcessor,
    text: str,
    *,
    on_request: Callable[[Any], None] | None = None,
) -> list[Any]:
    """Пропустить сообщение через диспетчер и дождаться фоновой обработки."""

    async def scenario() -> list[Any]:
        harness = make_harness(service, on_request=on_request)
        try:
            await harness.send(text)
            await harness.handler.wait_idle()
        finally:
            await harness.close()
        return harness.session.requests

    return asyncio.run(scenario())


@dataclass
class FakeSummaryService:
    """Фейковый сервис конспекта: результат, ошибка и запись вызовов."""

    result: SummaryResult | None = None
    error: Exception | None = None
    started: threading.Event | None = None
    release: threading.Event | None = None
    calls: list[str] = field(default_factory=list)
    thread_ids: list[int] = field(default_factory=list)

    def process(self, url: str) -> SummaryResult:
        self.calls.append(url)
        self.thread_ids.append(threading.get_ident())
        if self.started is not None:
            self.started.set()
        if self.release is not None and not self.release.wait(timeout=5):
            raise AssertionError("тест не отпустил обработку")
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def make_result(tmp_path: Path, *, filename: str = "kak-uchitsya.md") -> SummaryResult:
    """Результат сервиса с уже записанным файлом заметки."""
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


def request_types(requests: list[Any]) -> list[type]:
    return [type(request) for request in requests]


def test_success_flow_sends_accepted_processing_document_and_done(tmp_path: Path) -> None:
    result = make_result(tmp_path)
    service = FakeSummaryService(result=result)
    file_existed_at_send: list[bool] = []

    def on_request(method: Any) -> None:
        if isinstance(method, SendDocument):
            file_existed_at_send.append(Path(method.document.path).is_file())

    requests = run_message(service, YOUTUBE_URL, on_request=on_request)

    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]
    assert requests[0].text == MESSAGE_ACCEPTED
    assert "принял" in requests[0].text.lower()
    assert requests[1].text == MESSAGE_PROCESSING
    assert "обрабатываю" in requests[1].text.lower()

    document = requests[2].document
    assert isinstance(document, FSInputFile)
    assert document.filename == result.filename
    assert Path(document.path) == result.path
    assert file_existed_at_send == [True], "файл должен существовать до отправки"

    assert requests[3].text == MESSAGE_DONE
    assert "готово" in requests[3].text.lower()
    assert service.calls == [YOUTUBE_URL]


@pytest.mark.parametrize("text", ["привет", "посоветуй фильм про космос", "как дела?"])
def test_text_without_link_asks_for_youtube_link(text: str) -> None:
    service = FakeSummaryService()

    requests = run_message(service, text)

    assert request_types(requests) == [SendMessage]
    assert requests[0].text == MESSAGE_NEED_LINK
    assert "YouTube" in requests[0].text
    assert service.calls == []


def test_first_link_in_text_is_processed(tmp_path: Path) -> None:
    service = FakeSummaryService(result=make_result(tmp_path))
    text = "Смотри https://youtu.be/dQw4w9WgXcQ, а потом https://youtu.be/aaaaaaaaaaa."

    requests = run_message(service, text)

    assert service.calls == ["https://youtu.be/dQw4w9WgXcQ"]
    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]


def test_non_youtube_link_reaches_service_for_adapter_validation() -> None:
    """Обработчик не фильтрует домены: не-YouTube отвергает адаптер (FR-2)."""
    service = FakeSummaryService(error=NotYouTubeURLError())

    requests = run_message(service, "Вот https://example.com/page — посмотри")

    assert service.calls == ["https://example.com/page"]
    assert isinstance(requests[-1], SendMessage)


def test_processing_runs_in_a_worker_thread(tmp_path: Path) -> None:
    service = FakeSummaryService(result=make_result(tmp_path))
    loop_thread: list[int] = []

    async def scenario() -> None:
        loop_thread.append(threading.get_ident())
        harness = make_harness(service)
        try:
            await harness.send(YOUTUBE_URL)
            await harness.handler.wait_idle()
        finally:
            await harness.close()

    asyncio.run(scenario())

    assert service.thread_ids, "сервис не был вызван"
    assert all(thread_id != loop_thread[0] for thread_id in service.thread_ids), (
        "process должен выполняться вне потока event loop (asyncio.to_thread)"
    )


def test_event_loop_is_not_blocked_while_video_is_processed(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    service = FakeSummaryService(
        result=make_result(tmp_path),
        started=started,
        release=release,
    )

    async def scenario() -> list[Any]:
        harness = make_harness(service)
        try:
            # Обработчик обязан вернуться сразу, не дожидаясь сервиса.
            started_at = time.monotonic()
            await asyncio.wait_for(harness.send(YOUTUBE_URL), timeout=1.0)
            assert time.monotonic() - started_at < 1.0
            assert not release.is_set()

            # Ждём старта сервиса в рабочем потоке; сам цикл ожидания
            # доказывает, что event loop жив, пока поток заблокирован.
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)

            assert started.is_set(), "обработка не стартовала в рабочем потоке"
            assert not release.is_set(), "сервис не должен завершиться до release"
            assert request_types(harness.session.requests) == [SendMessage, SendMessage]
        finally:
            release.set()
            await harness.handler.wait_idle()
            await harness.close()
        return harness.session.requests

    requests = asyncio.run(scenario())

    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]


def test_service_error_sends_generic_failure() -> None:
    service = FakeSummaryService(error=RuntimeError("boom"))

    requests = run_message(service, YOUTUBE_URL)

    assert request_types(requests) == [SendMessage, SendMessage, SendMessage]
    assert requests[-1].text == MESSAGE_FAILED
    assert service.calls == [YOUTUBE_URL]


@pytest.mark.parametrize("with_path", [False, True], ids=["path-none", "file-missing"])
def test_missing_saved_file_is_reported(with_path: bool, tmp_path: Path) -> None:
    service = FakeSummaryService(
        result=SummaryResult(
            note=Note(title="Без файла"),
            markdown="# Без файла\n",
            filename="bez-fayla.md",
            transcript=TranscriptText(),
            path=(tmp_path / "missing.md") if with_path else None,
        )
    )

    requests = run_message(service, YOUTUBE_URL)

    assert request_types(requests) == [SendMessage, SendMessage, SendMessage]
    assert requests[-1].text == MESSAGE_NO_FILE


def test_create_router_registers_link_handler() -> None:
    router = create_router(FakeSummaryService())

    assert router.name == "tldw"
    assert len(router.message.handlers) == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Смотри https://youtu.be/dQw4w9WgXcQ.", "https://youtu.be/dQw4w9WgXcQ"),
        ("youtu.be/dQw4w9WgXcQ", "youtu.be/dQw4w9WgXcQ"),
        ("youtube.com/watch?v=dQw4w9WgXcQ", "youtube.com/watch?v=dQw4w9WgXcQ"),
        (
            "https://youtube.com/shorts/dQw4w9WgXcQ?feature=share",
            "https://youtube.com/shorts/dQw4w9WgXcQ?feature=share",
        ),
        ("https://example.com https://youtu.be/bbb", "https://example.com"),
        ("<https://youtu.be/abc>", "https://youtu.be/abc"),
        ("Моя почта user@example.com", None),
        ("никакой ссылки", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_first_url(text: str | None, expected: str | None) -> None:
    assert extract_first_url(text) == expected


def make_video() -> Video:
    return Video(
        url=YOUTUBE_URL,
        metadata=VideoMetadata(
            video_id=VIDEO_ID,
            title="Как учиться",
            channel="Канал автора",
            duration_seconds=600,
        ),
        transcript=Transcript(
            segments=(TranscriptSegment("Вступление", 0),),
            language="ru",
            source="test",
        ),
    )


def make_summary() -> VideoSummary:
    return VideoSummary(
        tldr=("Первое предложение.", "Второе предложение.", "Третье предложение."),
        key_ideas=tuple(f"Идея номер {index}" for index in range(1, 6)),
        timeline=(SummaryTimelineEntry("00:00", "Вступление"),),
        quotes=(
            SummaryQuote("Первая цитата.", "00:01"),
            SummaryQuote("Вторая цитата.", "00:02"),
        ),
        follow_ups=("Проверить пример из видео.",),
    )


@dataclass
class FakeVideoSource:
    video: Video

    def fetch(self, url: str) -> Video:
        return self.video


@dataclass
class FakeSummarizer:
    summary: VideoSummary

    def summarize(self, transcript: str, *, title: str = "", channel: str = "") -> VideoSummary:
        return self.summary


def test_note_is_saved_in_data_dir_before_sending(fake_env: dict[str, str]) -> None:
    settings = load_settings(fake_env)
    service = SummaryService(
        FakeVideoSource(make_video()),
        FakeSummarizer(make_summary()),
        data_dir=settings.data_dir,
    )
    file_existed_at_send: list[bool] = []

    def on_request(method: Any) -> None:
        if isinstance(method, SendDocument):
            file_existed_at_send.append(Path(method.document.path).is_file())

    requests = run_message(service, YOUTUBE_URL, on_request=on_request)

    expected_path = settings.data_dir / note_filename(Note(title="Как учиться"))
    assert expected_path.is_file()
    assert expected_path.parent == settings.data_dir

    document = next(request for request in requests if isinstance(request, SendDocument))
    assert Path(document.document.path) == expected_path
    assert document.document.filename == expected_path.name
    assert "## TL;DR" in expected_path.read_text(encoding="utf-8")
    assert file_existed_at_send == [True]

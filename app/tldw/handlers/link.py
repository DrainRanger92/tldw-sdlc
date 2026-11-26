"""Обработчик сообщения со ссылкой: «принял → обрабатываю → готово».

Сценарий T-006:

1. сообщение со ссылкой сразу получает ответ «принял» — до любых тяжёлых
   вызовов, чтобы пользователь видел, что бот не потерял запрос;
2. обработка уходит в фоновую задачу :meth:`LinkHandler._process`, поэтому
   обработчик возвращает управление диспетчеру и event loop не ждёт YouTube
   и LLM;
3. в фоне сначала отправляется «обрабатываю…», затем синхронный
   :meth:`tldw.services.summary.SummaryService.process` уводится в рабочий
   поток через :func:`asyncio.to_thread` (адаптеры и сервис синхронные);
4. готовый ``.md`` уже сохранён сервисом через слой ``storage`` в
   ``Settings.data_dir`` — обработчик лишь проверяет файл и отправляет его
   документом с именем из шаблона заметки, после чего пишет «готово».

Сценарий T-007: первым делом проверяется доступ по allowlist
(:class:`tldw.access.AccessPolicy`). Посторонний получает «доступ закрыт», и
обработка не начинается — ни разбора ссылки, ни вызовов YouTube/LLM.

Текст без ссылки не запускает обработку: бот подсказывает нужный формат.
Если ссылок несколько, обрабатывается первая — включая не-YouTube ссылку:
её отвергает адаптер, а понятное сообщение об ошибке появится в T-008.
Пока обработчик показывает общее сообщение о сбое и пишет причину в лог.

Обработчик остаётся тонким (ADR-002): проверка доступа, разбор текста, ответы
пользователю и передача ссылки в сервис — без бизнес-логики и без обращений
к сети.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, Final, Protocol

from aiogram import F, Router
from aiogram.types import FSInputFile, Message

from tldw.access import AccessPolicy
from tldw.services.summary import SummaryResult

log = logging.getLogger(__name__)

MESSAGE_ACCESS_DENIED: Final = (
    "⛔ Доступ закрыт. Этот бот работает по списку разрешённых пользователей."
)
MESSAGE_ACCEPTED: Final = "✅ Принял ссылку."
MESSAGE_PROCESSING: Final = "⏳ Обрабатываю видео…"
MESSAGE_DONE: Final = "✅ Готово."
MESSAGE_NEED_LINK: Final = (
    "Пришлите ссылку на видео YouTube — например, https://youtu.be/XXXXXXXXXXX. "
    "Я сделаю из него заметку для Obsidian."
)
MESSAGE_FAILED: Final = "Не удалось обработать видео. Попробуйте позже."
MESSAGE_NO_FILE: Final = "Заметка не сохранилась. Попробуйте ещё раз."

#: Ссылка со схемой или ``www.`` — любой домен; без схемы распознаются только
#: домены YouTube, чтобы упоминание почты или файла не считалось ссылкой.
_LINK_RE: Final = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'«»]+"
    r"|(?:[a-z0-9-]+\.)*(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/[^\s<>\"'«»]+",
    re.IGNORECASE,
)
#: Пунктуация, которая в обычном тексте прилипает к ссылке с конца.
_TRAILING_PUNCTUATION: Final = ".,;:!?…»\"')]}"


class SummaryProcessor(Protocol):
    """Минимальный интерфейс сервиса конспекта, нужный обработчику."""

    def process(self, url: str) -> SummaryResult:
        """Обработать ссылку и вернуть сохранённую заметку."""


def extract_first_url(text: str | None) -> str | None:
    """Вернуть первую ссылку из текста сообщения или ``None``.

    Поддерживаются ссылки со схемой ``http(s)`` (и ``www.``) на любой домен,
    а также ссылки YouTube без схемы: ``youtube.com/watch?v=…``,
    ``youtu.be/…``, ``youtube.com/shorts/…``. Хвостовая пунктуация, которая
    прилипает к ссылке в предложении («Смотри https://youtu.be/… .»),
    отбрасывается; при нескольких ссылках возвращается первая по тексту.
    """
    if not text:
        return None
    for match in _LINK_RE.finditer(text):
        url = match.group(0).rstrip(_TRAILING_PUNCTUATION)
        if url:
            return url
    return None


class LinkHandler:
    """Telegram-обработчик текстовых сообщений со ссылкой.

    :param service: сервис конспекта (узкий протокол :class:`SummaryProcessor`).
    :param allowed_user_ids: allowlist из конфига; пустой список — доступ всем.

    Фоновые задачи учитываются в :attr:`_tasks`: так :meth:`wait_idle` может
    дождаться их завершения (тесты, будущая остановка бота), а event loop не
    собирает задачу как мусор до её завершения.
    """

    def __init__(
        self,
        service: SummaryProcessor,
        *,
        allowed_user_ids: tuple[int, ...] = (),
    ) -> None:
        self._service = service
        self._access = AccessPolicy(allowed_user_ids)
        self._tasks: set[asyncio.Task[None]] = set()

    def register(self, router: Router) -> None:
        """Подключить обработчик к роутеру для всех текстовых сообщений."""
        router.message.register(self.handle_message, F.text)

    async def handle_message(self, message: Message) -> None:
        """Проверить доступ, ответить «принял» и запустить обработку ссылки."""
        user_id = _message_user_id(message)
        if not self._access.is_allowed(user_id):
            _log_denied(user_id)
            await message.answer(MESSAGE_ACCESS_DENIED)
            return

        url = extract_first_url(message.text)
        if url is None:
            await message.answer(MESSAGE_NEED_LINK)
            return

        await message.answer(MESSAGE_ACCEPTED)
        self._start(self._process(message, url))

    async def wait_idle(self) -> None:
        """Дождаться завершения всех запущенных фоновых обработок."""
        while self._tasks:
            pending = tuple(self._tasks)
            await asyncio.gather(*pending, return_exceptions=True)
            self._tasks.difference_update(pending)

    def _start(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _process(self, message: Message, url: str) -> None:
        try:
            await message.answer(MESSAGE_PROCESSING)
            result = await asyncio.to_thread(self._service.process, url)
            document = _saved_document(result)
            if document is None:
                await message.answer(MESSAGE_NO_FILE)
                return
            await message.answer_document(document)
            await message.answer(MESSAGE_DONE)
            log.info("Заметка отправлена: %s", result.filename)
        except Exception:  # noqa: BLE001 - обработчик не должен падать молча
            # T-008 заменит общее сообщение разбором типизированных ошибок.
            log.exception("Не удалось обработать ссылку %s", url)
            try:
                await message.answer(MESSAGE_FAILED)
            except Exception:  # noqa: BLE001 - сбой Telegram не должен всплывать из задачи
                log.exception("Не удалось отправить сообщение об ошибке")


def _message_user_id(message: Message) -> int | None:
    """ID отправителя сообщения; ``None``, если отправителя нет (канал)."""
    user = message.from_user
    return user.id if user is not None else None


def _log_denied(user_id: int | None) -> None:
    """Записать отказ в лог, не раскрывая текст сообщения и ссылку."""
    if user_id is None:
        log.warning("Доступ закрыт: у сообщения нет отправителя, allowlist задан")
    else:
        log.warning("Доступ закрыт: user ID %s нет в TELEGRAM_ALLOWED_USER_IDS", user_id)


def _saved_document(result: SummaryResult) -> FSInputFile | None:
    """Отдать сохранённый ``.md`` на отправку; ``None``, если файла нет."""
    path = result.path
    if path is None:
        log.error("Сервис не сохранил заметку %s: путь не задан", result.filename)
        return None
    note_path = Path(path)
    if not note_path.is_file():
        log.error("Файл заметки не найден: %s", note_path)
        return None
    return FSInputFile(note_path, filename=result.filename)


__all__ = [
    "MESSAGE_ACCESS_DENIED",
    "MESSAGE_ACCEPTED",
    "MESSAGE_DONE",
    "MESSAGE_FAILED",
    "MESSAGE_NEED_LINK",
    "MESSAGE_NO_FILE",
    "MESSAGE_PROCESSING",
    "LinkHandler",
    "SummaryProcessor",
    "extract_first_url",
]

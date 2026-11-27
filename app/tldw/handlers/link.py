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

Активный шаблон пользователя (T-008 ``specs/002-user-note-templates``).
После allowlist и до тяжёлых вызовов обработчик снимает активный шаблон
отправителя (:meth:`tldw.services.template.TemplateService.get_active`) и
передаёт снимок в :meth:`tldw.services.summary.SummaryService.process`.
Снимок берётся один раз
на обработку, поэтому смена шаблона во время генерации не влияет на результат,
а шаблон одного пользователя никогда не попадает в заметку другого: ключ —
``from_user.id`` из самого сообщения.

* шаблона нет (или он встроенный) — в сервис уходит ``None``, и путь остаётся
  прежним MVP: те же сообщения, тот же промпт, тот же ``.md``;
* ошибка чтения хранилища или повреждённый файл не теряют ссылку: пишется
  ``WARNING`` и обработка продолжается по встроенному шаблону.

``template_service`` необязателен: старый конструктор
``LinkHandler(service, allowed_user_ids=…)`` работает как раньше.

Текст без ссылки не запускает обработку: бот подсказывает нужный формат.
Если ссылок несколько, обрабатывается первая — включая не-YouTube ссылку:
её отвергает адаптер.

Ошибки (T-008 ``specs/001-mvp``). Сбой обработки разбирает
:func:`tldw.errors.log_failure`: известные типизированные ошибки (не YouTube,
нет субтитров, слишком длинное
видео, AI не ответил) дают пользователю своё понятное сообщение и запись
причины в лог на уровне ``WARNING``; неизвестный сбой — общее сообщение и
``ERROR`` со стектрейсом, очищенным от секретов и длинных фрагментов
транскрипта. Ответ об ошибке не содержит стектрейса.

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

from tldw.access import AccessPolicy, DenialLogLimiter, warn_if_allowlist_missing
from tldw.errors import log_failure
from tldw.services.summary import SummaryResult
from tldw.services.template import ActiveTemplate, TemplateService
from tldw.text import safe_url_host, video_id_from_url

log = logging.getLogger(__name__)

MESSAGE_ACCESS_DENIED: Final = (
    "⛔ Доступ закрыт. Этот бот работает по списку разрешённых пользователей."
)
MESSAGE_ACCEPTED: Final = "✅ Принял ссылку."
MESSAGE_PROCESSING: Final = "⏳ Обрабатываю видео…"
MESSAGE_DONE: Final = "✅ Готово."
MESSAGE_ALREADY_PROCESSING: Final = (
    "⏳ Эта ссылка уже обрабатывается. Дождитесь готовой заметки."
)
MESSAGE_NEED_LINK: Final = (
    "Пришлите ссылку на видео YouTube — например, https://youtu.be/XXXXXXXXXXX. "
    "Я сделаю из него заметку для Obsidian."
)
MESSAGE_NO_FILE: Final = "Заметка не сохранилась. Попробуйте ещё раз."
#: Сколько ссылок обрабатывается одновременно по умолчанию. Семафор ограничивает
#: только вызов сервиса (YouTube + LLM): очередь ожидающих задач не ограничена,
#: и каждая задача ещё до слота отвечает «Принял»/«Обрабатываю». Лимит осознанно
#: не вынесен в ``Settings``: это защита процесса, а не настройка пользователя;
#: перевод в env — кандидат в T-017 (REVIEW-2 T-016). Повторная обработка того
#: же видео в том же чате не дублируется отдельно (см. ``_inflight``).
MAX_CONCURRENT_LINKS: Final = 2

#: Ключ дедупликации: чат, отправитель и ``video_id`` (или URL).
#: Чат входит в ключ, потому что заметка уходит в чат сообщения: та же ссылка,
#: отправленная тем же пользователем в другой чат или в другой канал (где
#: ``sender=None``), — это другая доставка, и ответ «уже обрабатывается» был бы
#: ложным (REVIEW-2 T-016).
_DedupeKey = tuple[int | None, int | None, str]

#: Ссылка со схемой или ``www.`` — любой домен; без схемы распознаются только
#: домены YouTube, чтобы упоминание почты или файла не считалось ссылкой.
#: Левая граница не даёт совпасть внутри ``notyoutube.com``, ``not_youtube.com``
#: или ``текстyoutube.com``: без неё шаблон выхватывал ``youtube.com/watch?v=x``
#: из чужого домена (P3 T-006/T-016). ``\w`` учитывает ``_`` и не-ASCII буквы.
_LINK_RE: Final = re.compile(
    r"(?<![\w.-])(?:https?://|www\.)[^\s<>\"'«»]+"
    r"|(?<![\w.-])(?:[a-z0-9-]+\.)*(?:youtube\.com|youtu\.be|youtube-nocookie\.com)"
    r"/[^\s<>\"'«»]+",
    re.IGNORECASE,
)
#: Пунктуация, которая в обычном тексте прилипает к ссылке с конца.
_TRAILING_PUNCTUATION: Final = ".,;:!?…»\"')]}"


class SummaryProcessor(Protocol):
    """Минимальный интерфейс сервиса конспекта, нужный обработчику."""

    def process(
        self,
        url: str,
        *,
        template: ActiveTemplate | None = None,
    ) -> SummaryResult:
        """Обработать ссылку и вернуть сохранённую заметку.

        ``template`` — снимок активного шаблона отправителя или ``None`` для
        прежнего встроенного пути MVP.
        """


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
    :param allowed_user_ids: allowlist из конфига; пустой кортеж — доступ всем,
        ``None`` (аргумент забыт) — тоже доступ всем, но с предупреждением в
        лог, чтобы fail-open не был молчаливым (T-016).
    :param template_service: сервис шаблонов; ``None`` (по умолчанию) — прежнее
        поведение MVP без пользовательских шаблонов.
    :param max_concurrent: сколько ссылок обрабатывается одновременно.
        Повторная ссылка того же пользователя в том же чате, пока первая в
        полёте, не запускает вторую обработку: LLM не вызывается дважды
        (T-016). Семафор ограничивает только вызов сервиса: очередь ожидающих
        задач не ограничена, и каждая задача ещё до слота отвечает
        «Принял»/«Обрабатываю» (осознанно, см. ``MAX_CONCURRENT_LINKS``).

    Фоновые задачи учитываются в :attr:`_tasks`: так :meth:`wait_idle` может
    дождаться их завершения (тесты, будущая остановка бота), а event loop не
    собирает задачу как мусор до её завершения. :meth:`aclose` отменяет и
    дожидается их при остановке приложения, поэтому «готово» не уходит в уже
    закрытую сессию Telegram.
    """

    def __init__(
        self,
        service: SummaryProcessor,
        *,
        allowed_user_ids: tuple[int, ...] | None = None,
        template_service: TemplateService | None = None,
        max_concurrent: int = MAX_CONCURRENT_LINKS,
    ) -> None:
        if (
            isinstance(max_concurrent, bool)
            or not isinstance(max_concurrent, int)
            or max_concurrent < 1
        ):
            raise ValueError("max_concurrent должен быть положительным целым")
        self._service = service
        if allowed_user_ids is None:
            # Явный fail-open: аргумент забыт, а не осознанно пуст.
            warn_if_allowlist_missing()
            self._access = AccessPolicy.open()
        else:
            self._access = AccessPolicy(allowed_user_ids)
        self._templates = template_service
        self._max_concurrent = max_concurrent
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._inflight: dict[_DedupeKey, asyncio.Task[None]] = {}
        self._denials = DenialLogLimiter()
        self._closed = False

    def register(self, router: Router) -> None:
        """Подключить обработчик к роутеру для всех текстовых сообщений."""
        router.message.register(self.handle_message, F.text)

    async def handle_message(self, message: Message) -> None:
        """Проверить доступ, зарезервировать слот и запустить обработку ссылки."""
        if self._closed:
            # Приложение уже останавливается: не начинаем новую обработку,
            # чтобы не писать в закрытую сессию Telegram.
            return
        user_id = _message_user_id(message)
        if not self._access.is_allowed(user_id):
            self._log_denied(user_id)
            await self._answer(message, MESSAGE_ACCESS_DENIED)
            return

        url = extract_first_url(message.text)
        if url is None:
            await self._answer(message, MESSAGE_NEED_LINK)
            return

        # Один и тот же ролик с разными query-параметрами — та же работа:
        # ключ дедупликации берёт безопасный video_id, если он есть. Чат в
        # ключе: та же ссылка в другом чате — другая доставка заметки.
        key = (_message_chat_id(message), user_id, video_id_from_url(url) or url)
        if self._is_inflight(key):
            log.debug("Ссылка уже обрабатывается, повторная задача не создаётся")
            await self._answer(message, MESSAGE_ALREADY_PROCESSING)
            return

        # Слот резервируется синхронно, до первого ``await``: иначе два
        # конкурентных обработчика одного пользователя оба проходят проверку
        # выше, второй перезаписывает ``_inflight`` и LLM вызывается дважды
        # (aiogram обрабатывает апдейты конкурентно). Поэтому «принял» и
        # снимок шаблона — уже внутри фоновой задачи.
        self._start(key, self._process(message, url, user_id))

    async def wait_idle(self) -> None:
        """Дождаться завершения всех запущенных фоновых обработок."""
        while self._tasks:
            pending = tuple(self._tasks)
            await asyncio.gather(*pending, return_exceptions=True)
            self._tasks.difference_update(pending)

    async def aclose(self) -> None:
        """Отменить и дождаться фоновых задач при остановке приложения.

        Вызывается :meth:`tldw.bootstrap.Application.close` **до** закрытия
        сессии Telegram: отменённая задача не отправит «готово» в закрытую
        сессию. ``asyncio.to_thread`` не прерывает уже запущенный поток, но его
        результат больше никому не отправляется. Повторный вызов безопасен.
        """
        self._closed = True
        pending = tuple(self._tasks)
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        self._inflight.clear()

    def _start(
        self,
        key: _DedupeKey,
        coro: Coroutine[Any, Any, None],
    ) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        self._inflight[key] = task

        def _done(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
            if self._inflight.get(key) is done:
                self._inflight.pop(key, None)

        task.add_done_callback(_done)

    def _limiter(self) -> asyncio.Semaphore:
        """Семафор параллелизма, привязанный к текущему event loop."""
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._semaphore_loop is not loop:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
            self._semaphore_loop = loop
        return self._semaphore

    def _is_inflight(self, key: _DedupeKey) -> bool:
        """Обрабатывается ли уже эта ссылка этим пользователем в этом чате."""
        pending = self._inflight.get(key)
        return pending is not None and not pending.done()

    async def _active_template(self, user_id: int | None) -> ActiveTemplate | None:
        """Снять активный шаблон отправителя после проверки доступа (T-008).

        Чтение файла шаблона — блокирующий ввод-вывод, поэтому уходит в
        рабочий поток (:func:`asyncio.to_thread`): event loop остаётся живым,
        пока хранилище читает файл (T-016).

        Возвращает ``None`` для встроенного шаблона и для пользователя без
        своего шаблона: сервис конспекта тогда идёт прежним путём MVP (тот же
        промпт, та же заметка). Без отправителя (канал) шаблон брать не у кого.

        Сбой чтения не теряет ссылку (FR-3): повреждённый файл хранилище уже
        превращает в ``None`` с ``WARNING``, а неожиданную ошибку гасим здесь —
        пользователь получает заметку по встроенному шаблону и предупреждение
        в логе. Текст шаблона и ссылка в лог не попадают.
        """
        if self._templates is None or user_id is None:
            return None
        try:
            active = await asyncio.to_thread(self._templates.get_active, user_id)
        except Exception as exc:  # noqa: BLE001 - сбой шаблона не должен терять ссылку
            log.warning(
                "Шаблон: не удалось прочитать активный шаблон user ID %s (%s), "
                "используется встроенный",
                user_id,
                type(exc).__name__,
            )
            return None
        if active.is_builtin:
            return None
        return active

    async def _process(
        self,
        message: Message,
        url: str,
        user_id: int | None,
    ) -> None:
        if self._closed:
            return
        try:
            await self._answer(message, MESSAGE_ACCEPTED)
            # Снимок шаблона — после allowlist и до тяжёлых вызовов: в
            # обработку уходит неизменяемое значение, поэтому смена шаблона во
            # время генерации не влияет на результат (FR-7). Чтение файла — в
            # потоке; повторная ссылка уже не создаёт вторую задачу, потому что
            # слот занят с начала этой корутины.
            template = await self._active_template(user_id)
            if self._closed:
                return
            await self._answer(message, MESSAGE_PROCESSING)
            async with self._limiter():
                result = await asyncio.to_thread(self._service.process, url, template=template)
            # Проверка файла — синхронный ввод-вывод, тоже вне event loop.
            document = await asyncio.to_thread(_saved_document, result)
            if document is None:
                await self._answer(message, MESSAGE_NO_FILE)
                return
            await message.answer_document(document)
            await self._answer(message, MESSAGE_DONE)
            log.info(
                "Заметка отправлена: %s (video_id: %s)",
                result.filename,
                video_id_from_url(url) or "—",
            )
        except Exception as exc:  # noqa: BLE001 - обработчик не должен падать молча
            # Типизированные ошибки получают своё сообщение, неизвестные —
            # общее; секреты и транскрипт в лог не попадают (T-008). Логгер
            # обработчика передаётся явно, чтобы источник записи был виден.
            # В контексте — безопасный video_id или host, без query и полного URL.
            report = log_failure(exc, logger=log, context=_failure_context(url))
            try:
                await message.answer(report.user_message)
            except Exception as reply_exc:  # noqa: BLE001 - сбой Telegram не должен всплывать
                # Причину всё равно пишем в лог, но без токена бота из URL.
                log_failure(reply_exc, logger=log)

    async def _answer(self, message: Message, text: str) -> None:
        """Ответить пользователю; сбой Telegram не должен ронять обработчик."""
        try:
            await message.answer(text)
        except Exception as exc:  # noqa: BLE001 - ответ не должен всплывать в aiogram
            log_failure(exc, logger=log, context="ответа пользователю")

    def _log_denied(self, user_id: int | None) -> None:
        """Записать отказ: первый раз ``WARNING``, повторы — ``DEBUG``."""
        if user_id is None:
            message = "Доступ закрыт: у сообщения нет отправителя, allowlist задан"
            args: tuple[object, ...] = ()
        else:
            message = "Доступ закрыт: user ID %s нет в TELEGRAM_ALLOWED_USER_IDS"
            args = (user_id,)
        self._denials.log_denial(log, message, *args, user_id=user_id)


def _message_user_id(message: Message) -> int | None:
    """ID отправителя сообщения; ``None``, если отправителя нет (канал)."""
    user = message.from_user
    return user.id if user is not None else None


def _message_chat_id(message: Message) -> int | None:
    """ID чата сообщения: заметка уходит именно туда, а не автору в личку."""
    chat = message.chat
    return chat.id if chat is not None else None


def _failure_context(url: str) -> str:
    """Контекст ошибки для лога: безопасный video_id или host, без query.

    Полный URL в лог не попадает: query-строка произвольного домена может
    содержать токен или персональные данные (P3 T-006). Если id/host выделить
    не удалось, остаётся общий текст — запись всё равно безопасна.
    """
    video_id = video_id_from_url(url)
    if video_id is not None:
        return f"обработки видео (video_id: {video_id})"
    host = safe_url_host(url)
    if host is not None:
        return f"обработки видео (host: {host})"
    return "обработки видео"


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
    "MAX_CONCURRENT_LINKS",
    "MESSAGE_ACCESS_DENIED",
    "MESSAGE_ACCEPTED",
    "MESSAGE_ALREADY_PROCESSING",
    "MESSAGE_DONE",
    "MESSAGE_NEED_LINK",
    "MESSAGE_NO_FILE",
    "MESSAGE_PROCESSING",
    "LinkHandler",
    "SummaryProcessor",
    "extract_first_url",
]

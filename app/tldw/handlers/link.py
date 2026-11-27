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

from tldw.access import AccessPolicy
from tldw.errors import log_failure
from tldw.services.summary import SummaryResult
from tldw.services.template import ActiveTemplate, TemplateService

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
    :param allowed_user_ids: allowlist из конфига; пустой список — доступ всем.
    :param template_service: сервис шаблонов; ``None`` (по умолчанию) — прежнее
        поведение MVP без пользовательских шаблонов.

    Фоновые задачи учитываются в :attr:`_tasks`: так :meth:`wait_idle` может
    дождаться их завершения (тесты, будущая остановка бота), а event loop не
    собирает задачу как мусор до её завершения.
    """

    def __init__(
        self,
        service: SummaryProcessor,
        *,
        allowed_user_ids: tuple[int, ...] = (),
        template_service: TemplateService | None = None,
    ) -> None:
        self._service = service
        self._access = AccessPolicy(allowed_user_ids)
        self._templates = template_service
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

        # Снимок шаблона — после allowlist и до тяжёлых вызовов: в фоновую
        # задачу уходит неизменяемое значение, поэтому смена шаблона во время
        # генерации не влияет на результат (FR-7).
        template = self._active_template(user_id)
        await message.answer(MESSAGE_ACCEPTED)
        self._start(self._process(message, url, template))

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

    def _active_template(self, user_id: int | None) -> ActiveTemplate | None:
        """Снять активный шаблон отправителя после проверки доступа (T-008).

        Вызывается в :meth:`handle_message` после allowlist и до фоновой
        обработки, поэтому в сервис уходит неизменяемый снимок, а не живой
        шаблон.

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
            active = self._templates.get_active(user_id)
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
        template: ActiveTemplate | None,
    ) -> None:
        try:
            await message.answer(MESSAGE_PROCESSING)
            result = await asyncio.to_thread(self._service.process, url, template=template)
            document = _saved_document(result)
            if document is None:
                await message.answer(MESSAGE_NO_FILE)
                return
            await message.answer_document(document)
            await message.answer(MESSAGE_DONE)
            log.info("Заметка отправлена: %s", result.filename)
        except Exception as exc:  # noqa: BLE001 - обработчик не должен падать молча
            # Типизированные ошибки получают своё сообщение, неизвестные —
            # общее; секреты и транскрипт в лог не попадают (T-008). Логгер
            # обработчика передаётся явно, чтобы источник записи был виден.
            report = log_failure(exc, logger=log)
            try:
                await message.answer(report.user_message)
            except Exception as reply_exc:  # noqa: BLE001 - сбой Telegram не должен всплывать
                # Причину всё равно пишем в лог, но без токена бота из URL.
                log_failure(reply_exc, logger=log)


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
    "MESSAGE_NEED_LINK",
    "MESSAGE_NO_FILE",
    "MESSAGE_PROCESSING",
    "LinkHandler",
    "SummaryProcessor",
    "extract_first_url",
]

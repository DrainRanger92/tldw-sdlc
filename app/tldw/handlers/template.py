"""Telegram-сценарий шаблона заметки: показ, изменение, подтверждение, сброс.

Сценарий T-007 (``specs/002-user-note-templates``, FR-4…FR-6, FR-9, FR-10).

Вход — команда ``/template`` и текстовая кнопка «Шаблон»: обе открывают показ
активного шаблона в любой момент, даже посреди другого шага сценария. Показ
человеческий: «встроенный» либо пронумерованные разделы в порядке применения и
тон (или «тон не задан»). Ни LLM, ни YouTube при показе не вызываются.

Изменение — FSM-сценарий:

1. «Изменить» переводит пользователя в состояние ожидания описания;
2. одно текстовое сообщение длиной 1–4000 символов уходит в разбор
   (:meth:`TemplateParser.parse_template`) через :func:`asyncio.to_thread`,
   поэтому HTTP-вызов LLM не блокирует event loop;
3. разобранный кандидат показывается человеческим текстом (пронумерованные
   разделы и тон, без JSON) с действиями «Сохранить», «Исправить», «Отмена»;
4. сохраняет только «Сохранить». «Исправить» отбрасывает кандидата и
   возвращает к вводу, новое описание заменяет кандидата; «Отмена» и «Закрыть»
   закрывают сценарий без изменений.

Второй способ ввести описание — голосовое сообщение (T-012, FR-11). Оно идёт
**тем же** путём, что и текст, но с дополнительными шагами до разбора. Порядок
проверок важен и закреплён ADR-015: allowlist → состояние сценария →
настроена ли граница распознавания → длительность → скачивание аудио →
:func:`asyncio.to_thread` вокруг синхронного распознавания → «разбор → показ
кандидата → Сохранить/Исправить/Отмена». Поэтому посторонний, слишком длинное
аудио и ненастроенный адаптер не тратят ни скачивание, ни распознавание.
Голосовое сообщение вне шага изменения получает подсказку, как войти в
сценарий; без настроенного адаптера бот сообщает, что доступен текстовый ввод.
Распознавание — **одна** попытка на сообщение, без повторов. Ошибка
распознавания не меняет активный шаблон и не создаёт кандидата: пользователь
возвращается к вводу описания. Аудио живёт только в памяти: в ``DATA_DIR`` оно
не сохраняется, а в лог не попадают ни аудио, ни полный текст распознавания —
только длительность, число байт/символов и тип ошибки.

Кандидат живёт только в FSM (``MemoryStorage``): в каталог данных он не попадает
и на генерацию заметок не влияет до явного «Сохранить». Перезапуск бота теряет
кандидата, но подтверждённый шаблон остаётся в хранилище, поэтому активный
шаблон после перезапуска прежний.

«Сбросить» требует явного подтверждения, идемпотентно удаляет пользовательский
шаблон и возвращает встроенный; LLM при этом не вызывается и текст не
запрашивается.

Доступ по allowlist (:class:`tldw.access.AccessPolicy`) проверяется первой
строкой каждого обработчика — и для сообщений, и для callback, — поэтому
посторонний не видит и не меняет шаблон. Пока пользователь ждёт ввода описания,
сообщение со ссылкой не запускает обработку видео: обработчик шаблона
регистрируется в роутере **раньше** обработчика ссылок
(:func:`tldw.handlers.router.create_router`) и просит закончить описание или
отменить его, чтобы не тратить квоту YouTube/LLM.

Слой остаётся тонким (ADR-002): обработчик проверяет доступ, ведёт диалог,
показывает шаблон и вызывает :class:`tldw.services.template.TemplateService`.
Разбор описания приходит через узкий протокол :class:`TemplateParser`, а
распознавание речи — через :class:`SpeechTranscriber`, поэтому Telegram-слой не
импортирует ``httpx`` и адаптеры. Ошибки разбора, распознавания, валидации и
записи — :class:`tldw.errors.UserFacingError`; обработчик отдаёт пользователю их
текст, а причину пишет в лог через :func:`tldw.errors.log_failure` с контекстом
«разбора/распознавания/сохранения/сброса шаблона (происхождение, разделов: N)»,
поэтому запись говорит о шаблоне, а не о видео, и не раскрывает описание, тон,
аудио, распознанный текст, JSON кандидата и секреты. Известный сбой оставляет
прежний активный шаблон и не создаёт кандидата; неизвестный даёт общее
сообщение о шаблоне (:data:`tldw.errors.MESSAGE_UNEXPECTED_TEMPLATE`, а не
текст про видео) и ``ERROR`` со стектрейсом. Ссылка в текстовом или
надиктованном описании отбивается одной и той же подсказкой в общем пути
:meth:`TemplateHandler._present_description`, поэтому до LLM она не доходит.

Заголовки и тон — пользовательские данные, а не разметка: все сообщения и
правки сценария уходят с ``parse_mode=None``, поэтому заголовок с ``<``, ``>``
или ``&`` показывается как есть и не ломает HTML-режим бота.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from io import BytesIO
from typing import Final, Protocol

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from tldw.access import AccessPolicy, DenialLogLimiter, warn_if_allowlist_missing
from tldw.config import MAX_AUDIO_DURATION_SECONDS
from tldw.errors import MESSAGE_AUDIO_TOO_LONG, MESSAGE_UNEXPECTED_TEMPLATE, log_failure
from tldw.handlers.link import MESSAGE_ACCESS_DENIED, extract_first_url
from tldw.services.template import (
    MAX_DESCRIPTION_LENGTH,
    MIN_DESCRIPTION_LENGTH,
    ActiveTemplate,
    NoteTemplate,
    TemplateService,
    TemplateValidationError,
)

log = logging.getLogger(__name__)

#: Текст кнопки основной клавиатуры: обрабатывается как команда ``/template``.
TEMPLATE_BUTTON_TEXT: Final = "Шаблон"

#: Подписи кнопок сценария — по ним же проверяются inline-клавиатуры.
BUTTON_EDIT: Final = "Изменить"
BUTTON_RESET: Final = "Сбросить"
BUTTON_CLOSE: Final = "Закрыть"
BUTTON_SAVE: Final = "Сохранить"
BUTTON_FIX: Final = "Исправить"
BUTTON_CANCEL: Final = "Отмена"
BUTTON_RESET_CONFIRM: Final = "Да, сбросить"

#: Показ активного шаблона.
MESSAGE_VIEW_TITLE: Final = "📋 Шаблон заметки"
MESSAGE_ORIGIN_BUILTIN: Final = "Сейчас действует встроенный шаблон."
MESSAGE_ORIGIN_USER: Final = "Сейчас действует ваш шаблон."
MESSAGE_SECTIONS_TITLE: Final = "Разделы по порядку:"
MESSAGE_TONE_MISSING: Final = "Тон: не задан."
MESSAGE_TONE: Final = "Тон: {tone}"
MESSAGE_VIEW_ACTIONS: Final = "Что сделать с шаблоном?"

#: Ввод описания и показ кандидата.
MESSAGE_ASK_DESCRIPTION: Final = (
    "Опишите обычными словами, какие разделы и в каком порядке нужны заметке и "
    "каким тоном её писать. Например: «Разделы: Коротко, Примеры кода, Что "
    "попробовать. Тон: короче, с акцентом на код». "
    f"Длина описания — от {MIN_DESCRIPTION_LENGTH} до {MAX_DESCRIPTION_LENGTH} символов."
)
MESSAGE_DESCRIPTION_EMPTY: Final = (
    "Описание пустое. Опишите разделы и тон обычными словами одной строкой."
)
MESSAGE_DESCRIPTION_TOO_LONG: Final = (
    "Описание слишком длинное: {length} символов при лимите {limit}. "
    "Сократите его и пришлите снова."
)
MESSAGE_DESCRIPTION_RETRY: Final = (
    "Опишите изменения иначе и пришлите ещё раз или нажмите «Отмена»."
)
MESSAGE_LINK_WHILE_DESCRIPTION: Final = (
    "Сначала закончите описание шаблона или отмените его кнопкой «Отмена» — "
    "видео по ссылке пока не обрабатываю."
)
MESSAGE_LINK_WHILE_CANDIDATE: Final = (
    "Сначала сохраните, исправьте или отмените кандидата шаблона — "
    "видео по ссылке пока не обрабатываю."
)
MESSAGE_PARSING: Final = "⏳ Разбираю описание…"
MESSAGE_CANDIDATE_TITLE: Final = "🤔 Кандидат шаблона. Проверьте, что получилось:"
MESSAGE_CANDIDATE_HINT: Final = "Сохранить этот шаблон?"

#: Голосовой ввод (T-012): подсказки до и после распознавания.
MESSAGE_VOICE_NOT_EXPECTED: Final = (
    "Голосовое описание принимаю только на шаге изменения шаблона: "
    f"откройте /template и нажмите «{BUTTON_EDIT}»."
)
MESSAGE_VOICE_UNAVAILABLE: Final = (
    "Голосовой ввод недоступен: распознавание речи не настроено. "
    "Пришлите описание текстом — сценарий шаблона работает как обычно."
)
MESSAGE_VOICE_DOWNLOAD_FAILED: Final = (
    "Не удалось получить голосовое сообщение. "
    "Запишите его ещё раз или отправьте описание текстом."
)
MESSAGE_VOICE_EMPTY: Final = (
    "Не удалось распознать речь в голосовом сообщении. "
    "Запишите его ещё раз или отправьте описание текстом."
)
MESSAGE_VOICE_RETRY: Final = (
    "Запишите описание ещё раз голосом или отправьте его текстом."
)

#: Итоги действий сценария.
MESSAGE_SAVED: Final = "✅ Шаблон сохранён. Следующие ссылки обработаю по нему."
MESSAGE_CLOSED: Final = "Сценарий закрыт, шаблон не изменён."
MESSAGE_RESET_ASK: Final = "Сбросить пользовательский шаблон и вернуться к встроенному?"
MESSAGE_RESET_DONE: Final = (
    "✅ Пользовательский шаблон удалён. Снова действует встроенный шаблон."
)
MESSAGE_STALE_CANDIDATE: Final = (
    "Кандидат устарел: его уже сохранили или сценарий закрыт. "
    "Шаблон не изменён — откройте /template заново."
)
MESSAGE_STALE_CALLBACK: Final = "Эта кнопка устарела. Откройте /template заново."

#: Префикс callback-данных сценария: ``template:<действие>``.
CALLBACK_PREFIX: Final = "template"
ACTION_EDIT: Final = "edit"
ACTION_RESET: Final = "reset"
ACTION_RESET_CONFIRM: Final = "reset_confirm"
ACTION_CLOSE: Final = "close"
ACTION_SAVE: Final = "save"
ACTION_FIX: Final = "fix"
ACTION_CANCEL: Final = "cancel"

#: Ключ FSM с неподтверждённым кандидатом: примитивы, без модели и без JSON.
_DATA_CANDIDATE: Final = "candidate"


class TemplateAction(CallbackData, prefix=CALLBACK_PREFIX):
    """Callback-данные сценария: только действие, без текста шаблона."""

    action: str


class TemplateStates(StatesGroup):
    """Шаги сценария: ожидание описания, подтверждение кандидата и сброса."""

    waiting_description = State()
    confirming_candidate = State()
    confirming_reset = State()


#: Состояния, в которых принимается описание — текстовое или голосовое.
_DESCRIPTION_STATES: Final = frozenset(
    {
        TemplateStates.waiting_description.state,
        TemplateStates.confirming_candidate.state,
    }
)


class TemplateParseResult(Protocol):
    """Нейтральный результат разбора описания.

    Структурно совпадает с ``tldw.adapters.llm.TemplatePatchDraft``, но
    Telegram-слой не импортирует адаптер: ``None`` означает «не менять эту
    часть», пустая строка тона — «очистить тон».
    """

    @property
    def sections(self) -> Sequence[str] | None:
        """Полный новый список разделов или ``None`` — не менять."""
        ...

    @property
    def tone(self) -> str | None:
        """Новый тон, ``None`` — не менять, пустая строка — очистить."""
        ...


class TemplateParser(Protocol):
    """Узкий интерфейс разбора описания шаблона (его реализует ``LLMClient``)."""

    def parse_template(
        self,
        text: str,
        *,
        current_sections: Sequence[str],
        current_tone: str = "",
    ) -> TemplateParseResult:
        """Разобрать описание в патч относительно текущего активного шаблона."""
        ...


class SpeechTranscriber(Protocol):
    """Узкий интерфейс распознавания речи (его реализует ``SpeechAdapter``).

    ``is_configured`` позволяет предложить текстовый ввод без обращения к сети,
    а синхронный ``transcribe`` вызывается через :func:`asyncio.to_thread`.
    Лимит длительности — общая константа :data:`tldw.config.MAX_AUDIO_DURATION_SECONDS`,
    поэтому обработчику не нужны дополнительные методы адаптера.
    """

    @property
    def is_configured(self) -> bool:
        """Настроена ли граница распознавания (ключ и модель из env)."""
        ...

    def transcribe(self, audio: bytes, *, duration_seconds: float) -> str:
        """Распознать речь одним запросом и вернуть текст."""
        ...


def render_template_text(active: ActiveTemplate) -> str:
    """Показать активный шаблон человеческим текстом, без JSON.

    Встроенный шаблон помечается словом «встроенный», пользовательский — «ваш»;
    разделы нумеруются в порядке применения, тон показывается как есть или
    строкой :data:`MESSAGE_TONE_MISSING`. Функция чистая: LLM и YouTube не
    вызываются.
    """
    lines = [
        MESSAGE_VIEW_TITLE,
        "",
        MESSAGE_ORIGIN_BUILTIN if active.is_builtin else MESSAGE_ORIGIN_USER,
        "",
        MESSAGE_SECTIONS_TITLE,
        *_numbered_sections(active.sections),
        "",
        render_tone(active.tone),
    ]
    return "\n".join(lines)


def render_candidate_text(sections: Sequence[str], tone: str) -> str:
    """Показать кандидата так же, как активный шаблон: номера, разделы и тон.

    Кандидат показывается без JSON и без служебных полей — только то, что
    увидит пользователь в заметке.
    """
    lines = [
        MESSAGE_CANDIDATE_TITLE,
        "",
        MESSAGE_SECTIONS_TITLE,
        *_numbered_sections(sections),
        "",
        render_tone(tone),
        "",
        MESSAGE_CANDIDATE_HINT,
    ]
    return "\n".join(lines)


def render_tone(tone: str) -> str:
    """Строка тона: пустой тон — «не задан»."""
    return MESSAGE_TONE_MISSING if not tone else MESSAGE_TONE.format(tone=tone)


def main_keyboard() -> ReplyKeyboardMarkup:
    """Текстовая клавиатура с кнопкой «Шаблон» — вход в сценарий в любой момент."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=TEMPLATE_BUTTON_TEXT)]],
        resize_keyboard=True,
    )


def empty_keyboard() -> InlineKeyboardMarkup:
    """Пустая inline-клавиатура: убирает кнопки у отработавшего сообщения.

    ``edit_text`` с ``reply_markup=None`` оставил бы клавиатуру на месте, поэтому
    терминальные шаги сценария присылают явно пустую клавиатуру. Текстовую
    клавиатуру с «Шаблоном» нельзя прикрепить к редактируемому сообщению (это
    разрешено только для нового), поэтому она приходит с показом шаблона и
    остаётся в чате.
    """
    return InlineKeyboardMarkup(inline_keyboard=[])


def view_keyboard() -> InlineKeyboardMarkup:
    """Действия просмотра: «Изменить», «Сбросить», «Закрыть»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(BUTTON_EDIT, ACTION_EDIT),
                _button(BUTTON_RESET, ACTION_RESET),
                _button(BUTTON_CLOSE, ACTION_CLOSE),
            ]
        ]
    )


def candidate_keyboard() -> InlineKeyboardMarkup:
    """Действия кандидата: «Сохранить», «Исправить», «Отмена»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(BUTTON_SAVE, ACTION_SAVE),
                _button(BUTTON_FIX, ACTION_FIX),
                _button(BUTTON_CANCEL, ACTION_CANCEL),
            ]
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    """Единственное действие ввода описания — «Отмена»."""
    return InlineKeyboardMarkup(inline_keyboard=[[_button(BUTTON_CANCEL, ACTION_CANCEL)]])


def reset_keyboard() -> InlineKeyboardMarkup:
    """Явное подтверждение сброса: «Да, сбросить» или «Отмена»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(BUTTON_RESET_CONFIRM, ACTION_RESET_CONFIRM),
                _button(BUTTON_CANCEL, ACTION_CANCEL),
            ]
        ]
    )


class TemplateHandler:
    """Telegram-сценарий шаблона заметки поверх :class:`TemplateService`.

    :param templates: сервис шаблонов (чтение активного, сохранение, сброс,
        слияние правки с текущим шаблоном).
    :param parser: разбор описания человеческим языком (реализация — LLM-адаптер).
    :param speech: распознавание речи (реализация — ``SpeechAdapter``);
        ``None`` — голосовой ввод не подключён, бот предлагает текст.
    :param allowed_user_ids: allowlist из конфига; пустой кортеж — доступ всем,
        ``None`` (аргумент забыт) — тоже доступ всем, но с предупреждением в
        лог, чтобы fail-open не был молчаливым (T-016).

    Порядок регистрации важен: команда и кнопка открывают сценарий в любом
    состоянии, затем идёт приём описания и голоса, затем callback. Обработчик
    ссылок подключается после (см. :func:`tldw.handlers.router.create_router`),
    поэтому ссылка во время ввода описания не запускает обработку видео.

    Голос регистрируется без фильтра состояния: так allowlist проверяется
    первой строкой для **любого** голосового сообщения, а не только когда
    пользователь уже в шаге изменения (ADR-015).
    """

    def __init__(
        self,
        templates: TemplateService,
        parser: TemplateParser,
        *,
        speech: SpeechTranscriber | None = None,
        allowed_user_ids: tuple[int, ...] | None = None,
    ) -> None:
        self._templates = templates
        self._parser = parser
        self._speech = speech
        if allowed_user_ids is None:
            warn_if_allowlist_missing()
            self._access = AccessPolicy.open()
        else:
            self._access = AccessPolicy(allowed_user_ids)
        self._denials = DenialLogLimiter()

    def register(self, router: Router) -> None:
        """Подключить обработчики к роутеру в порядке «вход → ввод → callback»."""
        router.message.register(self.handle_open, Command("template"))
        router.message.register(self.handle_open, F.text == TEMPLATE_BUTTON_TEXT)
        router.message.register(
            self.handle_description,
            StateFilter(*_DESCRIPTION_STATES),
            F.text,
        )
        router.message.register(self.handle_voice, F.voice)
        router.callback_query.register(self.handle_callback, TemplateAction.filter())

    async def handle_open(self, message: Message, state: FSMContext) -> None:
        """Открыть показ активного шаблона: ``/template`` или кнопка «Шаблон».

        Незавершённый сценарий закрывается, неподтверждённый кандидат
        отбрасывается: показ всегда говорит только про активный шаблон, а
        сохранить можно лишь явным «Сохранить».

        Показ идёт двумя сообщениями: постоянная текстовая клавиатура с кнопкой
        «Шаблон» и inline-действия — разные клавиатуры, а у одного сообщения
        может быть только одна. Первое сообщение оставляет кнопку «Шаблон» в
        чате, второе — кнопки «Изменить», «Сбросить», «Закрыть».
        """
        user_id = _sender_id(message)
        if user_id is None or not self._access.is_allowed(user_id):
            await self._deny(message, "показ", user_id)
            return

        await state.clear()
        active = await self._active(user_id)
        log.info(
            "Шаблон: показан активный (%s, разделов: %s)",
            _origin_label(active),
            len(active.sections),
        )
        await message.answer(
            render_template_text(active),
            reply_markup=main_keyboard(),
            parse_mode=None,
        )
        await message.answer(
            MESSAGE_VIEW_ACTIONS,
            reply_markup=view_keyboard(),
            parse_mode=None,
        )

    async def handle_description(self, message: Message, state: FSMContext) -> None:
        """Принять одно текстовое описание и показать кандидата.

        Описание принимается и пока показан кандидат: новое сообщение заменяет
        неподтверждённого кандидата, активный шаблон при этом не меняется.

        Сообщение со ссылкой не запускает обработку видео ни в ожидании
        описания, ни при показанном кандидате: пользователь получает просьбу
        закончить/отменить описание или разобраться с кандидатом. Проверка
        ссылки живёт в общем с голосом пути :meth:`_present_description`
        (лимиты, разбор в рабочем потоке, показ кандидата, обработка ошибок),
        поэтому текст и надиктованное описание ведут себя одинаково.
        """
        user_id = _sender_id(message)
        if user_id is None or not self._access.is_allowed(user_id):
            await self._deny(message, "изменение", user_id)
            return

        await self._present_description(
            message,
            state,
            user_id,
            (message.text or "").strip(),
        )

    async def handle_voice(self, message: Message, state: FSMContext) -> None:
        """Принять голосовое описание: проверить, скачать, распознать, показать.

        Порядок закреплён ADR-015 и не случаен:

        1. allowlist — первой строкой: посторонний не тратит скачивание и
           распознавание;
        2. состояние сценария: голос принимается только на шаге изменения,
           иначе пользователь получает подсказку, как войти в сценарий;
        3. ``is_configured`` — до длительности: ненастроенный адаптер говорит
           «доступен текст», а не «слишком длинное аудио»;
        4. длительность — до скачивания: ``voice.duration`` из апдейта
           сверяется с :data:`tldw.config.MAX_AUDIO_DURATION_SECONDS`;
        5. скачивание — в память (``BytesIO``), без записи в ``DATA_DIR``;
        6. распознавание — одна попытка через :func:`asyncio.to_thread`;
        7. дальше общий путь «ссылка → разбор → показ кандидата →
           подтверждение»: надиктованная ссылка отбивается той же подсказкой,
           что и текстовая, и LLM не вызывается.

        Ошибка скачивания или распознавания не меняет активный шаблон: при
        сбое распознавания кандидат и состояние сбрасываются, и пользователь
        возвращается к вводу. Аудио и распознанный текст в лог не попадают.
        """
        user_id = _sender_id(message)
        if user_id is None or not self._access.is_allowed(user_id):
            await self._deny(message, "голосовое описание", user_id)
            return

        current_state = await state.get_state()
        if current_state not in _DESCRIPTION_STATES:
            await message.answer(MESSAGE_VOICE_NOT_EXPECTED, parse_mode=None)
            return

        speech = self._speech
        if speech is None or not speech.is_configured:
            log.info("Шаблон: голосовой ввод недоступен, предложен текст")
            await message.answer(
                MESSAGE_VOICE_UNAVAILABLE,
                reply_markup=cancel_keyboard(),
                parse_mode=None,
            )
            return

        voice = message.voice
        if voice is None:  # pragma: no cover - защита от вызова без F.voice
            await message.answer(
                MESSAGE_VOICE_UNAVAILABLE,
                reply_markup=cancel_keyboard(),
                parse_mode=None,
            )
            return

        if voice.duration > MAX_AUDIO_DURATION_SECONDS:
            log.warning(
                "Шаблон: голосовое описание длиннее лимита (%s с > %s с)",
                voice.duration,
                MAX_AUDIO_DURATION_SECONDS,
            )
            await message.answer(
                MESSAGE_AUDIO_TOO_LONG.format(limit=MAX_AUDIO_DURATION_SECONDS),
                reply_markup=cancel_keyboard(),
                parse_mode=None,
            )
            return

        try:
            audio = await self._download_voice(message)
        except Exception as exc:  # noqa: BLE001 - сбой Telegram не должен всплывать
            # Аудио не скачалось: сценарий и активный шаблон не меняются,
            # пользователь может записать сообщение снова или прислать текст.
            log_failure(
                exc,
                logger=log,
                context=await self._failure_context("скачивания голосового", user_id),
                unknown_message=MESSAGE_UNEXPECTED_TEMPLATE,
            )
            await message.answer(
                MESSAGE_VOICE_DOWNLOAD_FAILED,
                reply_markup=cancel_keyboard(),
                parse_mode=None,
            )
            return

        try:
            text = await asyncio.to_thread(self._transcribe, audio, float(voice.duration))
        except Exception as exc:  # noqa: BLE001 - обработчик не должен падать молча
            # Сбой распознавания оставляет прежний активный шаблон и не создаёт
            # кандидата; старый неподтверждённый кандидат тоже отбрасывается.
            await state.set_data({})
            await state.set_state(TemplateStates.waiting_description)
            report = log_failure(
                exc,
                logger=log,
                context=await self._failure_context("распознавания", user_id),
                unknown_message=MESSAGE_UNEXPECTED_TEMPLATE,
            )
            await message.answer(
                f"{report.user_message}\n\n{MESSAGE_VOICE_RETRY}",
                reply_markup=cancel_keyboard(),
                parse_mode=None,
            )
            return

        text = text.strip()
        if not text:
            log.warning("Шаблон: распознавание вернуло пустой текст")
            await message.answer(
                MESSAGE_VOICE_EMPTY,
                reply_markup=cancel_keyboard(),
                parse_mode=None,
            )
            return
        await self._present_description(message, state, user_id, text)

    async def _present_description(
        self,
        message: Message,
        state: FSMContext,
        user_id: int,
        text: str,
    ) -> None:
        """Общий путь описания: ссылка, лимиты, разбор и показ кандидата.

        Текст приходит из сообщения или из распознавания речи — дальше разницы
        нет: сначала проверяется ссылка (её нельзя отправлять в LLM как
        описание), затем те же лимиты, показ кандидата теми же кнопками, а
        ошибка разбора оставляет прежний активный шаблон. Проверка ссылки
        стоит здесь, а не в ``handle_description``, чтобы голосовой и текстовый
        вводы были симметричны (P3 T-012, T-015).
        """
        current_state = await state.get_state()
        if extract_first_url(text) is not None:
            await message.answer(
                MESSAGE_LINK_WHILE_DESCRIPTION
                if current_state == TemplateStates.waiting_description.state
                else MESSAGE_LINK_WHILE_CANDIDATE,
                parse_mode=None,
            )
            return
        if len(text) < MIN_DESCRIPTION_LENGTH:
            await message.answer(
                MESSAGE_DESCRIPTION_EMPTY,
                reply_markup=cancel_keyboard(),
                parse_mode=None,
            )
            return
        if len(text) > MAX_DESCRIPTION_LENGTH:
            await message.answer(
                MESSAGE_DESCRIPTION_TOO_LONG.format(
                    length=len(text),
                    limit=MAX_DESCRIPTION_LENGTH,
                ),
                reply_markup=cancel_keyboard(),
                parse_mode=None,
            )
            return

        await message.answer(MESSAGE_PARSING, parse_mode=None)
        try:
            candidate = await asyncio.to_thread(self._parse, user_id, text)
        except Exception as exc:  # noqa: BLE001 - обработчик не должен падать молча
            # Известный сбой не меняет активный шаблон и не создаёт кандидата:
            # прежний неподтверждённый кандидат тоже отбрасывается, иначе
            # «Сохранить» после неудачной правки сохранило бы старый.
            await state.set_data({})
            await state.set_state(TemplateStates.waiting_description)
            report = log_failure(
                exc,
                logger=log,
                context=await self._failure_context("разбора", user_id),
                unknown_message=MESSAGE_UNEXPECTED_TEMPLATE,
            )
            await message.answer(
                f"{report.user_message}\n\n{MESSAGE_DESCRIPTION_RETRY}",
                reply_markup=cancel_keyboard(),
                parse_mode=None,
            )
            return

        await state.set_data({_DATA_CANDIDATE: _candidate_payload(candidate)})
        await state.set_state(TemplateStates.confirming_candidate)
        active = await self._active(user_id)
        log.info(
            "Шаблон: показан кандидат (%s, разделов: %s)",
            _origin_label(active),
            len(candidate.sections),
        )
        await message.answer(
            render_candidate_text(candidate.sections, candidate.tone),
            reply_markup=candidate_keyboard(),
            parse_mode=None,
        )

    async def _download_voice(self, message: Message) -> bytes:
        """Скачать голосовое сообщение в память: на диск оно не попадает.

        Используется путь aiogram ``Bot.download`` с ``BytesIO``: файл живёт
        только в памяти обработчика и уходит в адаптер байтами. Скачивание идёт
        по ``file_id`` (поддерживается всеми aiogram 3.x), а возвращаются ровно
        те байты, которые отдал Telegram; в логе — только их размер.
        """
        bot = message.bot
        voice = message.voice
        if bot is None or voice is None:
            raise RuntimeError("голосовое сообщение недоступно для скачивания")
        buffer = BytesIO()
        downloaded = await bot.download(voice.file_id, destination=buffer)
        if downloaded is None:  # pragma: no cover - Bot.download отдаёт буфер
            raise RuntimeError("Telegram не вернул содержимое голосового сообщения")
        audio = downloaded.getvalue()
        log.info("Шаблон: голосовое получено (%s байт)", len(audio))
        return audio

    def _transcribe(self, audio: bytes, duration_seconds: float) -> str:
        """Синхронное распознавание — в рабочем потоке, одна попытка.

        В лог пишутся только длительность и тип результата: аудио и полный текст
        распознавания — пользовательские данные (ADR-009, FR-12). Число
        байт/символов пишет сам адаптер.
        """
        speech = self._speech
        if speech is None:  # pragma: no cover - вызывается после проверки
            raise RuntimeError("распознавание речи не подключено")
        log.info(
            "Шаблон: распознавание голосового описания (%.1f с)",
            duration_seconds,
        )
        return speech.transcribe(audio, duration_seconds=duration_seconds)

    def _parse(self, user_id: int, description: str) -> NoteTemplate:
        """Синхронный разбор описания: текущий активный шаблон + текст → кандидат.

        Выполняется в рабочем потоке: ``LLMClient`` синхронный и ходит по HTTP.
        Неизменённую часть (разделы или тон) сохраняет
        :meth:`TemplateService.candidate_from_parse`, поэтому «пиши короче»
        не теряет разделы.
        """
        active = self._templates.get_active(user_id)
        log.info(
            "Шаблон: разбор описания (%s, разделов: %s)",
            _origin_label(active),
            len(active.sections),
        )
        draft = self._parser.parse_template(
            description,
            current_sections=active.sections,
            current_tone=active.tone,
        )
        return self._templates.candidate_from_parse(
            user_id,
            sections=draft.sections,
            tone=draft.tone,
        )

    async def _failure_context(self, stage: str, user_id: int) -> str:
        """Контекст записи о сбое: стадия, происхождение и число разделов.

        В лог попадает только пометка о шаблоне, поэтому запись говорит о
        шаблоне, а не о видео, и не раскрывает заголовки, тон и JSON. Если
        активный шаблон прочитать не удалось, остаётся короткая пометка без
        подробностей — контекст не должен подменять исходный сбой. Чтение идёт
        в рабочем потоке, как и остальные файловые операции T-016.
        """
        try:
            active = await asyncio.to_thread(self._templates.get_active, user_id)
        except Exception:  # noqa: BLE001 - контекст не важнее исходной ошибки
            return f"{stage} шаблона"
        return (
            f"{stage} шаблона ({_origin_label(active)}, "
            f"разделов: {len(active.sections)})"
        )

    async def _active(self, user_id: int) -> ActiveTemplate:
        """Прочитать активный шаблон в рабочем потоке (файловый ввод-вывод)."""
        return await asyncio.to_thread(self._templates.get_active, user_id)

    async def _deny(self, message: Message, action: str, user_id: int | None) -> None:
        """Записать отказ и ответить, не роняя обработчик сбоем Telegram."""
        self._log_denied(action, user_id)
        try:
            await message.answer(MESSAGE_ACCESS_DENIED, parse_mode=None)
        except Exception as exc:  # noqa: BLE001 - ответ не должен всплывать в aiogram
            log_failure(exc, logger=log, context="ответа об отказе в доступе")

    async def _deny_callback(
        self,
        callback: CallbackQuery,
        action: str,
        user_id: int | None,
    ) -> None:
        """То же для callback: всплывающее уведомление не роняет обработчик."""
        self._log_denied(action, user_id)
        try:
            await callback.answer(MESSAGE_ACCESS_DENIED, show_alert=True)
        except Exception as exc:  # noqa: BLE001 - ответ не должен всплывать в aiogram
            log_failure(exc, logger=log, context="ответа об отказе в доступе")

    def _log_denied(self, action: str, user_id: int | None) -> None:
        """Записать отказ: первый раз ``WARNING``, повторы — ``DEBUG``."""
        if user_id is None:
            message = "Шаблон: доступ к действию «%s» закрыт, отправителя нет"
            args: tuple[object, ...] = (action,)
        else:
            message = "Шаблон: доступ к действию «%s» закрыт, user ID %s нет в allowlist"
            args = (action, user_id)
        self._denials.log_denial(log, message, *args, user_id=user_id)

    async def handle_callback(
        self,
        callback: CallbackQuery,
        state: FSMContext,
        callback_data: TemplateAction,
    ) -> None:
        """Обработать нажатие кнопки сценария: доступ, затем действие.

        Доступ проверяется до любого действия, поэтому посторонний не увидит и
        не изменит шаблон. Сохранение и сброс дополнительно сверяются с
        состоянием FSM: устаревшая или повторная кнопка не срабатывает дважды, а
        получает понятное сообщение.
        """
        user_id = callback.from_user.id
        if not self._access.is_allowed(user_id):
            await self._deny_callback(callback, "кнопка", user_id)
            return

        await callback.answer()
        action = callback_data.action
        if action == ACTION_EDIT:
            await self._edit(callback, state)
        elif action == ACTION_RESET:
            await self._ask_reset(callback, state)
        elif action == ACTION_RESET_CONFIRM:
            await self._reset(callback, state, user_id)
        elif action == ACTION_SAVE:
            await self._save(callback, state, user_id)
        elif action == ACTION_FIX:
            await self._fix(callback, state)
        elif action == ACTION_CANCEL:
            await self._cancel(callback, state)
        elif action == ACTION_CLOSE:
            await self._close(callback, state)
        else:
            await self._show(callback, MESSAGE_STALE_CALLBACK, empty_keyboard())

    async def _edit(self, callback: CallbackQuery, state: FSMContext) -> None:
        """«Изменить»: ждать одно текстовое описание, кандидата не создавать."""
        await state.clear()
        await state.set_state(TemplateStates.waiting_description)
        await self._show(callback, MESSAGE_ASK_DESCRIPTION, cancel_keyboard())

    async def _fix(self, callback: CallbackQuery, state: FSMContext) -> None:
        """«Исправить»: отбросить кандидата и вернуться к вводу описания."""
        if await self._candidate(state) is None:
            await self._show(callback, MESSAGE_STALE_CALLBACK, empty_keyboard())
            return
        await state.clear()
        await state.set_state(TemplateStates.waiting_description)
        await self._show(callback, MESSAGE_ASK_DESCRIPTION, cancel_keyboard())

    async def _cancel(self, callback: CallbackQuery, state: FSMContext) -> None:
        """«Отмена»: закрыть сценарий без изменений активного шаблона."""
        if await state.get_state() is None:
            await self._show(callback, MESSAGE_STALE_CALLBACK, empty_keyboard())
            return
        await state.clear()
        await self._show(callback, MESSAGE_CLOSED, empty_keyboard())

    async def _close(self, callback: CallbackQuery, state: FSMContext) -> None:
        """«Закрыть»: закрыть просмотр или сценарий без изменений."""
        await state.clear()
        await self._show(callback, MESSAGE_CLOSED, empty_keyboard())

    async def _ask_reset(self, callback: CallbackQuery, state: FSMContext) -> None:
        """«Сбросить»: спросить явное подтверждение, ничего не удаляя."""
        await state.clear()
        await state.set_state(TemplateStates.confirming_reset)
        await self._show(callback, MESSAGE_RESET_ASK, reset_keyboard())

    async def _reset(
        self,
        callback: CallbackQuery,
        state: FSMContext,
        user_id: int,
    ) -> None:
        """Подтверждение сброса: идемпотентно удалить шаблон, вернуть встроенный."""
        if await state.get_state() != TemplateStates.confirming_reset.state:
            await self._show(callback, MESSAGE_STALE_CALLBACK, empty_keyboard())
            return
        try:
            await asyncio.to_thread(self._templates.reset, user_id)
        except Exception as exc:  # noqa: BLE001 - ошибка не должна всплывать в Telegram
            # Сброс не выполнен: пользователь не видит сообщения об успехе, а
            # прежний шаблон остаётся активным.
            report = log_failure(
                exc,
                logger=log,
                context=await self._failure_context("сброса", user_id),
                unknown_message=MESSAGE_UNEXPECTED_TEMPLATE,
            )
            await state.clear()
            await self._show(callback, report.user_message, empty_keyboard())
            return

        await state.clear()
        log.info("Шаблон: пользовательский сброшен, действует встроенный")
        active = await self._active(user_id)
        await self._show(
            callback,
            f"{MESSAGE_RESET_DONE}\n\n{render_template_text(active)}",
            empty_keyboard(),
        )

    async def _save(
        self,
        callback: CallbackQuery,
        state: FSMContext,
        user_id: int,
    ) -> None:
        """«Сохранить»: только здесь кандидат становится активным шаблоном."""
        candidate = await self._candidate(state)
        if candidate is None:
            await state.clear()
            await self._show(callback, MESSAGE_STALE_CANDIDATE, empty_keyboard())
            return
        try:
            await asyncio.to_thread(self._templates.save, user_id, candidate)
        except Exception as exc:  # noqa: BLE001 - ошибка не должна всплывать в Telegram
            # Активный шаблон остаётся прежним: кандидат не сохранён, состояние
            # и клавиатура сохраняются, чтобы попытку можно было повторить.
            report = log_failure(
                exc,
                logger=log,
                context=await self._failure_context("сохранения", user_id),
                unknown_message=MESSAGE_UNEXPECTED_TEMPLATE,
            )
            await self._show(callback, report.user_message, candidate_keyboard())
            return

        await state.clear()
        log.info("Шаблон: сохранён пользовательский (разделов: %s)", len(candidate.sections))
        active = await self._active(user_id)
        await self._show(
            callback,
            f"{MESSAGE_SAVED}\n\n{render_template_text(active)}",
            empty_keyboard(),
        )

    async def _candidate(self, state: FSMContext) -> NoteTemplate | None:
        """Достать кандидата из FSM; ``None`` — его нет или данные повреждены."""
        payload = (await state.get_data()).get(_DATA_CANDIDATE)
        if payload is None:
            return None
        try:
            return _template_from_payload(payload)
        except (TypeError, ValueError, TemplateValidationError):
            log.warning("Кандидат шаблона в FSM повреждён, сценарий сбрасывается")
            return None

    async def _show(
        self,
        callback: CallbackQuery,
        text: str,
        reply_markup: InlineKeyboardMarkup,
    ) -> None:
        """Показать следующий шаг в сообщении сценария, по возможности на месте.

        Устаревшая кнопка не должна плодить сообщения: правим то сообщение, к
        которому она прикреплена. Если сообщение недоступно или не меняется,
        отправляем новое.
        """
        message = callback.message
        if isinstance(message, Message):
            try:
                await message.edit_text(text, reply_markup=reply_markup, parse_mode=None)
                return
            except TelegramBadRequest:
                log.debug("Сообщение сценария шаблона не изменилось, отправляю новое")
        bot = callback.bot
        chat_id = message.chat.id if message is not None else callback.from_user.id
        if bot is not None:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=None,
            )


def _button(text: str, action: str) -> InlineKeyboardButton:
    """Кнопка сценария: в callback-данных только действие."""
    return InlineKeyboardButton(
        text=text,
        callback_data=TemplateAction(action=action).pack(),
    )


def _numbered_sections(sections: Sequence[str]) -> list[str]:
    """Разделы по порядку применения с номерами — человеческий показ."""
    return [f"{index}. {heading}" for index, heading in enumerate(sections, start=1)]


def _candidate_payload(template: NoteTemplate) -> dict[str, object]:
    """Кандидат в FSM: только примитивы, без модели и без JSON-строки."""
    return {"sections": list(template.sections), "tone": template.tone}


def _template_from_payload(payload: object) -> NoteTemplate:
    """Восстановить кандидата из данных FSM и провалидировать лимитами T-001."""
    if not isinstance(payload, dict):
        raise TypeError("кандидат шаблона должен быть словарём")
    sections = payload.get("sections")
    tone = payload.get("tone", "")
    if isinstance(sections, (str, bytes)) or not isinstance(sections, (list, tuple)):
        raise TypeError("разделы кандидата должны быть списком")
    if not isinstance(tone, str):
        raise TypeError("тон кандидата должен быть строкой")
    return NoteTemplate(sections=tuple(sections), tone=tone)


def _origin_label(active: ActiveTemplate) -> str:
    """Происхождение шаблона для лога: без текста разделов и тона."""
    return "встроенный" if active.is_builtin else "пользовательский"


def _sender_id(message: Message) -> int | None:
    """ID отправителя сообщения; ``None``, если отправителя нет (канал)."""
    user = message.from_user
    return user.id if user is not None else None


__all__ = [
    "ACTION_CANCEL",
    "ACTION_CLOSE",
    "ACTION_EDIT",
    "ACTION_FIX",
    "ACTION_RESET",
    "ACTION_RESET_CONFIRM",
    "ACTION_SAVE",
    "BUTTON_CANCEL",
    "BUTTON_CLOSE",
    "BUTTON_EDIT",
    "BUTTON_FIX",
    "BUTTON_RESET",
    "BUTTON_RESET_CONFIRM",
    "BUTTON_SAVE",
    "CALLBACK_PREFIX",
    "MESSAGE_ACCESS_DENIED",
    "MESSAGE_ASK_DESCRIPTION",
    "MESSAGE_CANDIDATE_HINT",
    "MESSAGE_CANDIDATE_TITLE",
    "MESSAGE_CLOSED",
    "MESSAGE_DESCRIPTION_EMPTY",
    "MESSAGE_DESCRIPTION_RETRY",
    "MESSAGE_DESCRIPTION_TOO_LONG",
    "MESSAGE_LINK_WHILE_CANDIDATE",
    "MESSAGE_LINK_WHILE_DESCRIPTION",
    "MESSAGE_ORIGIN_BUILTIN",
    "MESSAGE_ORIGIN_USER",
    "MESSAGE_PARSING",
    "MESSAGE_RESET_ASK",
    "MESSAGE_RESET_DONE",
    "MESSAGE_SAVED",
    "MESSAGE_SECTIONS_TITLE",
    "MESSAGE_STALE_CALLBACK",
    "MESSAGE_STALE_CANDIDATE",
    "MESSAGE_TONE",
    "MESSAGE_TONE_MISSING",
    "MESSAGE_VIEW_ACTIONS",
    "MESSAGE_VIEW_TITLE",
    "MESSAGE_VOICE_DOWNLOAD_FAILED",
    "MESSAGE_VOICE_EMPTY",
    "MESSAGE_VOICE_NOT_EXPECTED",
    "MESSAGE_VOICE_RETRY",
    "MESSAGE_VOICE_UNAVAILABLE",
    "TEMPLATE_BUTTON_TEXT",
    "SpeechTranscriber",
    "TemplateAction",
    "TemplateHandler",
    "TemplateParseResult",
    "TemplateParser",
    "TemplateStates",
    "candidate_keyboard",
    "cancel_keyboard",
    "empty_keyboard",
    "main_keyboard",
    "render_candidate_text",
    "render_template_text",
    "render_tone",
    "reset_keyboard",
    "view_keyboard",
]

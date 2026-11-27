"""Домен шаблона заметки: разделы, тон, лимиты и встроенный шаблон.

Модуль нейтральный: импортирует только стандартную библиотеку, :mod:`tldw.errors`
и текстовый помощник :mod:`tldw.text`, поэтому его можно использовать из
сервисов, обработчиков и тестов без Telegram и HTTP. Здесь живут:

* :class:`NoteTemplate` — разделы в порядке применения и тон генерации;
* :class:`ActiveTemplate` — шаблон вместе с происхождением (``builtin`` /
  ``user``) для человеческого показа и записи в лог;
* :data:`BUILTIN_SECTIONS` / :data:`BUILTIN_TEMPLATE` — ровно пять секций MVP
  в прежнем порядке; ``services/note.py`` строит из них имена ``SECTION_*``,
  поэтому публичный API заметки не меняется;
* лимиты FR-2/FR-5/FR-12 — константы кода, а не env;
* :class:`TemplateRepository` — протокол хранилища шаблонов по Telegram user ID;
* :class:`TemplateService` — выдача активного шаблона, сохранение, сброс и
  слияние правки с текущим шаблоном.

Границы слоёв (ADR-002): хранилище обменивается примитивами
``(разделы, тон)`` (:data:`TemplateRecord`), поэтому ``storage`` не импортирует
доменную модель и не зависит от ``services``. Вся нормализация и валидация —
здесь: заголовок не может добавить ``#``, ``---``, YAML frontmatter,
управляющие символы или вторую строку, а порядок разделов сохраняется.

Ошибки предназначены пользователю: :class:`TemplateValidationError` (шаблон не
прошёл лимиты) и :class:`TemplateParseError` (из правки не собралось
изменений) наследуются от :class:`tldw.errors.UserFacingError`, поэтому
Telegram-слой показывает их текст как есть, а в лог пишет причину.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol, TypeAlias, runtime_checkable

from tldw.errors import TemplateParseError, TemplateValidationError
from tldw.text import one_line

log = logging.getLogger(__name__)

#: Сколько разделов может быть в шаблоне (FR-2).
MIN_SECTIONS: Final = 1
#: Верхняя граница разделов: больше не влезает в читаемую заметку (FR-2).
MAX_SECTIONS: Final = 12
#: Предел длины заголовка раздела в символах (FR-2).
MAX_HEADING_LENGTH: Final = 80
#: Предел длины тона в символах; пустой тон допустим (FR-2).
MAX_TONE_LENGTH: Final = 1000
#: Лимиты текста описания шаблона человеческим языком (FR-5).
MIN_DESCRIPTION_LENGTH: Final = 1
MAX_DESCRIPTION_LENGTH: Final = 4000
#: Сколько непустых пунктов допускается в пользовательском разделе (FR-7).
MIN_SECTION_ITEMS: Final = 1
MAX_SECTION_ITEMS: Final = 10

#: Сообщения валидации и разбора — на русском, без текста шаблона.
MESSAGE_SECTIONS_NOT_LIST: Final = "Разделы шаблона задаются списком заголовков."
MESSAGE_SECTIONS_RANGE: Final = (
    f"В шаблоне должно быть от {MIN_SECTIONS} до {MAX_SECTIONS} разделов."
)
MESSAGE_HEADING_NOT_TEXT: Final = "Заголовок раздела должен быть текстом."
MESSAGE_HEADING_EMPTY: Final = "Заголовок раздела не может быть пустым."
MESSAGE_HEADING_TOO_LONG: Final = (
    f"Заголовок раздела не может быть длиннее {MAX_HEADING_LENGTH} символов."
)
MESSAGE_HEADING_UNSAFE: Final = (
    "Заголовок раздела не может содержать «---», YAML frontmatter "
    "и служебные символы."
)
MESSAGE_HEADING_DUPLICATE: Final = (
    "Заголовки разделов не должны повторяться, в том числе в другом регистре."
)
MESSAGE_TONE_NOT_TEXT: Final = "Тон шаблона должен быть текстом."
MESSAGE_TONE_TOO_LONG: Final = (
    f"Тон шаблона не может быть длиннее {MAX_TONE_LENGTH} символов."
)
MESSAGE_PARSE_NO_CHANGES: Final = (
    "В описании не нашлось изменений разделов или тона. "
    "Опишите, что именно изменить в шаблоне."
)
MESSAGE_PARSE_UNCHANGED: Final = (
    "Новый шаблон совпадает с текущим. Опишите, что именно изменить."
)

#: Ведущие решётки markdown-заголовка: ``## Раздел`` и ``# # Раздел``.
_HEADING_PREFIX_RE: Final = re.compile(r"^(?:#+\s*)+")
#: Управляющие символы, которые нельзя пускать в заголовок и YAML.
_CONTROL_CHARS_RE: Final = re.compile(r"[\x00-\x1f\x7f]")
#: Признак YAML frontmatter или горизонтальной линии в заголовке.
_FRONTMATTER_MARKER: Final = "---"

#: Запись шаблона в хранилище: ``(разделы в порядке применения, тон)``.
#:
#: Хранилище возвращает и принимает именно примитивы, поэтому не импортирует
#: :class:`NoteTemplate` и не зависит от ``services`` (ADR-002).
TemplateRecord: TypeAlias = tuple[tuple[str, ...], str]


def _normalize_sections(value: object) -> tuple[str, ...]:
    """Проверить контейнер разделов и нормализовать каждый заголовок."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TemplateValidationError(MESSAGE_SECTIONS_NOT_LIST)
    if not MIN_SECTIONS <= len(value) <= MAX_SECTIONS:
        raise TemplateValidationError(MESSAGE_SECTIONS_RANGE)
    normalized: list[str] = []
    seen: set[str] = set()
    for section in value:
        heading = normalize_heading(section)
        key = heading.casefold()
        if key in seen:
            raise TemplateValidationError(MESSAGE_HEADING_DUPLICATE)
        seen.add(key)
        normalized.append(heading)
    return tuple(normalized)


def normalize_heading(value: object) -> str:
    """Сделать из значения безопасный заголовок раздела или объяснить отказ.

    Единственная реализация правила «заголовок не может добавить ``#``,
    ``---``, YAML frontmatter или вторую строку»: её используют и шаблон
    (:class:`NoteTemplate`), и динамический раздел заметки
    (:class:`tldw.services.note.NoteSection`), поэтому форматы не разъезжаются.

    :raises TemplateValidationError: если заголовок пустой, не текст, длиннее
        :data:`MAX_HEADING_LENGTH` или содержит служебные символы.
    """
    if not isinstance(value, str):
        raise TemplateValidationError(MESSAGE_HEADING_NOT_TEXT)
    heading = _HEADING_PREFIX_RE.sub("", one_line(value)).strip()
    if not heading:
        raise TemplateValidationError(MESSAGE_HEADING_EMPTY)
    if _FRONTMATTER_MARKER in heading or _CONTROL_CHARS_RE.search(heading):
        raise TemplateValidationError(MESSAGE_HEADING_UNSAFE)
    if len(heading) > MAX_HEADING_LENGTH:
        raise TemplateValidationError(MESSAGE_HEADING_TOO_LONG)
    return heading


def _normalize_tone(value: object) -> str:
    """Нормализовать тон: ``None`` и пробелы — пустой тон, иначе лимит длины."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TemplateValidationError(MESSAGE_TONE_NOT_TEXT)
    tone = one_line(value)
    if len(tone) > MAX_TONE_LENGTH:
        raise TemplateValidationError(MESSAGE_TONE_TOO_LONG)
    return tone


class TemplateOrigin(StrEnum):
    """Происхождение активного шаблона — для показа и лога (FR-4, FR-12)."""

    BUILTIN = "builtin"
    USER = "user"


@dataclass(frozen=True, slots=True)
class NoteTemplate:
    """Шаблон заметки: разделы в порядке применения и тон генерации.

    ``sections`` — названия без ``##``; порядок кортежа — порядок разделов в
    готовой заметке. ``tone`` — свободные указания к стилю, языку и акцентам;
    пустая строка означает «тон не задан» (язык по умолчанию — русский).

    Конструктор нормализует и валидирует данные, поэтому любой созданный
    экземпляр уже безопасен для рендера и промпта:

    * 1–12 разделов (:data:`MIN_SECTIONS`–:data:`MAX_SECTIONS`);
    * заголовок 1–:data:`MAX_HEADING_LENGTH` символов, всегда одна строка:
      ведущие ``#`` снимаются, переводы строк и повторные пробелы
      схлопываются;
    * в заголовке нет ``---``, YAML frontmatter и управляющих символов;
    * заголовки уникальны без учёта регистра и крайних пробелов;
    * тон 0–:data:`MAX_TONE_LENGTH` символов, пустой допустим.

    :raises TemplateValidationError: если данные не проходят лимиты FR-2.
    """

    sections: tuple[str, ...]
    tone: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "sections", _normalize_sections(self.sections))
        object.__setattr__(self, "tone", _normalize_tone(self.tone))


@dataclass(frozen=True, slots=True)
class ActiveTemplate:
    """Активный шаблон вместе с происхождением: встроенный или пользовательский.

    Признак :attr:`is_builtin` нужен, чтобы показать пользователю «встроенный»
    и записать в лог факт применения (FR-4, FR-12), не раскрывая текст шаблона.
    """

    template: NoteTemplate
    origin: TemplateOrigin

    def __post_init__(self) -> None:
        if not isinstance(self.template, NoteTemplate):
            raise TypeError("ActiveTemplate.template должен быть NoteTemplate")
        object.__setattr__(self, "origin", TemplateOrigin(self.origin))

    @property
    def is_builtin(self) -> bool:
        """Применяется ли встроенный шаблон MVP."""
        return self.origin is TemplateOrigin.BUILTIN

    @property
    def sections(self) -> tuple[str, ...]:
        """Разделы активного шаблона в порядке применения."""
        return self.template.sections

    @property
    def tone(self) -> str:
        """Тон активного шаблона; пустая строка — «тон не задан»."""
        return self.template.tone


@dataclass(frozen=True, slots=True)
class TemplatePatch:
    """Правка шаблона из разбора описания человеческим языком (FR-5).

    ``None`` в поле означает «не менять эту часть», поэтому правка сохраняет
    неизменённую часть текущего шаблона. Пустая строка тона — осознанная
    очистка тона.
    """

    sections: tuple[str, ...] | None = None
    tone: str | None = None

    @property
    def is_empty(self) -> bool:
        """Не просит ли правка изменить хоть что-нибудь."""
        return self.sections is None and self.tone is None


#: Встроенный шаблон MVP: ровно пять заголовков в прежнем порядке.
BUILTIN_SECTIONS: Final[tuple[str, ...]] = (
    "TL;DR",
    "Ключевые идеи",
    "Таймкоды",
    "Цитаты",
    "Что проверить",
)
#: Встроенный шаблон без тона: поведение по умолчанию (FR-1).
BUILTIN_TEMPLATE: Final[NoteTemplate] = NoteTemplate(sections=BUILTIN_SECTIONS)
#: Встроенный активный шаблон с признаком происхождения.
BUILTIN_ACTIVE_TEMPLATE: Final[ActiveTemplate] = ActiveTemplate(
    BUILTIN_TEMPLATE,
    TemplateOrigin.BUILTIN,
)


@runtime_checkable
class TemplateRepository(Protocol):
    """Хранилище шаблонов по Telegram user ID.

    Обмен — примитивами :data:`TemplateRecord` ``(разделы, тон)``: слой
    хранения не импортирует доменную модель и не зависит от ``services``.
    ``load`` возвращает ``None``, если шаблона нет или файл непригоден;
    ``delete`` идемпотентен, повторный вызов не ошибка.
    """

    def load(self, user_id: int) -> TemplateRecord | None:
        """Вернуть сохранённый шаблон пользователя или ``None``."""
        ...

    def save(self, user_id: int, record: TemplateRecord) -> None:
        """Атомарно сохранить шаблон пользователя."""
        ...

    def delete(self, user_id: int) -> None:
        """Удалить шаблон пользователя; отсутствие файла не ошибка."""
        ...


def merge_template(current: NoteTemplate, patch: TemplatePatch) -> NoteTemplate:
    """Слить правку с текущим шаблоном, сохранив неизменённую часть (FR-2).

    * заданы только разделы — тон берётся из ``current``;
    * задан только тон — разделы берутся из ``current``;
    * пустой (в том числе из пробелов) тон очищает тон;
    * результат валидируется как обычный :class:`NoteTemplate`, порядок
      разделов сохраняется.

    :raises TemplateParseError: если правка не задаёт ни разделов, ни тона или
        итог совпадает с текущим шаблоном — пользователю нечего подтверждать.
    :raises TemplateValidationError: если итог нарушает лимиты FR-2.
    """
    if patch.is_empty:
        raise TemplateParseError(MESSAGE_PARSE_NO_CHANGES)
    candidate = NoteTemplate(
        sections=current.sections if patch.sections is None else patch.sections,
        tone=current.tone if patch.tone is None else patch.tone,
    )
    if candidate == current:
        raise TemplateParseError(MESSAGE_PARSE_UNCHANGED)
    return candidate


class TemplateService:
    """Сценарии шаблона поверх :class:`TemplateRepository`.

    Сервис не знает про Telegram и HTTP и не ходит в сеть: он либо отдаёт
    встроенный шаблон, либо читает и пишет пользовательский через хранилище.
    Кандидат из :meth:`merge_patch` не сохраняется — его подтверждает
    вызывающий слой (FR-6).
    """

    def __init__(self, repository: TemplateRepository) -> None:
        self._repository = repository

    def get_active(self, user_id: int) -> ActiveTemplate:
        """Вернуть активный шаблон: пользовательский, иначе встроенный (FR-1).

        Повреждённые данные хранилища не роняют бота (FR-3): если запись не
        проходит валидацию, пишется предупреждение и возвращается встроенный
        шаблон. Текст шаблона в лог не попадает.
        """
        record = self._repository.load(user_id)
        if record is None:
            return BUILTIN_ACTIVE_TEMPLATE
        try:
            sections, tone = record
            template = NoteTemplate(sections=sections, tone=tone)
        except (TypeError, ValueError, TemplateValidationError):
            log.warning(
                "Сохранённый шаблон пользователя некорректен, "
                "используется встроенный"
            )
            return BUILTIN_ACTIVE_TEMPLATE
        return ActiveTemplate(template, TemplateOrigin.USER)

    def save(self, user_id: int, template: NoteTemplate) -> None:
        """Сохранить пользовательский шаблон для Telegram user ID."""
        self._repository.save(user_id, (template.sections, template.tone))

    def reset(self, user_id: int) -> None:
        """Удалить пользовательский шаблон; после сброса активен встроенный."""
        self._repository.delete(user_id)

    def merge_patch(self, user_id: int, patch: TemplatePatch) -> NoteTemplate:
        """Слить правку с текущим **активным** шаблоном пользователя (FR-2).

        У пользователя без своего шаблона неизменённой частью считаются
        встроенные разделы и пустой тон. Возвращается кандидат: он ещё не
        сохранён и на генерацию заметок не влияет (FR-6).
        """
        return merge_template(self.get_active(user_id).template, patch)

    def candidate_from_parse(
        self,
        user_id: int,
        *,
        sections: Sequence[str] | None = None,
        tone: str | None = None,
    ) -> NoteTemplate:
        """Собрать кандидата из нейтрального патча разбора описания (T-006).

        LLM-адаптер возвращает только примитивы: ``None`` — «не менять эту
        часть», пустая строка тона — «очистить тон». Неизменённая часть берётся
        из текущего активного шаблона пользователя, итог валидируется по
        лимитам T-001 (:class:`NoteTemplate`).

        Кандидат **не сохраняется**: его подтверждает вызывающий слой (FR-6),
        а прежний активный шаблон остаётся в силе.

        :raises TemplateParseError: если патч не меняет ни разделов, ни тона
            или итог совпадает с текущим шаблоном.
        :raises TemplateValidationError: если итог нарушает лимиты FR-2 или
            пришёл не примитивами (например, разделы строкой).
        """
        if sections is not None:
            if isinstance(sections, (str, bytes)) or not isinstance(sections, Sequence):
                raise TemplateValidationError(MESSAGE_SECTIONS_NOT_LIST)
            sections = tuple(sections)
        patch = TemplatePatch(sections=sections, tone=tone)
        return self.merge_patch(user_id, patch)



__all__ = [
    "BUILTIN_ACTIVE_TEMPLATE",
    "BUILTIN_SECTIONS",
    "BUILTIN_TEMPLATE",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_HEADING_LENGTH",
    "MAX_SECTION_ITEMS",
    "MAX_SECTIONS",
    "MAX_TONE_LENGTH",
    "MIN_DESCRIPTION_LENGTH",
    "MIN_SECTION_ITEMS",
    "MIN_SECTIONS",
    "ActiveTemplate",
    "NoteTemplate",
    "TemplateOrigin",
    "TemplateParseError",
    "TemplatePatch",
    "TemplateRepository",
    "TemplateService",
    "TemplateValidationError",
    "merge_template",
    "normalize_heading",
]

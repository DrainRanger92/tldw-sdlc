"""Хранение пользовательских шаблонов заметки по Telegram user ID (T-002).

Шаблон одного пользователя лежит в отдельном файле
``<DATA_DIR>/templates/<user_id>.json`` и имеет версию схемы:

.. code-block:: json

    {"version": 1, "sections": ["TL;DR", "Примеры кода"], "tone": "короче"}

Запись атомарная (:func:`tldw.storage.atomic.atomic_write_text`): временный файл
рядом с целевым плюс :func:`os.replace`. Сбой записи не уничтожает прежний
шаблон, не оставляет ``.tmp`` и поднимается как :class:`TemplateStorageError`.
Одновременные сохранения одного пользователя не оставляют битый файл: действует
последнее заменённое значение.

Чтение устойчивое: отсутствующий файл — штатное состояние пользователя без
своего шаблона, поэтому это ``DEBUG`` (T-015); битый JSON, неверная схема или
неизвестная ``version`` дают ``WARNING`` в лог и ``None`` (для вызывающего слоя
это значит «использовать встроенный шаблон»), но файл не удаляется молча — его
можно починить или мигрировать вручную.

Границы слоёв (ADR-002): модуль намеренно **не импортирует** :mod:`tldw.services`
и обменивается примитивами ``(разделы, тон)``. Поэтому :class:`TemplateStore`
структурно (duck typing) реализует ``TemplateRepository``, а доменная валидация
лимитов FR-2 остаётся в ``services/template.py``. Здесь проверяется только
схема файла: типы полей и версия.

Telegram user ID — положительное целое число; имя файла собирается из него, а не
из пользовательской строки, поэтому ID не может выйти за каталог шаблонов.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

from tldw.errors import UserFacingError
from tldw.storage.atomic import atomic_write_text

log = logging.getLogger(__name__)

#: Версия схемы файла шаблона; неизвестная версия даёт встроенный шаблон.
SCHEMA_VERSION: Final = 1
#: Подкаталог каталога данных, в котором лежат файлы пользователей.
TEMPLATES_DIR_NAME: Final = "templates"
#: Сообщение пользователю при ошибке записи шаблона (FR-8).
MESSAGE_TEMPLATE_STORAGE_FAILED: Final = "Не удалось сохранить шаблон. Попробуйте позже."
#: Сообщение пользователю при ошибке удаления шаблона (FR-8, FR-9).
MESSAGE_TEMPLATE_DELETE_FAILED: Final = "Не удалось удалить шаблон. Попробуйте позже."


class TemplateStorageError(UserFacingError):
    """Шаблон не удалось сохранить или удалить в каталоге данных.

    Текст безопасен для пользователя; причина остаётся в цепочке исключений для
    :func:`tldw.errors.log_failure`.
    """

    def __init__(self, message: str = MESSAGE_TEMPLATE_STORAGE_FAILED) -> None:
        super().__init__(message)


class _InvalidTemplateFile(ValueError):
    """Внутренняя причина: содержимое файла не соответствует схеме v1."""


def _validate_user_id(user_id: object) -> int:
    """Принять только положительный целый Telegram user ID.

    ``bool`` отвергается отдельно: в Python он является подклассом ``int``, но
    идентификатором пользователя не бывает. Строки тоже отвергаются — путь
    строится из числа, поэтому ``"../evil"`` не может выйти за каталог.

    :raises ValueError: если ID не положительное целое число.
    """
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        raise ValueError("Telegram user ID должен быть положительным целым числом")
    if user_id <= 0:
        raise ValueError("Telegram user ID должен быть положительным целым числом")
    return user_id


def _build_payload(record: object) -> dict[str, object]:
    """Проверить примитивы ``(разделы, тон)`` и собрать JSON-объект схемы v1.

    Доменные лимиты (1–12 разделов, длина заголовка и тона) здесь не
    дублируются: это ответственность ``services/template.py``. Хранилище
    проверяет только структуру, чтобы не записать заведомо чужой формат.

    :raises ValueError: если запись не является парой «список строк, строка».
    """
    if not isinstance(record, (tuple, list)) or len(record) != 2:
        raise ValueError("Запись шаблона должна быть парой (разделы, тон)")
    sections, tone = record
    if isinstance(sections, (str, bytes)) or not isinstance(sections, (tuple, list)):
        raise ValueError("Разделы шаблона должны быть списком строк")
    if not all(isinstance(section, str) for section in sections):
        raise ValueError("Разделы шаблона должны быть списком строк")
    if not isinstance(tone, str):
        raise ValueError("Тон шаблона должен быть строкой")
    return {"version": SCHEMA_VERSION, "sections": list(sections), "tone": tone}


def _parse_payload(raw: str) -> tuple[tuple[str, ...], str]:
    """Разобрать JSON схемы v1 в примитивы ``(разделы, тон)``.

    :raises _InvalidTemplateFile: если JSON битый, схема нарушена или версия
        схемы неизвестна. Текст шаблона в сообщение не попадает.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _InvalidTemplateFile("файл не содержит корректный JSON") from exc
    if not isinstance(payload, dict):
        raise _InvalidTemplateFile("ожидался JSON-объект")
    if "version" not in payload:
        raise _InvalidTemplateFile("нет поля version")
    version = payload["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise _InvalidTemplateFile("поле version должно быть целым числом")
    if version != SCHEMA_VERSION:
        raise _InvalidTemplateFile(f"неизвестная версия схемы: {version}")
    if "sections" not in payload:
        raise _InvalidTemplateFile("нет поля sections")
    sections = payload["sections"]
    if isinstance(sections, (str, bytes)) or not isinstance(sections, list):
        raise _InvalidTemplateFile("поле sections должно быть списком строк")
    if not all(isinstance(section, str) for section in sections):
        raise _InvalidTemplateFile("поле sections должно быть списком строк")
    if "tone" not in payload:
        raise _InvalidTemplateFile("нет поля tone")
    tone = payload["tone"]
    if not isinstance(tone, str):
        raise _InvalidTemplateFile("поле tone должно быть строкой")
    return tuple(sections), tone


class TemplateStore:
    """Файловое хранилище шаблонов по Telegram user ID в ``DATA_DIR``.

    :param data_dir: каталог данных из конфига; файлы лежат в его подкаталоге
        :data:`TEMPLATES_DIR_NAME`. Каталог создаётся при первой записи, чтение
        отсутствующего каталога не ошибка.

    Экземпляр структурно реализует протокол ``TemplateRepository``:
    :meth:`load` возвращает ``None`` для отсутствующего или непригодного файла,
    :meth:`save` атомарно пишет шаблон, :meth:`delete` идемпотентен.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._templates_dir = self._data_dir / TEMPLATES_DIR_NAME

    @property
    def templates_dir(self) -> Path:
        """Каталог файлов шаблонов (``<DATA_DIR>/templates``)."""
        return self._templates_dir

    def _template_path(self, user_id: object) -> Path:
        """Собрать путь ``<templates>/<user_id>.json`` из проверенного числа."""
        identifier = _validate_user_id(user_id)
        path = self._templates_dir / f"{identifier}.json"
        if path.parent != self._templates_dir:  # pragma: no cover - страховка
            raise ValueError("Путь шаблона вышел за каталог данных")
        return path

    def load(self, user_id: int) -> tuple[tuple[str, ...], str] | None:
        """Вернуть сохранённый шаблон пользователя или ``None``.

        ``None`` означает «шаблона нет» либо «файл непригоден». Отсутствие
        файла — штатное состояние (у пользователя может не быть своего
        шаблона), поэтому пишется ``DEBUG`` и не шумит на каждой ссылке.
        Битый файл — ``WARNING``: его стоит заметить и починить. Исключение не
        поднимается (FR-3), файл при этом остаётся на месте.
        """
        path = self._template_path(user_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            log.debug(
                "Шаблон пользователя %s не сохранён, используется встроенный",
                user_id,
            )
            return None
        except UnicodeDecodeError:
            log.warning(
                "Файл шаблона пользователя %s не является текстом UTF-8, "
                "используется встроенный",
                user_id,
            )
            return None
        except OSError as exc:
            log.warning(
                "Не удалось прочитать шаблон пользователя %s (%s), используется встроенный",
                user_id,
                type(exc).__name__,
            )
            return None
        try:
            return _parse_payload(raw)
        except _InvalidTemplateFile as exc:
            log.warning(
                "Шаблон пользователя %s непригоден (%s), используется встроенный; "
                "файл оставлен на месте",
                user_id,
                exc,
            )
            return None

    def save(self, user_id: int, record: tuple[tuple[str, ...], str]) -> None:
        """Атомарно сохранить шаблон пользователя, заменив прежний.

        :raises TemplateStorageError: если каталог или файл нельзя записать;
            прежний файл при этом остаётся нетронутым.
        :raises ValueError: если ID или структура записи некорректны.
        """
        path = self._template_path(user_id)
        payload = _build_payload(record)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        try:
            atomic_write_text(path, text)
        except OSError as exc:
            raise TemplateStorageError() from exc

    def delete(self, user_id: int) -> None:
        """Удалить шаблон пользователя; отсутствие файла не ошибка (FR-9).

        :raises TemplateStorageError: если файл существует, но удалить его не
            удалось.
        :raises ValueError: если ID некорректен.
        """
        path = self._template_path(user_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise TemplateStorageError(MESSAGE_TEMPLATE_DELETE_FAILED) from exc


__all__ = [
    "MESSAGE_TEMPLATE_DELETE_FAILED",
    "MESSAGE_TEMPLATE_STORAGE_FAILED",
    "SCHEMA_VERSION",
    "TEMPLATES_DIR_NAME",
    "TemplateStorageError",
    "TemplateStore",
]

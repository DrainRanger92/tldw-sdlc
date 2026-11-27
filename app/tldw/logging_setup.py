"""Настройка логирования приложения.

Формат лога единый для всех слоёв. Очистка от секретов централизована:
:class:`SanitizingFilter` ставится на обработчики :func:`configure_logging`,
поэтому её проходят **все** записи — ``cli.py``, старт/остановка, обработчики,
адаптеры и сторонние библиотеки. Раньше ``log.exception`` в ``cli.py``
обходил :func:`tldw.errors.sanitize_log_text` и мог напечатать
``Authorization: Bearer``, ключ или полный транскрипт (P2 T-008).
"""

from __future__ import annotations

import logging

from tldw.errors import format_traceback, sanitize_log_block, sanitize_log_text

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

#: Признак стектрейса в готовом сообщении: такой блок очищается построчно.
_TRACEBACK_MARKERS = (
    "Traceback (most recent call last)",
    "Exception Group Traceback",
    "ExceptionGroup:",
)


class SanitizingFilter(logging.Filter):
    """Вырезать секреты и длинные фрагменты из любой записи лога.

    Фильтр работает до форматирования: сообщение и аргументы заменяются уже
    очищенной строкой, а стектрейс ``exc_info`` — безопасным текстом
    :func:`tldw.errors.format_traceback`. Многострочные записи (стектрейсы)
    сохраняют строки и обрезаются по длине, остальные схлопываются в одну
    строку как обычная причина сбоя.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            if any(marker in message for marker in _TRACEBACK_MARKERS):
                safe_message = sanitize_log_block(message)
            else:
                safe_message = sanitize_log_text(message)
            record.msg = safe_message
            record.args = ()
            if record.exc_info:
                exc = record.exc_info[1]
                record.exc_text = (
                    format_traceback(exc) if exc is not None else None
                ) or None
            if record.stack_info:
                record.stack_info = sanitize_log_block(record.stack_info)
        except Exception:  # noqa: BLE001 - фильтр не должен ронять логирование
            # Если очистка не удалась, лучше потерять детали, чем записать
            # неочищенный текст исключения: exc_info тоже убираем.
            record.msg = "[запись лога не удалось очистить]"
            record.args = ()
            record.exc_text = None
            record.exc_info = None
            record.stack_info = None
        return True


def configure_logging(level: str = "INFO") -> None:
    """Включить логирование с уровнем из конфига и очисткой записей."""
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
    sanitizer = SanitizingFilter()
    root = logging.getLogger()
    # Повторный configure_logging не должен копить фильтры на root: обработчики
    # пересоздаются force=True, а фильтры логгера живут дольше.
    root.filters[:] = [
        log_filter for log_filter in root.filters if not isinstance(log_filter, SanitizingFilter)
    ]
    # Фильтр на обработчиках покрывает propagated-записи всех логгеров;
    # фильтр на самом root — записи, отправленные прямо в корневой логгер.
    root.addFilter(sanitizer)
    for handler in root.handlers:
        handler.addFilter(sanitizer)


__all__ = ["LOG_FORMAT", "SanitizingFilter", "configure_logging"]

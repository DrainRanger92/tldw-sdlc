"""Настройка логирования приложения.

Формат лога единый для всех слоёв; значения секретов в лог не попадают,
потому что настройки исключают их из repr, а сообщения собираются из имён
переменных, а не из значений.
"""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Включить логирование с уровнем из конфига."""
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)


__all__ = ["LOG_FORMAT", "configure_logging"]

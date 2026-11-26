"""Точка входа TLDW.

Запуск из каталога ``app``::

    .venv/bin/python main.py

Проверка конфигурации без подключения к Telegram::

    .venv/bin/python main.py --check-config

Настройки и секреты читаются только из env/``app/.env``; при неполной
конфигурации процесс завершается с понятным сообщением и кодом 2.
"""

from __future__ import annotations

import sys

from tldw.cli import main

if __name__ == "__main__":
    sys.exit(main())

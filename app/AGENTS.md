# Правила приложения TLDW

- Продукт: Telegram-бот, превращающий видео с YouTube в `.md`-заметку для Obsidian.
  Vision и границы продукта — `specs/product.md`.
- Стек: Python 3.11+, aiogram 3, httpx, pytest, ruff. Метаданные пакета —
  `app/pyproject.toml`, окружение — `app/.venv`.
- Точка входа: `app/main.py` (запуск — `aidevteam app dev`). Логика — пакет
  `app/tldw/`: `handlers/` (Telegram), `services/` (транскрипт, конспект, шаблон
  заметки), `adapters/` (YouTube, LLM), `storage/`.
- Команды: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`,
  затем `.venv/bin/python -m pytest -q` и `.venv/bin/ruff check .`.
- Внешние вызовы (Telegram API, субтитры YouTube, LLM) — только через адаптеры;
  тесты не ходят в сеть и не требуют токенов.
- Секреты: `TELEGRAM_BOT_TOKEN`, ключ LLM — из env/`app/.env`, никогда в коде.
- Данные, кэш транскриптов и скачанные файлы — `app/data/` (в `.gitignore`).
  Не коммить `.venv`, `data`, `.env`.
- Перед работой: прочитай `memory/MEMORY.md`; проверь `skills/*/SKILL.md`.

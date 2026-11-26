# Базовая линия TLDW после T-001

Дата: 2026-11-26. Состояние `app/` после первого выполненного цикла.
Исторический срез; актуальная базовая линия — `tldw-baseline-after-t003.md`.

## Что уже работает

- Точка входа `app/main.py` вызывает `tldw.cli.main`; поддерживаются
  `--check-config` (собрать приложение и выйти без polling) и `--env-file`.
- Конфиг читается только из env. Обязательные переменные:
  `TELEGRAM_BOT_TOKEN`, `LLM_API_KEY`, `LLM_MODEL`. Необязательные:
  `TELEGRAM_ALLOWED_USER_IDS`, `LLM_BASE_URL`, `LLM_TIMEOUT_SECONDS`,
  `VIDEO_MAX_DURATION_SECONDS`, `DATA_DIR`, `LOG_LEVEL`.
- `app/.env` только подставляет отсутствующие переменные; реальный env
  приоритетнее. Ошибка конфигурации — `ConfigError` и код возврата 2,
  без трейсбека и без значений секретов.
- `Settings` — frozen dataclass; токен и ключ LLM исключены из `repr`.
- `build_application(settings)` создаёт `Bot` и `Dispatcher`, подключает
  корневой роутер `tldw`, создаёт каталог данных и **не ходит в сеть**;
  `Application.run()` запускает polling, `Application.close()` закрывает
  сессию.
- Слои `tldw/handlers`, `tldw/services`, `tldw/adapters`, `tldw/storage`
  существуют как пакеты; пока в них только каркас и `__init__.py`.
- Данные (заметки, кэш транскриптов, скачанные файлы) — в `app/data`
  по умолчанию; путь берётся из `DATA_DIR`, относительный путь считается
  от `app/`, каталог в `.gitignore`.
- 58 офлайновых тестов и `ruff check .` зелёные; тесты не требуют сети
  и реальных токенов.

## Что ещё не сделано

Модель заметки и рендер Obsidian-шаблона, адаптер YouTube, адаптер LLM,
сервис «транскрипт → заметка», Telegram-сценарий «принял → обрабатываю →
готово», проверка allowlist в обработчике, понятные сообщения об ошибках,
README приложения и сквозной тест. Это задачи T-002–T-009.

## Команды

```bash
cd app
.venv/bin/python main.py --check-config
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

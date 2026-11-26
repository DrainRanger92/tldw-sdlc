---
name: offline-tests
description: Как писать тесты TLDW без сети и токенов — фиктивный env, tmp_path для DATA_DIR, фейковые адаптеры и httpx.MockTransport; так, чтобы гейты pytest и ruff проходили офлайн.
---

# Офлайновые тесты TLDW

Паттерн взят из T-001: он повторён в пяти тестовых модулях
(`test_config`, `test_bootstrap`, `test_cli`, `test_entrypoint`,
`test_packaging`) и понадобится всем задачам, которые добавляют
Telegram/YouTube/LLM-вызовы.

## Когда применять

Любой тест в `app/tests/`, если код:
- читает конфиг из env;
- собирает aiogram-приложение;
- ходит в Telegram, YouTube или LLM;
- пишет файлы заметок/кэша.

## Базовый каркас

- `tests/conftest.py` даёт фикстуру `fake_env` с валидными, но фиктивными
  `TELEGRAM_BOT_TOKEN`, `LLM_API_KEY`, `LLM_MODEL` и `DATA_DIR` внутри
  `tmp_path`.
- Настройки собираются явным словарём: `load_settings(fake_env)`. Не
  подменяйте `os.environ`, если можно передать `environ` аргументом.
- `DATA_DIR` всегда указывает в `tmp_path` (или его подкаталог). Тест не
  должен писать в настоящий `app/data`.
- Приложение собирается через `build_application(settings)`; блокирующий
  `Application.run()` подменяется параметром `run_application` в `tldw.cli.main`.
- Для проверки приоритета env над `.env` используйте `tmp_path`-файл и
  `monkeypatch`/явный словарь, а не реальный `app/.env`.

## Внешние вызовы

- Telegram: мок aiogram-бота/`Message`; не поднимайте polling.
- LLM: `httpx.MockTransport` с заранее заданными ответами — успех, битый JSON,
  HTTP 500, таймаут; проверяйте, что ошибки превращаются в `LLMError`.
- YouTube: фейковый клиент адаптера; отдельные кейсы «нет субтитров»,
  «не YouTube», сбой основного источника и срабатывание fallback.
- Никаких `time.sleep`, реальных сетевых адресов и токенов из окружения.

## Обязательные проверки

```bash
cd app
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Тесты должны проходить на чистом окружении без `TELEGRAM_BOT_TOKEN`
и `LLM_API_KEY` в env. Если тест требует реальный секрет — он сломан.

## Анти-паттерны

- Читать настоящий `app/.env` в тесте.
- Оставлять `data/` после теста: только `tmp_path`.
- Мокать `httpx` глобально вместо `MockTransport`/фейкового клиента.
- Проверять только happy path: для каждого адаптера нужны сетевые ошибки,
  таймауты и битые ответы.

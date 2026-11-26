# Базовая линия TLDW после T-007

Дата: 2026-11-26. Срез приложения после седьмого цикла: доступ к боту
ограничен allowlist из env. Предыдущий срез — `tldw-baseline-after-t006.md`
(282 теста); актуальный — **309 тестов**, версия **0.10.0**.

## Что появилось

- **`app/tldw/access.py` (73 строки).** Нейтральный модуль правила доступа:
  - `AccessPolicy` — frozen dataclass с `allowed_user_ids: tuple[int, ...]`;
    в `__post_init__` убирает повторы с сохранением порядка; `is_open` —
    пустой список; `is_allowed(user_id)` — пустой список пропускает всех,
    иначе `None` (сообщение без отправителя) и чужой ID отклоняются;
  - `describe_allowlist(ids)` — «все пользователи» или «N ID», без раскрытия
    самих ID;
  - `warn_if_open_access(ids)` и `OPEN_ACCESS_WARNING` — предупреждение в лог
    при старте, если список пуст.
  Модуль не импортирует `handlers`/`services`/`adapters` (ADR-002).
- **`config.py`.** `Settings.allowed_user_ids` читается из
  `TELEGRAM_ALLOWED_USER_IDS`: разделители `,`, `;`, пробелы; нечисловое или
  неположительное значение → `ConfigError` без эха значения; пустое значение
  → `()` (доступ всем). Переменная остаётся необязательной.
- **`handlers/link.py`.** `LinkHandler` принимает `allowed_user_ids` и
  создаёт `AccessPolicy`. `handle_message` первым делом проверяет доступ —
  до `extract_first_url`, до `MESSAGE_ACCEPTED` и до фоновой задачи. Чужой
  ID получает `MESSAGE_ACCESS_DENIED` («⛔ Доступ закрыт…»), в лог пишется
  warning с user ID (без текста сообщения и ссылки).
- **Проводка.** `create_router(service, *, allowed_user_ids=())` →
  `LinkHandler`; `build_application` передаёт `settings.allowed_user_ids` и
  вызывает `warn_if_open_access` после `configure_logging`; `cli.py`
  использует общий `describe_allowlist` вместо локальной копии (и в
  `--check-config`, и в стартовом логе).
- **Тесты.** Новый `tests/test_access.py` — 15 кейсов политики (пустой и
  заполненный список, посторонний, `None`, дедупликация, описание,
  предупреждение). Плюс кейсы в `test_link_handler.py` (порядок проверки до
  парсинга, `service.calls == []`), `test_bootstrap.py` (allowlist доходит до
  роутера, warning при пустом списке), `test_config.py` (пустое значение,
  нечисловой и неположительный ID, отсутствие значения в ошибке),
  `test_cli.py` (некорректный allowlist → `EXIT_CONFIG_ERROR`, бот не
  стартует). Всего +27 кейсов, **309 passed**.
- **Релизные метаданные.** Версия 0.10.0 в `pyproject.toml` и
  `tldw/__init__.py`; CHANGELOG дополнен записью 0.10.0 про T-007. В записи
  0.9.0 осталась неточность про синхронизацию версии (см.
  `t007-review-issues.md`).

## Чего ещё нет

- **T-008 (понятные ошибки).** Все сбои по-прежнему сводятся к общему
  `MESSAGE_FAILED`; отдельных текстов для «не YouTube», «нет субтитров»,
  «слишком длинное видео», «AI не ответил» нет; `log.exception` пишет полный
  URL. P1-долг T-004/T-005 не закрыт, `except Exception` его маскирует.
- **T-009 (E2E + README).** Сквозного теста «сообщение → `.md`» на всех
  фейках и инструкции запуска в README приложения нет. README репозитория
  описывает оркестратор, а не запуск бота.
- Ограничения обработчика: `Application.close()` не отменяет фоновые задачи;
  нет лимита параллелизма и дедупликации ссылок; `_LINK_RE` без левой
  границы; отказ в доступе не обёрнут и не ограничен по частоте; тест loop
  опирается на жёсткие таймауты.
- Долг прошлых циклов: пустой транскрипт не отвергается сервисом,
  `InvalidURL`/`RuntimeError` мимо `LLMError`, три копии `_one_line`, дубль
  дедупликации ID в `config` и `AccessPolicy`.

## Проверки

- `pytest -q` — **309 passed** (было 282; +27 кейсов), без сети и токенов;
- `ruff check .` — чисто;
- прогон без `TELEGRAM_BOT_TOKEN`/`LLM_API_KEY`/`LLM_MODEL`/
  `TELEGRAM_ALLOWED_USER_IDS` — 309 passed;
- ревьюер в read-only: `test_access.py` + не-`tmp_path` кейсы
  `test_link_handler.py` — 34 passed, 9 env-errors; `test_config`/
  `test_bootstrap`/`test_cli` — 5 passed, 52 env-errors; `ruff` чистый.
  Зелёный полный прогон — за гейтом VERIFY.

## Следующий шаг

По `specs/001-mvp/tasks.md` остались T-008 (S) и T-009 (S/M). Порядок:
T-005, T-006 → T-008; T-006, T-007, T-008 → T-009. Обе задачи после T-007
надо прогнозировать как **M**: T-008 — таксономия ошибок + P1-долг (верхняя
половина M), T-009 — E2E через все слои + README. Подробнее —
`estimate-size-s-collapsed-into-m.md`.

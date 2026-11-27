# Базовая линия TLDW после T-009

Дата: 2026-11-26. Срез приложения после закрытия инкремента MVP (T-001–T-009).
Предыдущий срез — `tldw-baseline-after-t008.md`; следующий —
`tldw-baseline-after-t010.md` (пользовательский шаблон, 828 тестов).

## Что умеет

Telegram-бот принимает ссылку YouTube, достаёт транскрипт
(`youtube-transcript-api` + fallback `yt-dlp`), просит LLM сделать конспект и
присылает `.md` для Obsidian: 7 полей frontmatter (`title`, `source`,
`channel`, `duration`, `date`, `tags`, `status: processed`) и 5 секций
(`TL;DR`, «Ключевые идеи», «Таймкоды», «Цитаты», «Что проверить»).

Сценарий целиком: «принял» → «обрабатываю» → документ → «готово»; доступ по
allowlist проверяется до разбора ссылки и вызовов адаптеров; известные сбои
(не YouTube, нет субтитров, слишком длинное видео, «AI не ответил») дают свои
русские тексты, неизвестные — общее сообщение и `ERROR` со стектрейсом; лог
очищается от токенов, ключей и транскрипта (`tldw/errors.py`).

## Структура

```text
app/
  main.py, README.md
  tldw/  bootstrap.py, config.py, cli.py, logging_setup.py, errors.py, access.py
         handlers/ (link.py, router.py, __init__.py)
         services/ (note.py, summary.py)
         adapters/ (youtube.py, llm.py, resources/)
         storage/  (notes.py)
  tests/ conftest.py, support.py, test_*.py
```

`tests/support.py` — общий каркас: `Harness`, `run_message`, фейковые
Telegram/YouTube/LLM, `make_video`/`make_summary`, фиктивные секреты.
`tests/test_e2e.py` — сквозной путь через настоящие `Dispatcher`,
`LinkHandler`, `SummaryService` и `storage`.

## Гейты и метаданные

- `pytest -q` — **371 passed**; `ruff check .` — чисто; тесты офлайновые, без
  сети и токенов.
- Версия — `0.13.0` в `pyproject.toml` и `tldw/__init__.py` (выровнена в
  T-009). Запись о T-009 в CHANGELOG отсутствует — её добавит VERSION-стадия;
  после неё `__init__` снова отстанет (см. `version-changelog-two-authors.md`).
- `app/README.md` — установка, таблица 9 env-переменных, `--check-config`,
  запуск, ручная проверка на реальной ссылке, негативные сценарии.

## Что осталось (долг)

- **P1 T-004:** `httpx.InvalidURL` и `RuntimeError` отсутствующего ресурса
  выходят мимо `LLMError`.
- **P1 T-005:** `SummaryService.process` не отвергает пустой транскрипт —
  защита держится на `YouTubeAdapter`.
- Оба P1 **не покрыты E2E**: `test_e2e.py` проверяет чужой ID, нет субтитров,
  длинное видео и `LLMError`, но не эти два случая.
- **P2:** очистка логов в `cli.py` идёт в обход `sanitize_log_text`;
  fail-open allowlist в `create_router`; отказ без обёртки и лимита частоты.
- **P3:** `video_id` в логе, ложные срабатывания редактора секретов,
  `ExceptionGroup` в стектрейсе, README не сверяет негативные `MESSAGE_*`,
  три копии `_one_line`, `_format_seconds` дублирует `format_timecode`.
- `specs/001-mvp/plan.md` всё ещё называет текущим инкрементом T-002.

Связанные: `cycle-09-spend.md`, `t009-review-issues.md`,
`review-debt-accumulates.md`, `adr-010-e2e-test-surface.md`.

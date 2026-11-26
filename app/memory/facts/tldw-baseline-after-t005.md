# Базовая линия TLDW после T-005

Дата: 2026-11-26. Срез после цикла 5 — самый актуальный; срезы после T-001,
T-002, T-003 и T-004 считать историческими.

## Что появилось

- **Сервис конспекта** `app/tldw/services/summary.py` (390 строк):
  - `SummaryService(video_source, summarizer, *, max_duration_seconds,
    max_transcript_chars, data_dir=None)` — композиция поверх протоколов
    `VideoSource.fetch(url)` и `SummaryProvider.summarize(...)`; в продакшене
    собирается `SummaryService.from_settings(settings, video_source,
    summarizer)`, порог и `DATA_DIR` — из `Settings`;
  - `process(url)` — единственный публичный сценарий: `fetch` → порог
    длительности → склейка транскрипта → `summarize` → `build_note` →
    `render_note` → запись файла; возвращает `SummaryResult(note, markdown,
    filename, transcript, path)`;
  - `VideoTooLongError(duration_seconds, max_duration_seconds)` — порог
    проверяется сразу после `fetch`, до LLM и до записи: видео длиннее порога
    не оставляет частичной заметки; `duration == порог` допускается,
    `duration is None` не блокирует;
  - `join_transcript(segments, ...)` — строки `[MM:SS]`/`[HH:MM:SS]`,
    формат берётся из `services.note.format_timecode` (без новой копии);
  - усечение — по границам сегментов, маркер
    `[транскрипт усечён: N из M сегментов]` входит в лимит
    `DEFAULT_MAX_TRANSCRIPT_CHARS = 80_000`, инструкция про маркер добавлена
    в `summary_prompt.md`; JSON-схема ответа LLM не меняется;
  - `build_note(video, summary, *, source=None)` — `Note` из метаданных и
    `VideoSummary`; `source` по умолчанию — исходная ссылка пользователя,
    а не нормализованная адаптером.
- **Хранение заметок** `app/tldw/storage/notes.py` (65 строк):
  `note_path(data_dir, filename)` проверяет имя (только `.md`, без каталогов
  и `..`), `save_note(...)` пишет атомарно — `mkstemp` + `os.replace`,
  временный файл удаляется в `except BaseException`; каталог данных создаётся
  при записи.
- **Экспорт** в `services/__init__.py` и `storage/__init__.py`:
  `SummaryService`, `SummaryResult`, `TranscriptText`, `VideoTooLongError`,
  `join_transcript`, `build_note`, `save_note`, `note_path`.
- **Тесты:** `tests/test_summary_service.py` (570 строк, 45 кейсов с
  параметризацией) и `tests/test_storage_notes.py` (75 строк) — на фейковых
  адаптерах, без сети и токенов; полный набор — **257 тестов**, `ruff check .`
  — чисто.

## Чего ещё нет

- Telegram-обработчика T-006 (приём ссылки, «принял → обрабатываю → готово»,
  `asyncio.to_thread`, отправка `.md`), allowlist T-007, пользовательских
  сообщений об ошибках T-008 и сквозного E2E + README T-009.
- Вызова сервиса из `handlers/`: `handlers/router.py` пока не знает про
  `SummaryService`; `build_application` его не собирает.
- Хранения истории/кэша транскриптов: `storage/` умеет только запись `.md`.

## Что знать перед работой

- `services/` не импортирует `httpx`, `youtube_transcript_api`, `yt_dlp`
  (проверяется отдельно) — T-006 обязан вызывать `process()` через
  `asyncio.to_thread`: сервис синхронный, как и оба адаптера.
- Порог длительности и `DATA_DIR` приходят из `Settings`; `SummaryService`
  не читает env сам, поэтому в тестах достаточно передать фейковые адаптеры и
  `tmp_path`.
- `source` в заметке — исходная ссылка пользователя; не заменяйте её на
  нормализованный URL, это зафиксировано тестом.
- Усечение транскрипта нормировано в символах (`80_000`), не в токенах;
  менять лимит нужно вместе с промптом и тестами.
- Известный долг: `httpx.InvalidURL` и `RuntimeError` отсутствующего ресурса
  по-прежнему выходят мимо `LLMError` (P1, закрыть до T-006/T-008);
  `TranscriptText.is_empty` не используется в продакшене — сервис не
  отвергает пустой транскрипт, хотя через `YouTubeAdapter` это недостижимо
  (см. `t005-review-issues.md`); три копии `_one_line` остались.
- Версии: `pyproject.toml` — 0.6.0, `tldw.__version__` — 0.1.0; T-005
  релизные метаданные не трогала.

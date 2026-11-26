# Базовая линия TLDW после T-004

Дата: 2026-11-26. Срез после цикла 4 — самый актуальный; срезы после T-001,
T-002 и T-003 считать историческими.

## Что появилось

- **LLM-адаптер** `app/tldw/adapters/llm.py` (602 строки): `LLMClient` с
  ключом, моделью, `base_url` и таймаутом из `Settings`; `from_settings`;
  `summarize(transcript, title=..., channel=...)`; повтор только на 429/5xx
  (до 3 попыток с удвоением паузы); таймаут и 4xx без повтора; `repr` не
  показывает ключ.
- **Модель ответа** `VideoSummary` (`tldr`, `key_ideas`, `timeline`,
  `quotes`, `follow_ups`) и `parse_summary`: строгая валидация JSON до сборки
  заметки. Битый JSON, пустой ответ, отсутствие обязательного поля и
  нарушение количества элементов дают `LLMResponseError`, частичной заметки
  нет.
- **Ошибки:** `LLMError` — база; `LLMTimeoutError`, `LLMProviderError`,
  `LLMResponseError`; русские `MESSAGE_*` для пользователя. Логи не содержат
  ключа и транскрипта.
- **Ресурсы пакета** `app/tldw/adapters/resources/`:
  `summary_prompt.md` (роль, структура, JSON-схема, лимиты разделов),
  `note_template.md` (5 секций шаблона), `__init__.py`; шаблон
  подставляется в промпт через `{{NOTE_TEMPLATE}}`, `build_system_prompt()`
  кэшируется. `pyproject.toml` объявляет `.md` как package-data.
- **Экспорт** в `adapters/__init__.py`: `LLMClient`, ошибки, `VideoSummary`,
  `SummaryQuote`, `SummaryTimelineEntry`, `parse_summary`, построители
  промпта и `load_*`.
- **Тесты:** `tests/test_llm_adapter.py` — 48 тестов на `httpx.MockTransport`
  (успех, retry на 429/500/502/503, постоянный 500, таймаут, сетевой сбой,
  401 без retry, битый JSON, пустой ответ, отсутствие полей, границы
  разделов, таймкоды, ключ/модель из env, отсутствие секретов в логах);
  плюс тест упаковки ресурсов в `tests/test_packaging.py`. Всего **212
  тестов**, `ruff check .` — чисто.

## Чего ещё нет

- Сервиса конспекта T-005: порог длительности, склейка транскрипта с
  таймкодами, вызов LLM, сборка `Note`, усечение длинного транскрипта.
- Telegram-обработчика T-006, allowlist T-007, пользовательских сообщений
  T-008 и сквозного E2E T-009.
- Связи YouTube → LLM: адаптеры готовы по отдельности, но сервисный слой их
  ещё не соединяет.

## Что знать перед работой

- Внешние вызовы — только через `adapters/`; `services/` и `handlers/` не
  импортируют `httpx`, `youtube_transcript_api` и `yt_dlp` (ADR-002).
- LLM-адаптер синхронный: T-006 обязан уводить `summarize()` в
  `asyncio.to_thread`, иначе заблокирует event loop.
- `VideoSummary` — ещё не заметка: frontmatter (`title`, `source`, `channel`,
  `duration`, `date`, `tags`, `status`) собирает T-005 из метаданных видео.
- Промпт и шаблон — ресурсы пакета, а не env; менять их нужно вместе с
  константами лимитов и тестами.
- Известный долг T-004: `httpx.InvalidURL` и `RuntimeError` отсутствующего
  ресурса выходят мимо `LLMError`; `MAX_FOLLOW_UPS` забыт в `__all__`;
  третья копия `_one_line`; логика `format_timecode` продублирована; тесты не
  покрывают лимит `timeline`, конструктор и отсутствие ресурса. См.
  `t004-review-issues.md`.
- `pyproject.toml` — 0.6.0, `tldw.__version__` — всё ещё 0.1.0; в CHANGELOG
  дубли 0.4.0/0.5.0 по T-003.

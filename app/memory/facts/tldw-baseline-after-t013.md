# Базовая линия TLDW после T-013

Дата: 2026-11-27. Срез после цикла 13 (T-013, аудит долга ревью) — **актуален
до выполнения T-014**. Версия пакета на момент LEARN — **0.17.0**; VERSION
цикла 13 сразу после LEARN поднимает её до **0.18.0** (minor, app-задача).

## Что умеет приложение

- Telegram-бот: ссылка на YouTube → транскрипт → конспект через LLM → `.md`
  для Obsidian; allowlist, фоновые задачи, понятные пользовательские ошибки.
- Пользовательский шаблон заметки: `/template` или кнопка «Шаблон», 1–12
  разделов и тон 0–1000, разбор текста через LLM, кандидат в памяти,
  сохранение только после «Сохранить»; голосовое описание (`handle_voice`) с
  лимитом 60 с и текстовым fallback без `SPEECH_*`.
- **Новое в T-013 — единый контракт ошибок внешних HTTP-адаптеров:**
  - `config.validate_http_url()` проверяет `LLM_BASE_URL` и `SPEECH_BASE_URL`
    целиком (схема, непустой host, порт 1–65535, IPv6, пробелы и
    управляющие символы) и падает `ConfigError` **на старте**, а не сырым
    `httpx.InvalidURL` в запросе; значение в сообщение не попадает;
  - `LLMClient` оборачивает создание клиента и `post`:
    `httpx.InvalidURL`/`ValueError` → `LLMProviderError`; отсутствие ресурса
    промпта или маркера подстановки → `LLMConfigError`, а не `RuntimeError`;
  - `SpeechAdapter` валидирует `SPEECH_BASE_URL` в конструкторе, оборачивает
    `_make_client()` и запрос, ограничивает `media_type` набором
    `ALLOWED_AUDIO_MEDIA_TYPES` (без CRLF/пробелов, регистр нормализуется);
  - `SummaryService.process` отвергает пустой/вырожденный транскрипт
    `EmptyTranscriptError(UserFacingError)` **до** LLM; `.md` и временные
    файлы не создаются, LLM не вызывается;
  - `join_transcript` при лимите меньше маркера и при `budget == 0`
    возвращает пустой `TranscriptText` (`used_segments=0`), а не обрезанный
    маркер.
- Публичная поверхность: `LLMConfigError`, `EmptyTranscriptError`,
  `MESSAGE_EMPTY_TRANSCRIPT`, `ALLOWED_AUDIO_MEDIA_TYPES` и
  `MAX_FOLLOW_UPS` добавлены в `__all__`/реэкспорты.

## Тесты и гейты

- **1011 офлайновых тестов** (было 927; +84), `pytest -q` — 1011 passed,
  `ruff check .` — All checks passed. Новых зависимостей, сети и токенов нет.
- Read-only ревью: 753 passed + 258 env-errors, 0 failed (74,5 %);
  целевые `test_llm_adapter.py` 68/68, `test_speech_adapter.py` 80/80;
  новые кейсы `test_config`/`test_summary_service` с `tmp_path` не
  исполнялись.
- Дифф T-013: 11 файлов, +716/−48; продакшн — 6 файлов (+234/−35), тесты —
  4 файла (+472/−4), новых тестовых файлов нет.

## Открытый долг и следующий шаг

- **≈86 пунктов/групп** (`review-debt-accumulates.md`): T-013 закрыла 9
  групп, ревью добавило 8. Открыты P2 `_media_type` → plain `ValueError`,
  дубли `MESSAGE_EMPTY_TRANSCRIPT`/`MESSAGE_NO_TRANSCRIPT`, широкий
  `except ValueError`, граница `join_transcript` при `limit ≈ marker`.
- P1 T-003 (YouTube: таймаут, двойной `_extract_info`, приоритет ошибок,
  `TypeError`, кэш yt-dlp) — задача **T-014**; поведение шаблона/голоса —
  T-015; логи/доступ/жизненный цикл — T-016; единый источник помощников —
  T-017; тесты/README/E2E P1 — T-018. Все пять — pending в
  `specs/003-review-debt/tasks.md`, размер M, но по факту T-013 их надо
  читать как M4+.
- Бэклог не пуст: `pull_from_backlog` поднимет T-014 (зависимостей нет) или
  T-015 (зависит от T-013, уже done). В один инкремент — 1–2 задачи
  (ADR-014), иначе 400+ шагов и одно ревью.

Связанные: `tldw-baseline-after-t012.md`, `cycle-13-spend.md`,
`t013-debt-audit.md`, `t013-review-issues.md`,
`adr-017-typed-error-contract-url-validation.md`,
`app/memory/MEMORY.md`, `app/skills/intake/SKILL.md`.

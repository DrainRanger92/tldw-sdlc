# Базовая линия TLDW после T-014

Дата: 2026-11-27. Срез после цикла 14 (T-014, надёжность YouTube-адаптера) —
**актуален до выполнения T-015**. Версия пакета на момент LEARN — **0.18.0**;
VERSION цикла 14 сразу после LEARN поднимает её до **0.19.0** (minor,
app-задача).

## Что умеет приложение

- Telegram-бот: ссылка на YouTube → транскрипт → конспект через LLM → `.md`
  для Obsidian; allowlist, фоновые задачи, понятные пользовательские ошибки.
- Пользовательский шаблон заметки: `/template` или кнопка «Шаблон», 1–12
  разделов и тон 0–1000, разбор текста через LLM, кандидат в памяти,
  сохранение только после «Сохранить»; голосовое описание (`handle_voice`) с
  лимитом 60 с и текстовым fallback без `SPEECH_*`.
- Единый контракт ошибок внешних HTTP-адаптеров (T-013):
  `config.validate_http_url()` падает `ConfigError` на старте для
  `LLM_BASE_URL`/`SPEECH_BASE_URL`; `InvalidURL`/`ValueError` заворачиваются
  в `LLMProviderError`/`SpeechProviderError`; отсутствие ресурса промпта —
  `LLMConfigError`; пустой транскрипт — `EmptyTranscriptError` до LLM;
  `media_type` ограничен allowlist.
- **Новое в T-014 — надёжный YouTube-адаптер:**
  - основной источник (`youtube-transcript-api`) получает
    `_TimeoutSession(requests.Session)` через `http_client`; таймаут
    `DEFAULT_TRANSCRIPT_TIMEOUT = 10.0 с` (валидируется: конечный, > 0),
    `requests.Timeout` → `YouTubeNetworkError`; зависший YouTube не держит
    поток `asyncio.to_thread`;
  - **один `_extract_info` на `fetch`:** `FallbackVideo(transcript, metadata)`
    и `FallbackClient.fetch(video_id)`; при срабатывании fallback метаданные
    берутся из того же ответа `yt-dlp`, второй полный запрос не делается;
    `fetch_metadata` вызывается только при успехе основного источника;
  - **приоритет ошибок:** `VideoUnavailableError` важнее
    `NoTranscriptError`, а он — важнее сети; `raise ... from` указывает на
    ошибку источника, определившего итог (`_transcript_error` возвращает
    пару «итог + причина»);
  - битый ответ библиотеки (`None`, dict 0.x, строка, не-итерация, битые
    числа) нормализуется в `YouTubeNetworkError`, а не `TypeError`;
  - `DownloadError` с маркерами private/unavailable/removed/terminated/
    members-only → `VideoUnavailableError`, остальные → `YouTubeNetworkError`;
    текст исключения в лог не попадает;
  - `cachedir: False` — yt-dlp не пишет в `~/.cache`, данные остаются в
    `DATA_DIR`;
  - успешный путь, `Video`/`VideoMetadata`/`Transcript` и тексты `MESSAGE_*`
    не менялись; изменился только внутренний контракт `FallbackClient`.
- Публичная поверхность: добавлены `FallbackVideo` и
  `DEFAULT_TRANSCRIPT_TIMEOUT` в `adapters.youtube.__all__` и реэкспорт
  `tldw.adapters`.

## Тесты и гейты

- **1056 офлайновых тестов** (было 1011; +45), `pytest -q` — 1056 passed,
  `ruff check .` — All checks passed. Новых зависимостей, сети и токенов нет;
  `requests` — транзитивная зависимость `youtube-transcript-api 1.x`, но
  прямой импорт пока не объявлен в `pyproject.toml` (P2 T-014).
- Read-only ревью: **797 passed + 259 env-errors, 0 failed (75,5 %)**;
  целевой `test_youtube_adapter.py` — 91 из 92 (кейс с `tmp_path` не
  исполнялся); `ruff` чисто. Ревьюер дополнительно проверил утверждения о
  библиотеках через `web_fetch`.
- Дифф T-014: 5 файлов, **+753/−105**; продакшн — 2 файла (+197/−45),
  тесты — 1 файл (+491/−57), плюс `facts/t014-debt-audit.md` (+62) и
  `specs/003-review-debt/tasks.md` (+3/−3, артефакт оркестратора); новых
  тестовых файлов нет.

## Открытый долг и следующий шаг

- **≈86 пунктов/групп** (`review-debt-accumulates.md`): T-014 закрыла
  6 групп P1 T-003 и добавила 7 пунктов (1 P2 + 6 P3,
  `t014-review-issues.md`), из них P3 про документацию закрыт в LEARN —
  нетто 0. Открыты P2 `requests` в `pyproject.toml`;
  P3: узкий `except TypeError` в `_segments_from_fetched`, мёртвый
  `except Timeout`, дубль `_timeout_seconds`/`_seconds`, неполное покрытие
  `socket_timeout`, мутабельное `FakeYoutubeDL.instances`.
- P1 T-003 **закрыты полностью**; после T-013 и T-014 P1-разделы
  T-003/T-004/T-005 больше не открыты. Дальше — поведение
  шаблона/голоса (T-015), логи/доступ/жизненный цикл (T-016), единый
  источник помощников (T-017), тесты/README/E2E P1 и упаковка (T-018).
  Все четыре — pending в `specs/003-review-debt/tasks.md`, размер M.
- По факту T-014 аудит-задачи **не все M4+**: T-014 (2 продакшн-файла) —
  M3, 116 шагов; T-013 (6 файлов) — M4+, 241 шаг. T-015 (4 файла) —
  вероятно M3/M4, T-016 (7+ файлов) — M4+; планировать по числу файлов,
  а не по метке (`estimate-audit-cost-scales-with-files.md`).
- Бэклог не пуст: `pull_from_backlog` поднимет T-015 (зависимость T-013
  выполнена). В один инкремент — 1–2 задачи (ADR-014); T-014 + T-015 вместе
  дали бы ≈250–300 шагов, то есть нормальный инкремент, а не «400+».

Связанные: `tldw-baseline-after-t013.md`, `cycle-14-spend.md`,
`t014-debt-audit.md`, `t014-review-issues.md`,
`adr-019-youtube-fetch-reliability.md`,
`app/memory/MEMORY.md`, `app/skills/intake/SKILL.md`.

# Базовая линия TLDW после T-003

Дата: 2026-11-26. Состояние `app/` после третьего выполненного цикла. Дополняет
`tldw-baseline-after-t002.md`; каркас и модель заметки остаются верными, здесь —
что добавил YouTube-адаптер. Исторический срез: после T-004 актуален
`tldw-baseline-after-t004.md`.

## Что уже работает

- `app/tldw/adapters/youtube.py` — единственная граница вызовов YouTube
  (ADR-002, ADR-004):
  - `parse_video_id(url) -> str` — `youtube.com/watch?v=`, `youtu.be/`,
    `/shorts/`, `/embed/`, `/live/`, ссылки без схемы, `m.`/`music.` хосты;
    не-YouTube ссылка или битый id → `NotYouTubeURLError` **до** внешних
    вызовов;
  - модели `TranscriptSegment(text, start_seconds, duration_seconds)`,
    `Transcript(segments, language, source)` с `.text`/`.is_empty`,
    `VideoMetadata(video_id, title, channel, duration_seconds)`,
    `Video(url, metadata, transcript)`;
  - `TranscriptApiClient` — основной источник через
    `youtube-transcript-api` (`YouTubeTranscriptApi().fetch(video_id,
    languages=...)`), ошибки библиотеки переводятся в типизированные;
  - `YtDlpClient` — fallback: метаданные и транскрипт из субтитров `json3`,
    `socket_timeout=15`, `noplaylist`, `skip_download`;
  - `YouTubeAdapter.fetch(url) -> Video` — ссылка → транскрипт (основной
    источник, при сбое fallback) → метаданные; транскрипт обязателен,
    метаданные необязательны (при сбое — пустые);
  - ошибки: `YouTubeAdapterError` → `NotYouTubeURLError`,
    `NoTranscriptError`, `YouTubeNetworkError`, `VideoUnavailableError`
    (подкласс сетевой). При двух ошибках «нет субтитров» имеет приоритет,
    иначе наружу выходит сетевая/«видео недоступно»;
  - адаптер **синхронный**: вызывающий слой (T-006) обязан уводить его в
    `asyncio.to_thread`, чтобы не блокировать event loop.
- Публичный вход — `tldw.adapters`: фасад, клиенты, модели, ошибки,
  `parse_video_id`, константы источников и языков.
- `pyproject.toml` фиксирует `youtube-transcript-api>=1.2,<2`; версия пакета —
  `0.4.0`. Код написан под API 1.x (`fetch()`/`FetchedTranscript`), переход
  на 2.x потребует правки клиента.
- Гейты: **163 теста** зелёные (было 116; T-003 добавила 47), `ruff check .`
  без замечаний. Новые тесты не используют сеть, токены и `tmp_path`.

## Что ещё не сделано

Адаптер LLM (промпт, JSON, `LLMError`, повторы), сервис «транскрипт →
заметка» с порогом длительности, Telegram-обработчик «принял → обрабатываю →
готово», allowlist, понятные ошибки, сквозной тест и README. Это T-004–T-009.

## Контракт для T-005/T-006

- Вызывайте `YouTubeAdapter.fetch(url)` и ловите подклассы
  `YouTubeAdapterError`; не импортируйте `youtube_transcript_api`/`yt_dlp`
  вне `adapters/`.
- `Video.transcript.text` — склеенный текст, `Video.transcript.segments` —
  таймкоды для промпта; `Video.metadata` может быть с пустыми полями, это
  нормальный сценарий, а не ошибка.
- Не создавайте второй экземпляр `YouTubeAdapter` на каждый запрос без
  необходимости: клиенты держат только конфигурацию, состояние неизменяемое.
- Перед T-006 закройте P1-замечания из `t003-review-issues.md` (таймаут
  основного источника, двойной `_extract_info`, приоритет ошибок): иначе
  поток в `to_thread` может зависнуть, а пользователь увидит неверное
  сообщение.

## Команды

```bash
cd app
.venv/bin/python main.py --check-config
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

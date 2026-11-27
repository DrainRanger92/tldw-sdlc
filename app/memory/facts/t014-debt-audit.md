# Аудит долга T-003, закрытого задачей T-014 (YouTube-адаптер)

Дата: 2026-11-27. Задача: T-014 «Надёжность YouTube-адаптера: таймаут, один
запрос метаданных, приоритет ошибок и кэш». Источники: `t003-review-issues.md`
(10 пунктов) и сводка `review-debt-accumulates.md` (T-003: «10 пунктов»).
Каждый пункт сверен с текущим `app/tldw/adapters/youtube.py`, а не с номерами
строк из отчёта цикла 3; прочитан и `adr-004-youtube-adapter-contract.md`.

Статусы: **актуален** — воспроизводился в коде до правки и исправлен здесь;
**устарел** — в коде уже не воспроизводится, закрыт с доказательством;
**процессный** — не код, закрыт/передан процессу; **передан** — актуален, но
по критериям относится к другой задаче спеки 003.

## Таблица «пункт → статус → доказательство»

| № | Пункт T-003 | Статус | Доказательство |
|---:|---|---|---|
| 1 | У основного источника (`TranscriptApiClient`) нет таймаута; зависший YouTube держит поток `to_thread` | **актуален → исправлен** | `youtube.py:310` — `_TimeoutSession(Session)` подставляет `timeout` в каждый запрос; `youtube.py:327` — `TranscriptApiClient(timeout=DEFAULT_TRANSCRIPT_TIMEOUT)`; `_make_api` передаёт сессию в `YouTubeTranscriptApi(http_client=...)`; `Timeout` → `YouTubeNetworkError`. Тесты: `test_transcript_api_client_default_timeout_is_positive`, `..._passes_timeout_session_to_library`, `test_timeout_session_applies_timeout_to_transport` (записывающий adapter видит `2.5` в `send(timeout=...)`), `test_transcript_api_client_maps_timeout_to_network_error`, 7 кейсов `..._rejects_invalid_timeout` (0/−1/inf/nan/str/bool/None) |
| 2 | Fallback делает второй полный `_extract_info`: `fetch_transcript` получил `info`, а `fetch_metadata` вызывает yt-dlp заново | **актуален → исправлен** | `youtube.py:239` — `FallbackVideo(transcript, metadata)`; `youtube.py:258` — `FallbackClient.fetch` — один запрос; `YtDlpClient.fetch` (`:386`) строит и то и другое из одного `info`; `YouTubeAdapter._fetch_fallback` (`:509`) забирает метаданные из того же результата, `fetch_metadata` вызывается только когда транскрипт дал основной источник. Тесты: `test_fetch_extracts_ytdlp_info_once_for_transcript_and_metadata` (сумма `extract_calls == 1` на реальном `YtDlpClient` + фейковом `YoutubeDL`), `test_fetch_extracts_ytdlp_info_once_when_primary_succeeds` (1 извлечение, 0 `urlopen`), `test_fetch_does_not_ask_metadata_again_after_fallback_transcript` (`metadata_calls == []`) |
| 3 | Приоритет ошибок: `VideoUnavailable` primary + `NoTranscript` fallback даёт «нет субтитров»; `raise ... from fallback_error` врёт при победе primary | **актуален → исправлен** | `youtube.py:591` — `_transcript_error` возвращает `(итоговая ошибка, причина)`: сначала `VideoUnavailableError`, затем `NoTranscriptError`, затем сеть; `_fetch_fallback` делает `raise final_error from cause`. Тесты: матрица `test_transcript_error_priority` — 9 сочетаний primary/fallback; `test_video_unavailable_in_primary_is_the_cause_of_final_error` (`__cause__ is primary_error`), `test_no_subtitles_error_names_the_winning_source_as_cause`, `test_network_error_names_the_last_attempt_as_cause`, `test_empty_fallback_transcript_keeps_primary_unavailable_error` |
| 4 | Построение `segments` вне `try`: `None`/старый dict-формат даёт `TypeError` вместо `YouTubeNetworkError` | **актуален → исправлен** | `youtube.py:534` — `_segments_from_fetched` проверяет форму ответа (None/str/bytes/Mapping/не-итерация/сниппет-dict/не-`str` текст/битые числа) и поднимает `YouTubeNetworkError`; вызывается после `try` вокруг `api.fetch`, но сам не пропускает сырых исключений. Тесты: `test_transcript_api_client_rejects_broken_response` — `None`, `{"snippets": [...]}`, `[{"text": ...}]`, строка, `42`; `test_transcript_api_client_rejects_snippet_with_bad_numbers` |
| 5 | `YtDlpClient._extract_info` маппит private/unavailable в `YouTubeNetworkError` | **актуален → исправлен** | `youtube.py:568` — `_download_error`: маркеры private/unavailable в тексте `DownloadError` → `VideoUnavailableError`, остальные → `YouTubeNetworkError`; `_extract_info` ловит `DownloadError` отдельно от прочих. Тесты: 6 кейсов `test_ytdlp_private_or_unavailable_maps_to_video_unavailable` (private/unavailable/removed/terminated/members-only), 3 кейса `test_ytdlp_other_download_errors_map_to_network`, сквозной `test_ytdlp_unavailable_error_reaches_user_through_adapter` |
| 6 | `YtDlpClient._options` не задаёт `cachedir=False`, yt-dlp пишет в `~/.cache` | **актуален → исправлен** | `youtube.py:452` — `"cachedir": False` (штатный способ yt-dlp отключить кэш: `Cache.store` выходит, если `enabled` ложно). Тест `test_ytdlp_cache_is_disabled_and_writes_nothing_to_home`: опции перехвачены у реального клиента, затем настоящий `YoutubeDL(options).cache.store(...)` при `XDG_CACHE_HOME`/`HOME` внутри `tmp_path` не создаёт ни одного каталога |
| 7 | Дубли `_text`/`_one_line` и валидации `duration_seconds` с `services/note.py` | **частично устарел, остаток передан в T-017** | `_one_line` закрыт ещё в цикле 10: `grep` находит только `tldw/text.py::one_line`, копий нет. `_text` остаётся в `adapters/youtube.py:730` и `services/note.py:562`; `VideoMetadata.__post_init__` повторяет `note.py::_validate_duration`. Это прямо в критериях T-017 («`_text`/валидация длительности не дублируются между `services` и `adapters`»), поэтому здесь не трогалось: правка задела бы формат/ошибки заметки вне поверхности T-014 |
| 8 | `adapters/__init__.py` жадно тянет `yt_dlp` (~секунда) | **актуален → передан в T-018** | `python -c "import sys, tldw.adapters; print('yt_dlp' in sys.modules)"` → `True` (реэкспорт `youtube` в `adapters/__init__.py`). Критерий T-018: «`adapters/__init__.py` не тянет `yt_dlp` при простом импорте». Правка ленивого экспорта — отдельная поверхность, в T-014 не входит |
| 9 | Тесты сцеплены с приватными именами (`_parse_json3`, monkeypatch `_extract_info`/`_read_caption`) | **частично исправлен, остаток передан в T-018** | В T-014 убраны monkeypatch `_extract_info`/`_read_caption`: тесты `YtDlpClient` теперь подменяют только библиотечную границу `tldw.adapters.youtube.YoutubeDL` фейком и проверяют публичные `fetch`/`fetch_metadata`. Импорт `_parse_json3` в `test_youtube_adapter.py:47` остался — критерий T-018 «тесты не цепляются за приватные имена»; переносить парсер в публичную поверхность в этой задаче не требовалось |
| 10 | `specs/001-mvp/plan.md` утверждает «Текущий инкремент — T-002» | **процессный → закрыт** | Это не код: `grep -n "Текущий инкремент" specs/001-mvp/plan.md` пуст, упоминание T-002 (`plan.md:41`) — историческая строка «(T-002, сделано)». Расхождение закрыто ANALYZE цикла 10 (`plan-md-increment-drift.md`); правило — «текущий инкремент» ведёт `tasks.md`/оркестратор, а не план. Строка в `specs/002-user-note-templates/plan.md:224` относится к другой спеке и не является пунктом T-003; `specs/` разработчик не правит |

## Что именно изменилось в коде

- `TranscriptApiClient`: параметр `timeout` (по умолчанию
  `DEFAULT_TRANSCRIPT_TIMEOUT = 10.0`), валидация конечного положительного
  числа, `_TimeoutSession` как `http_client` библиотеки, явный маппинг
  `requests.Timeout`.
- `TranscriptApiClient.fetch`: ответ библиотеки нормализуется
  `_segments_from_fetched`; битые формы дают `YouTubeNetworkError`.
- `FallbackVideo` + `FallbackClient.fetch`: один ответ yt-dlp на транскрипт и
  метаданные; `YtDlpClient.fetch_metadata` остаётся для успешного основного
  пути (там всё равно нужно одно извлечение).
- `_download_error`: private/unavailable → `VideoUnavailableError`,
  остальные `DownloadError` → `YouTubeNetworkError`.
- `_options`: `cachedir=False`.
- `_transcript_error`: приоритет `VideoUnavailable` → `NoTranscript` → сеть и
  пара «итоговая ошибка + причина» для `raise ... from`.
- Тексты `MESSAGE_*` и успешный путь (`Video`, `VideoMetadata`, `Transcript`,
  источники) не менялись.

## Гейты

- `pytest -q`: **1056 passed** (было 1011; +45 нетто, из них 92 кейса в
  `tests/test_youtube_adapter.py`), офлайн, без токенов.
- `ruff check .`: чисто.
- Новых зависимостей нет: `requests` — уже жёсткая зависимость
  `youtube-transcript-api 1.x` (её `http_client`), `pyproject.toml` не менялся;
  тесты не ходят в сеть и не трогают настоящий `~/.cache`.

## Передано дальше (не потеряно)

- **T-017:** дубли `_text` и валидации длительности с `services/note.py`
  (пункт 7), `__all__`/публичная поверхность, а также новый P3 ревью —
  дубль `_timeout_seconds`/`_seconds`.
- **T-018:** ленивый `adapters/__init__.py` (пункт 8), тесты без приватных
  имён и `_parse_json3` (пункт 9), а также новый P2 — `requests` без строки
  в `pyproject.toml`, узкий `except TypeError` в `_segments_from_fetched`,
  мёртвый `except Timeout`, неполное покрытие `socket_timeout`,
  мутабельное `FakeYoutubeDL.instances`.

## Итог ревью и LEARN

Ревью T-014 — `VERDICT: APPROVE`, FIX=0; таблица аудита была **в диффе**
(`+62`), поэтому P2 цикла 13 «не подтверждено из диффа» не повторился.
Ревьюер сверил все 10 пунктов и не нашёл дефектов корректности; замечания —
1 P2 (`requests` в `pyproject.toml`) и 6 P3 — выписаны в
`t014-review-issues.md`. P3 про устаревшие ADR-004/`offline-tests`/индекс
закрыт в LEARN (ADR-019, обновление скила и `MEMORY.md`); остальные пункты
переданы в T-017/T-018.

# Замечания ревью T-003, оставшиеся в коде

Дата: 2026-11-26. Ревью T-003 завершилось `VERDICT: APPROVE`: все критерии
приёмки закрыты, 47 новых тестов проходят (163 всего), `ruff` зелёный,
блокирующих дефектов нет. Замечания ниже не исправлены в этом цикле; сохраняю
их, чтобы закрыть до T-005/T-006, где адаптер начнёт работать на пользователя.

## P1 — исправить до сервиса и Telegram-сценария

1. **Нет таймаута у основного источника.** `TranscriptApiClient` создаёт
   `YouTubeTranscriptApi()` без `http_client` с таймаутом (у `YtDlpClient`
   `socket_timeout=15` есть). Если YouTube зависнет, поток, выделенный в T-006
   через `to_thread`, останется занятым. Нужна `requests.Session` с timeout
   или явный таймаут вокруг вызова.
2. **Fallback делает два полных `_extract_info`.** `fetch_transcript` уже
   получил `info` с title/channel/duration, но `fetch_metadata` вызывает
   yt-dlp заново. Это лишние секунды и удвоенная нагрузка на YouTube; передать
   `info` из транскрипта в метаданные или кэшировать в пределах одного
   `fetch`.
3. **Неверный приоритет ошибок.** Если основной источник дал
   `VideoUnavailableError`, а fallback — `NoTranscriptError`,
   `_transcript_error` возвращает `NoTranscriptError`, и пользователь увидит
   «нет субтитров» вместо «видео недоступно». Плюс `raise ... from
   fallback_error` даже когда итог диктует primary-ошибка — cause в логе
   вводит в заблуждение.
4. **`TranscriptApiClient.fetch`: нормализация вне `try`.** Построение
   `segments` из `fetched` вынесено за `try/except`; при неожиданном ответе
   (`None`, список dict в старом стиле API) вылетит `TypeError`, хотя
   докстринг обещает `YouTubeNetworkError` для битого ответа. Обернуть
   построение или проверить тип.
5. **`YtDlpClient._extract_info` прячет «видео недоступно».** Любой
   `DownloadError` маппится в `YouTubeNetworkError`, включая private/
   unavailable, хотя для этого есть `VideoUnavailableError`. Сейчас этот класс
   производит только основной путь.
6. **Кэш yt-dlp вне `app/data/`.** `YtDlpClient._options` не задаёт
   `cachedir=False`, поэтому yt-dlp может писать в `~/.cache`. Это расходится
   с правилом «данные приложения — в `DATA_DIR`» и с `.gitignore`.

## P2 — гигиена, можно закрыть при следующем касании

7. **Дублирование с `services/note.py`.** `_text` и `_one_line` побайтово
   совпадают, а валидация `duration_seconds` в `VideoMetadata.__post_init__`
   повторяет `note.py::_validate_duration` вместе с текстами ошибок. Этот же
   пункт уже был на ревью T-002 — долг повторяется. Вынести общие текстовые
   помощники в нейтральный модуль, но не импортировать `services` из
   `adapters` (иначе инверсия слоёв).
8. **Жадный импорт в `adapters/__init__.py`.** Любой `import tldw.adapters`
   тянет `yt_dlp` (~секунда) и падает без зависимости; для будущего
   LLM-адаптера и его тестов это лишняя связность. Ленивый `__getattr__` или
   импорт в момент использования.
9. **Тесты сцеплены с приватными именами.** Импортируется `_parse_json3`,
   monkeypatch’атся `_extract_info`/`_read_caption`. Нет кейсов на маппинг
   ошибок yt-dlp, проброс `languages` через `YouTubeAdapter(languages=...)` в
   оба клиента, `parse_video_id(None)` и вариант языка `ru-orig`. Проверять
   публичное поведение, приватные помощники — через тонкий публичный парсер
   или не тестировать напрямую.

## P3 — документация

10. `specs/001-mvp/plan.md` по-прежнему утверждает «Текущий инкремент —
    T-002», хотя T-003 уже done. См. `plan-md-increment-drift.md`.

## Статус

**Цикл 14 (T-014): P1 1–6 закрыты полностью, P2 7 и 9 частично переданы,
P2 8 и P3 10 — переданы/процессные.** Итог по пунктам:

| № | Пункт | Статус после T-014 |
|---:|---|---|
| 1 | Таймаут основного источника | **закрыт:** `_TimeoutSession`, `DEFAULT_TRANSCRIPT_TIMEOUT`, `Timeout` → `YouTubeNetworkError` |
| 2 | Два `_extract_info` | **закрыт:** `FallbackVideo`/`FallbackClient.fetch`, метаданные из того же `info` |
| 3 | Приоритет ошибок и `raise from` | **закрыт:** `_transcript_error` возвращает «итог + причина» |
| 4 | `TypeError` на битом ответе | **закрыт:** `_segments_from_fetched` нормализует формы в `YouTubeNetworkError` |
| 5 | `DownloadError` private/unavailable | **закрыт:** `_download_error` по маркерам, текст в лог не идёт |
| 6 | Кэш yt-dlp вне `DATA_DIR` | **закрыт:** `cachedir: False` |
| 7 | Дубли `_text`/`_one_line`/duration | `_one_line` закрыт в цикле 10; `_text` и duration — **передано в T-017** |
| 8 | Жадный `adapters/__init__.py` | **передано в T-018** (ленивый экспорт) |
| 9 | Тесты и приватные имена | monkeypatch приватных убран; импорт `_parse_json3` — **передано в T-018** |
| 10 | `plan.md` «текущий инкремент» | **процессный, закрыт** (`plan-md-increment-drift.md`) |

Доказательства и таблица — `t014-debt-audit.md`; замечания ревью T-014 —
`t014-review-issues.md`. Открытых P1 у T-003 больше нет: впервые P1-раздел
закрыт полностью, а не передан следующей задаче.

# Замечания ревью T-011: `APPROVE` с шестью пунктами, FIX не было

Дата: 2026-11-27. Ревью цикла 11 проверяло diff против `53f9901` —
единственную задачу T-011 (адаптер распознавания речи). Вердикт —
**`VERDICT: APPROVE`** с первой итерации, но с шестью неблокирующими
группами замечаний: **2 P2 + 4 P3**. Раунда исправлений не было, поэтому
все шесть групп остаются в коде и переходят в долг
(`review-debt-accumulates.md`).

Ревьюер работал в read-only песочнице. Полный `pytest` недоступен
(`tmp_path`), но он прогнал **685 тестов из 893 (77 %) + 208 env-errors,
0 failed**, целевые файлы (`test_speech_adapter.py` — 52,
`test_readme.py` — 35, `test_packaging.py` — 8,
`test_llm_adapter`/`test_youtube_adapter`/`test_errors` — 121) и
`ruff check --no-cache .`; см. `reviewer-readonly-tests.md`.

## Пункты

1. **P2 — handoff: 741 строка вне diffstat.** В промпт ревью ушёл diffstat
   из **6 tracked-файлов и 154 строк**, а два главных файла T-011 —
   `app/tldw/adapters/speech.py` (331) и `app/tests/test_speech_adapter.py`
   (410) — были **untracked**. Разработчик не выполнил `git add -N app/`
   перед подсчётом диффа. Ревьюер нашёл файлы через `git status -uall` и
   прочитал их, поэтому приёмка не пострадала; оркестратор коммитит
   `git add -A`, поэтому код не потерялся. Но доказательство на ревью снова
   было неполным: это **третья регрессия** после T-006 (109 строк вместо
   ~721) и T-009 (21/69 вместо ≈687). Цена — 5–10 шагов диагностики и P2 в
   долге; см. `review-diff-untracked.md`.
2. **P2 — `httpx.InvalidURL` вылетает мимо `SpeechError`.**
   `SpeechAdapter.transcribe` ловит `httpx.TimeoutException` и
   `httpx.HTTPError` (`speech.py:224-235`), но `httpx.InvalidURL` не
   наследуется от `HTTPError`, а `_make_client()` (`speech.py:249-258`)
   стоит вне `try`. Конфиг проверяет только схему URL
   (`_parse_http_url`), а не host/port. Воспроизведено:
   `SPEECH_BASE_URL=https://x.test:bad/v1` проходит `load_settings` и
   конструктор адаптера, после чего `transcribe()` поднимает сырой
   `httpx.InvalidURL` — не `SpeechError` и без понятного пользователю
   текста. Это та же дыра, что P1 T-004 в `LLMAdapter` (открыт с цикла 4);
   теперь она повторилась во втором адаптере — см.
   `invalidurl-escapes-every-adapter.md`.
3. **P3 — конфиг принимает `inf`/`nan`.** `_parse_float`
   (`config.py:261-270`) проверяет только `value < minimum`, поэтому
   `SPEECH_TIMEOUT_SECONDS=inf` (и `nan`) проходит в `Settings`. Сам
   `SpeechAdapter` отвергает такое значение (`speech.py:148`), но падает
   plain `ValueError`, а не `ConfigError`, и только при создании адаптера.
   Тот же дефект был P3 T-001 для `LLM_TIMEOUT_SECONDS`; нужна проверка
   `math.isfinite` в `_parse_float`, а не в каждом адаптере.
4. **P3 — порядок проверок «длительность → конфигурация».** В
   `transcribe` сначала `_check_duration` (`speech.py:209`), потом
   `is_configured` (`speech.py:210`). Для ненастроенного адаптера аудио
   61 с даст `SpeechTooLongError`, а не `SpeechNotConfiguredError` —
   пользователь увидит «слишком длинное», хотя голосовой ввод вообще не
   настроен. T-012 должна проверять `is_configured` первой (и предлагать
   текст), иначе критерий «без настройки — текстовый ввод» будет нарушен.
5. **P3 — `media_type` не валидируется.** `media_type` подставляется в
   multipart (`speech.py:219-223`) как есть; CRLF в нём добавляет лишний
   заголовок части (`Content-Type: audio/ogg\r\nX: y`). `filename` httpx
   экранирует, а `media_type` — нет. Сейчас значение задаёт код
   (`DEFAULT_AUDIO_MEDIA_TYPE`), но T-012 будет передавать тип из
   Telegram-сообщения — до этого стоит ограничить набор допустимых
   MIME-типов.
6. **P3 — логи и тесты.** (а) Адаптер сам пишет `WARNING` при отсутствии
   конфигурации (`speech.py:210-212`), а `log_failure` в обработчике
   залогирует тот же `SpeechNotConfiguredError` ещё раз — двойной WARNING
   на каждое голосовое сообщение; достаточно одного места. (б) На
   HTTP-ошибке логируется `response.reason_phrase` (`speech.py:238-242`) —
   серверный текст; для критерия «аудио и распознанный текст не в логе»
   достаточно кода ответа. (в) В `test_speech_adapter.py` локальный
   `BASE_ENV` дублирует `support.fake_env`; (г) в `test_config.py` нет
   кейсов `SPEECH_TIMEOUT_SECONDS=inf/nan` (в адаптере они есть).

## Что это значит

- **`APPROVE` снова не означает «долга нет».** Шесть групп не исправлялись:
  P2 `InvalidURL` — поведенческая дыра, P2 handoff — процессная, четыре P3 —
  качество/конфиг/логи. Долг вырос с **77 до 83** пунктов и групп
  (2 P2 + 4 P3), P1 T-004/T-005 не закрыты.
- **`InvalidURL` — уже не случайность, а паттерн.** T-004 (LLM) и T-011
  (speech) повторили одну ошибку: `except httpx.HTTPError` не ловит
  `InvalidURL`, конфиг проверяет только префикс. Следующий адаптер получит
  её же, если не вынести проверку в общий рецепт (`adapter-boundary`) и не
  добавить тест на битый URL в конфиг.
- **Конфиг снова оказался слабее адаптера.** `SpeechAdapter` проверяет
  конечность таймаута, `load_settings` — нет; это второй случай после
  T-001. Проверка должна быть в `_parse_float`, одном месте для всех
  таймаутов.
- **T-012 нужно планировать с этими пунктами:** `is_configured` до
  длительности, allowlist до скачивания, `media_type` из Telegram,
  одиночный WARNING и общий `_parse_float`. Иначе голосовой сценарий
  унаследует все четыре P3.

## Что стало в цикле 12

T-012 закрыла три группы из шести: handoff (P2) не повторился — `git add -N`
выполнен, 16 файлов в diffstat; `math.isfinite` добавлен в `_parse_float`
(P3); порядок «`is_configured` до длительности» соблюдён, двойного WARNING
больше нет (P3). **Остались:** P2 `httpx.InvalidURL` мимо `SpeechError`
(обработчик проверяет `is_configured`, но `_make_client` вне `try`, а конфиг
проверяет только схему URL) и P3 `media_type` (T-012 его из Telegram не
передаёт, поэтому дефект теоретический). Долг: 83 → ≈87
(`review-debt-accumulates.md`, `cycle-12-spend.md`).

Связанные: `review-debt-accumulates.md`, `review-diff-untracked.md`,
`reviewer-readonly-tests.md`, `cycle-11-spend.md`,
`invalidurl-escapes-every-adapter.md`,
`t001-t010-review-issues.md`, `t004-review-issues.md`,
`tldw-baseline-after-t011.md`, `adr-015-speech-adapter.md`.

## Что стало в цикле 13 (T-013)

- **P2 `InvalidURL` закрыт:** `SpeechAdapter` валидирует `SPEECH_BASE_URL`
  в конструкторе, оборачивает `_make_client()` и `client.post()`,
  `httpx.InvalidURL`/`ValueError` → `SpeechProviderError`; конфиг падает
  `ConfigError` на старте. Тесты `test_broken_base_url_*`,
  `test_request_url_error_*`.
- **P3 `media_type` закрыт частично:** `ALLOWED_AUDIO_MEDIA_TYPES` и
  `_media_type` ограничивают набор MIME; но на неизвестный тип всё ещё
  летит plain `ValueError`, а не `SpeechError` (P2 ревью T-013) — это
  осталось в долге и попадает в T-015/T-016, когда тип придёт из Telegram.
- **Остались:** `reason_phrase` сервера в логе → T-016; тестовые дубли
  `BASE_ENV` → T-018.
- Итог: 2 группы закрыто (P2 + P3), 2 переданы, 2 устарели в цикле 12.

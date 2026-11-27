# Базовая линия TLDW после T-011: адаптер речи готов, Telegram ещё не подключён

Дата: 2026-11-27. После цикла 11 (T-011) приложение умеет всё, что умело
после T-010, плюс **распознавание речи как отдельная граница**; голосовой
ввод в Telegram ещё не подключён — это T-012 в бэклоге. Исторический срез:
актуальная базовая линия — `tldw-baseline-after-t012.md`.

## Что появилось

- **`app/tldw/adapters/speech.py` (331 строка).** `SpeechAdapter` —
  единственное место, где приложение обращается к сервису распознавания
  речи. Контракт:
  - `transcribe(audio, *, duration_seconds, filename="audio.ogg",
    media_type="audio/ogg")` делает **один** `POST` на
    `audio/transcriptions` OpenAI-совместимого API и возвращает текст;
  - ключ, базовый URL, модель и таймаут — из **необязательных**
    `SPEECH_API_KEY`, `SPEECH_BASE_URL`, `SPEECH_MODEL`,
    `SPEECH_TIMEOUT_SECONDS`; пустые ключ/модель = «голосовой ввод не
    настроен», `is_configured` даёт обработчику сигнал для текстового
    fallback;
  - лимит `MAX_AUDIO_DURATION_SECONDS = 60` проверяется до сети;
  - ошибки — `SpeechError(UserFacingError)` и подклассы
    `SpeechNotConfiguredError`, `SpeechTooLongError`, `SpeechTimeoutError`,
    `SpeechProviderError`, `SpeechResponseError`;
  - аудио и полный текст распознавания в лог не пишутся; в лог идут
    длительность, число байт/символов, код ответа и тип исключения;
  - `__repr__` скрывает ключ; адаптер синхронный, вызывающий слой должен
    уводить его в `asyncio.to_thread` (T-012).
- **Конфиг:** четыре опциональные переменные в `config.py`,
  `OPTIONAL_ENV_VARS`/`ALL_ENV_VARS`, `.env.example` и README-таблица
  синхронны; `load_settings` работает без `SPEECH_*`; `Settings.repr`
  скрывает `speech_api_key`.
- **Тесты:** `app/tests/test_speech_adapter.py` — 52 кейса на
  `httpx.MockTransport` (успех, 4xx/5xx, таймаут, сетевой сбой, битый/пустой
  ответ, лимит 60 с, отсутствие конфигурации, невалидные URL/таймаут,
  `repr`, логи); плюс 9 кейсов конфига (4 функции с параметризацией). Всего
  в наборе **893 теста** (в памяти было 828, фактический baseline — 832:
  +52 речи +9 конфига), в обычной песочнице — **893 passed**,
  `ruff check .` — чисто.
- **Документация:** README описывает `SPEECH_*`, лимит 60 секунд,
  приватность и текстовый fallback; `adapters/__init__.py` реэкспортирует
  адаптер и ошибки.

## Чего ещё нет

- **Telegram-сценария голоса.** Нет обработки `message.voice`, скачивания
  аудио, проверки allowlist до распознавания, `to_thread`, FSM-перехода
  «голос → кандидат шаблона», E2E и README-сценария. Это T-012 (прогноз
  M3, `EXECUTE 80–115 + REVIEW 25–40`; факт цикла 12 — M4, 197 шагов).
- **Закрытия долга.** P1 T-004/T-005 открыты (в `LLMAdapter` —
  `InvalidURL`/`RuntimeError`, в `SummaryService` — пустой транскрипт);
  новый P2 T-011 — `InvalidURL` в `SpeechAdapter` и handoff с 741 строкой
  вне diffstat; P3 — `inf/nan` в `_parse_float`, порядок
  «длительность → конфигурация», `media_type`, двойной WARNING и
  `reason_phrase` в логе. Всего **83** пункта/группы долга.
- **Версия:** на момент отчёта `0.15.0`; VERSION цикла 11 поднимет
  `pyproject.toml` и `tldw/__init__.py` до **0.16.0** и добавит одну запись
  CHANGELOG.

## Что это значит для следующей задачи

T-012 должна использовать готовый `SpeechAdapter`, а не писать второй
HTTP-клиент: проверить allowlist **до** скачивания и распознавания,
`is_configured` — до проверки длительности (иначе 61-секундное аудио даст
`SpeechTooLongError` вместо «голос не настроен»), скачивание и
`transcribe` — через `to_thread`, распознанный текст — в тот же путь
«разбор → показ кандидата → Сохранить/Исправить/Отмена», аудио не
сохранять в `DATA_DIR` и не логировать. Ошибка распознавания не должна
менять активный шаблон.

Связанные: `cycle-11-spend.md`, `t011-review-issues.md`,
`tldw-baseline-after-t010.md`, `adr-015-speech-adapter.md`,
`adr-013-telegram-template-flow.md`, `estimate-backlog-label-stale.md`,
`review-debt-accumulates.md`, `app/skills/intake/SKILL.md`.

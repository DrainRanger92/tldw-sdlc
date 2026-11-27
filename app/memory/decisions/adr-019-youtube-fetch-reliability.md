# ADR-019: надёжный YouTube-fetch — один `extract_info`, таймаут сессии и приоритет ошибок

- Статус: принято, уточняет ADR-004
- Дата: 2026-11-27
- Цикл: 14, задача T-014

## Контекст

ADR-004 задал контракт YouTube-адаптера, но оставил четыре P1 из ревью T-003:
у основного источника не было таймаута, fallback делал второй полный
`_extract_info`, приоритет ошибок «видео недоступно»/«нет субтитров» был
неверным, а `yt-dlp` мог писать кэш вне `DATA_DIR`. Плюс битый ответ
`youtube-transcript-api` давал `TypeError` вместо `YouTubeNetworkError`, а
`DownloadError` private/unavailable маскировался под сетевую ошибку. К циклу
14 эти пункты жили в коде десять циклов и были единственным открытым
P1-разделом; T-013 закрыла такой же класс дыр в LLM/речи, но YouTube
остался.

Ограничения: успешный путь, `Video`/`VideoMetadata`/`Transcript`, тексты
`MESSAGE_*` и внешний `YouTubeAdapter.fetch` не должны меняться; новых
зависимостей и обязательных env — нет; тесты офлайновые.

## Решение

1. **Один ответ `yt-dlp` на `fetch`.** Внутренний контракт резервного
   источника меняется: вместо `fetch_transcript`/`fetch_metadata` —
   `FallbackClient.fetch(video_id) -> FallbackVideo`, где
   `FallbackVideo` содержит и `Transcript`, и `VideoMetadata`.
   `YtDlpClient.fetch` делает один `_extract_info`, строит из него оба
   объекта; `YouTubeAdapter._fetch_fallback` забирает метаданные из того же
   результата. `fetch_metadata` остаётся публичным и вызывается только при
   успехе основного источника (там одно извлечение всё равно нужно).
2. **Таймаут основного источника — через сессию.** `youtube-transcript-api`
   1.x не принимает таймаут в `fetch`, но принимает `http_client`; адаптер
   передаёт `_TimeoutSession(requests.Session)`, которая подставляет
   `timeout` в каждый запрос. `DEFAULT_TRANSCRIPT_TIMEOUT = 10.0`;
   `_timeout_seconds` проверяет конечность и положительность; `Timeout` →
   `YouTubeNetworkError`. `requests` — жёсткая зависимость библиотеки, но
   адаптер импортирует её напрямую (см. последствия).
3. **Приоритет ошибок и причина.** `_transcript_error` возвращает пару
   «итоговая ошибка + причина»: `VideoUnavailableError` важнее
   `NoTranscriptError`, он важнее сети. `raise final_error from cause`
   указывает на источник, определивший итог, а не всегда на fallback.
4. **Нормализация чужого ответа.** `_segments_from_fetched` принимает только
   итерацию объектов со строковым `text` и числовыми `start`/`duration`;
   `None`, dict старого формата 0.x, строка, не-итерация и битые числа →
   `YouTubeNetworkError`. Сырые `TypeError`/`AttributeError` наружу не
   выходят.
5. **`DownloadError` различает недоступность.** Маркеры private/unavailable/
   removed/terminated/members-only в тексте → `VideoUnavailableError`,
   остальные → `YouTubeNetworkError`; текст исключения в лог не попадает.
6. **Кэш `yt-dlp` отключён.** `_options` содержит `cachedir: False`; данные
   приложения остаются только в `DATA_DIR`.

## Альтернативы

- **Оставить два вызова `fetch_metadata`/`fetch_transcript`.** Проще, но
  удваивает нагрузку на YouTube и время fallback; P1 остаётся.
- **Обёртка таймаута вокруг `fetch` в отдельном потоке.** Не отменяет
  зависший запрос, только перестаёт ждать; поток остаётся занят.
- **Свой HTTP-клиент для субтитров.** Дублирует разбор ответа библиотеки и
  ломается при смене её внутреннего API.
- **Ловить `DownloadError` целиком как сетевую ошибку.** Пользователь видит
  «сеть» вместо «видео недоступно»; P1 приоритета возвращается.
- **Оставить кэш в `~/.cache`.** Нарушает правило «данные приложения — в
  `DATA_DIR`» и делает тесты зависимыми от домашнего каталога.

## Последствия

- Внутренний контракт `FallbackClient` изменён: `fetch` вместо
  `fetch_transcript`; `FallbackVideo` экспортирован из
  `adapters.youtube`/`tldw.adapters`. Внешний контракт
  `YouTubeAdapter.fetch` не менялся.
- 92 кейса в `tests/test_youtube_adapter.py` (+45), набор — **1056 тестов**;
  проверены матрица 9 сочетаний primary/fallback, `__cause__`, таймаут до
  транспорта (`send(timeout=...)`), один `extract_info` на обоих путях,
  битые ответы, оба класса `DownloadError`, отсутствие записи в кэш.
- Появился прямой импорт `requests` при транзитивной зависимости: это
  **P2** ревью T-014 (`t014-review-issues.md`) — объявить
  `requests>=2,<3` в `pyproject.toml` или зафиксировать транзитивность
  тестом; задача T-018.
- Остались P3: `_segments_from_fetched` ловит только `TypeError` из
  `list(fetched)`; `except Timeout` — мёртвая ветка; `_timeout_seconds`
  дублирует `_seconds`; неполное покрытие `socket_timeout`; мутабельное
  состояние `FakeYoutubeDL.instances` (T-017/T-018).
- **Документация — часть контракта:** ADR-004 уточнён этим ADR, скил
  `offline-tests` переведён с `fetch_transcript` на `fetch`; при следующей
  смене внутреннего контракта обновлять ADR/скилы в том же диффе.
- Тексты `MESSAGE_*`, успешный путь и `Video`-модель не изменились;
  регрессии поведения нет.

Связанные: `adr-004-youtube-adapter-contract.md`, `adr-002-layered-adapters.md`,
`adr-017-typed-error-contract-url-validation.md`,
`app/memory/facts/cycle-14-spend.md`,
`app/memory/facts/t014-debt-audit.md`,
`app/memory/facts/t014-review-issues.md`,
`app/memory/facts/t003-review-issues.md`,
`app/skills/adapter-boundary/SKILL.md`.

---
name: adapter-boundary
description: Как добавить новый внешний HTTP-адаптер в TLDW (YouTube, LLM, распознавание речи) — необязательные env и from_settings, полная валидация URL, типизированные UserFacingError, один запрос и таймаут, приватные логи, офлайновые тесты на httpx.MockTransport; применять при любой новой интеграции и при ревью адаптера.
---

# Граница внешнего HTTP-адаптера

Паттерн повторился трижды: T-003 (YouTube + yt-dlp), T-004 (LLM,
`chat/completions`) и T-011 (распознавание речи,
`audio/transcriptions`). Каждый раз адаптер получался похожим — модуль в
`tldw/adapters/`, настройки из `Settings`, `httpx` только внутри, typed
ошибки, `MockTransport` в тестах, — но каждый раз ревью находило одни и те
же дыры: `httpx.InvalidURL` мимо типизированной ошибки (T-004, T-011),
необязательные env, которые забыли синхронизировать с `.env.example`,
payload/URL в логе, `except Exception` в обработчике, маскирующий отказ.
Скил фиксирует рецепт, чтобы новый адаптер не повторял четвёртую копию.

## Когда применять

- Новая внешняя интеграция: HTTP API, библиотека-обёртка, распознавание
  речи, платёжный/почтовый сервис.
- Ревью любого файла в `app/tldw/adapters/` — пройдите по чек-листу ниже.
- T-012 (голосовой ввод) — использовать готовый `SpeechAdapter`, а не
  писать второй HTTP-клиент.

## Рецепт

1. **Модуль и протокол.** Новый `tldw/adapters/<name>.py`; обработчики и
   сервисы не импортируют `httpx` (ADR-002). Публичный класс/фабрика
   (`from_settings`), `__all__`, реэкспорт в `adapters/__init__.py`.
   Синхронный вызов — вызывающий слой уводит в `asyncio.to_thread`.
2. **Настройки — только необязательные env.** Дефолты в `config.py`;
   `load_settings` работает без переменных; пустой ключ/модель = «не
   настроено», а не `ConfigError`. Обновить `OPTIONAL_ENV_VARS`,
   `ALL_ENV_VARS`, `.env.example`, README-таблицу и тест
   `test_env_example_lists_every_app_variable_without_secrets`.
   `Settings.repr` не должен показывать ключ.
3. **Валидация URL — целиком, а не по префиксу.** `urllib.parse.urlsplit`
   (непустой host, корректный port) или `httpx.URL`; ошибка — `ConfigError`
   на старте. В адаптере — та же проверка в конструкторе. Помнить:
   `httpx.InvalidURL` **не** наследник `httpx.HTTPError`.
4. **Таймаут и число попыток.** `httpx.Timeout(self.timeout)`; таймаут —
   конечное положительное число (проверять `math.isfinite`, не только
   `> 0`). Одна попытка по умолчанию; повторы — только если контракт
   явно идемпотентен и это отдельное решение (T-004: 429/5xx; T-011:
   повторов нет).
5. **Типизированные ошибки.** `<Name>Error(UserFacingError)` + подклассы
   (`NotConfigured`, `TooLong`, `Timeout`, `Provider`, `Response`).
   Сообщения — русские, без внутренних деталей; причина — в лог. Ловить:
   - `httpx.TimeoutException` → timeout;
   - `httpx.InvalidURL` **и** `httpx.HTTPError` → provider/network;
   - `response.status_code >= 400` → provider (не логировать тело);
   - `ValueError`/не-JSON/пустой текст → response.
   Создание клиента (`_make_client`) — внутри `try`, если оно может
   бросить.
6. **Приватность логов.** Не логировать payload, тело ответа, полный текст
   распознавания/транскрипт, URL с query и ключ. Можно: длительность,
   число байт/символов, код ответа, тип исключения. `__repr__` адаптера
   скрывает ключ. Тесты `caplog` проверяют обе стороны: payload **нет**, а
   служебные поля **есть**.
7. **Проверки до сети.** Лимит длительности/размера, `is_configured`,
   пустой вход — до `client.post`; тест утверждает `requests == []`.
   Порядок проверок фиксировать: для T-012 `is_configured` должен идти
   раньше лимита, иначе ненастроенный адаптер ответит «слишком длинное».
8. **Тесты на `httpx.MockTransport`.** Минимум: успех (проверить URL,
   заголовок авторизации, multipart/JSON, таймаут в `request.extensions`),
   4xx/5xx, таймаут, сетевой сбой, битый/пустой ответ, отсутствие
   конфигурации, лимит, невалидный URL, `inf/nan` таймаута, `repr` без
   ключа, логи без payload. 40–60 кейсов — норма (T-011: 52). Сеть и
   реальные ключи не нужны (см. `offline-tests`).
9. **Гейты и документация.** `ruff check .`, полный `pytest` в обычной
   песочнице; README — переменные, лимит, приватность, fallback; CHANGELOG
   и версию ведёт оркестратор (см. `release-metadata`).

## Чек-лист ревью адаптера

- [ ] `httpx` не импортируется вне `adapters/`.
- [ ] Нет новых обязательных env; `.env.example`/`ALL_ENV_VARS`/README
      синхронны; `load_settings` работает без новых переменных.
- [ ] URL валидируется полностью (host/port), а не только префикс.
- [ ] `httpx.InvalidURL` и `HTTPError` обёрнуты в typed error; создание
      клиента внутри `try`.
- [ ] Таймаут конечный и положительный; число попыток соответствует
      контракту.
- [ ] `status >= 400`, битый JSON, пустой текст → разные typed ошибки.
- [ ] Payload/тело/полный текст/ключ не в логе и не в `repr`.
- [ ] Лимит и `is_configured` проверяются до сети; порядок проверок
      осмыслен.
- [ ] Тесты покрывают успех, 4xx/5xx, таймаут, сетевой сбой, битый ответ,
      конфиг, лимит, битый URL, `inf/nan`; `MockTransport`, без `tmp_path`.
- [ ] `__all__`/`adapters/__init__.py`/README-дерево обновлены.
- [ ] `ruff` и полный `pytest` зелёные; в read-only ревьюер может прогнать
      файл без `tmp_path`.

## Анти-паттерны

- `except httpx.HTTPError` без `InvalidURL` — ошибка конфига вылетает
  сырым исключением (T-004 P1, T-011 P2).
- Проверять URL только `startswith("http://")` — пропускает `:bad`,
  пустой host, `https://[`.
- Логировать тело ответа/транскрипт/аудио «для отладки».
- Оборачивать всё в `except Exception` в обработчике — дыра остаётся
  невидимой для E2E.
- Делать ключ/модель обязательными — ломает текстовый сценарий.
- Повторять запрос по умолчанию — стоимость и маскировка ошибок.
- Тестировать только happy path и только валидный `base_url`.
- Импортировать `httpx` в сервисе/обработчике «на минутку».

## См. также

- `app/memory/decisions/adr-004-youtube-adapter-contract.md` — контракт
  YouTube и fallback.
- `app/memory/decisions/adr-005-llm-adapter-contract.md` — повторы,
  JSON-валидация, `LLMError`.
- `app/memory/decisions/adr-015-speech-adapter.md` — решение T-011:
  необязательные env, один запрос, 60 секунд, текстовый fallback.
- `app/memory/facts/invalidurl-escapes-every-adapter.md` — почему
  `InvalidURL` повторяется и как его ловить.
- `app/memory/facts/t011-review-issues.md` — шесть пунктов ревью T-011.
- `app/memory/facts/t004-review-issues.md` — P1 LLM-адаптера.
- `app/skills/offline-tests/SKILL.md` — `MockTransport`, `caplog`,
  read-only-путь.
- `app/skills/shared-helpers/SKILL.md` — общий `_parse_http_url`, лимиты,
  тестовый каркас.
- `app/skills/review-handoff/SKILL.md` — как передать новый адаптер на
  ревью (`git add -N`).

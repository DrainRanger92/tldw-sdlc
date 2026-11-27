# `httpx.InvalidURL` вылетает мимо типизированных ошибок в каждом адаптере

Дата: 2026-11-27. Повторяющийся дефект границы HTTP-адаптеров TLDW:
`except httpx.HTTPError` не ловит `httpx.InvalidURL`, потому что
`InvalidURL` наследуется от `Exception`, а не от `HTTPError`. Конфиг при
этом проверяет только схему URL (`startswith("http://")`), а не host/port.
В результате опечатка в `*_BASE_URL` даёт не типизированную
`UserFacingError`, а сырое исключение httpx — пользователь видит общий текст
или ошибку, а не понятное сообщение.

## Замеры

| Цикл | Адаптер | Где | Что происходит | Статус |
|---|---|---|---|---|
| 4 (T-004) | `adapters/llm.py` | `LLMAdapter` | `httpx.InvalidURL` и `RuntimeError` отсутствующего ресурса выходят мимо `LLMError`; ловится только `HTTPError` | **P1, открыт** |
| 11 (T-011) | `adapters/speech.py` | `SpeechAdapter.transcribe` | `SPEECH_BASE_URL=https://x.test:bad/v1` проходит `load_settings` и конструктор; `transcribe()` поднимает сырой `httpx.InvalidURL`, не `SpeechError`; `_make_client()` вне `try` | **P2, новый** |
| 3 (T-003) | `adapters/youtube.py` | `YoutubeAdapter` | родственный класс: `TypeError` вместо `YouTubeNetworkError` при неверном ответе библиотеки | P1, открыт |

Оба HTTP-адаптера повторяют одну форму: `try: client.post(...) except
httpx.TimeoutException ... except httpx.HTTPError ...`. `InvalidURL`
возникает **до** HTTP-обмена (при разборе URL/порта), поэтому не попадает
ни в один `except`. В `SpeechAdapter` `_make_client()` вообще стоит вне
`try`, так что ошибка конструктора клиента тоже не оборачивается.

## Почему тесты это не ловят

- `test_llm_adapter.py` и `test_speech_adapter.py` используют валидные
  `base_url` и `MockTransport`; `MockTransport` не валидирует порт.
- Тест «неверный `base_url`» есть только на уровне конструктора
  (`ValueError`), а не на уровне `transcribe`/`load_settings`.
- Конфиг-тест проверяет только отсутствие схемы (`speech.example.test/v1`),
  но не `https://host:bad/v1`.
- Поэтому зелёный набор 893 тестов не означает, что опечатка в env даст
  `SpeechError`/`LLMError`.

## Правило для следующего адаптера

1. **Валидировать URL целиком в конфиге:** `urllib.parse.urlsplit`,
   непустой host, корректный port (или `httpx.URL` с проверкой), а не
   только префикс. Ошибка — `ConfigError` на старте, а не в момент
   запроса.
2. **Ловить `httpx.InvalidURL` явно** рядом с `HTTPError` (и оборачивать
   создание клиента, если оно может бросить). Для `SpeechAdapter` —
   `except (httpx.InvalidURL, httpx.HTTPError)`.
3. **Добавить тест на битый URL в каждом адаптере:** `https://host:bad/v1`,
   `https://[`, пустой host; ожидать типизированную ошибку, а не
   `ValueError`/`InvalidURL`.
4. **Не полагаться на `except Exception` в обработчике:** он маскирует
   ошибку общим текстом и скрывает дыру от E2E (так было с P1 T-004 до
   T-008).
5. **Проверять `repr` и логи:** `InvalidURL` может содержать URL с ключом
   в query; в лог — только тип исключения, без текста.

Это повторяющийся паттерн (2 адаптера, 2 цикла), поэтому рецепт вынесен в
скил `app/skills/adapter-boundary/SKILL.md`. Пока P1 T-004 не закрыт,
следующий адаптер (например, голосовой T-012) получит ту же дыру, если не
добавить `InvalidURL` в шаблон.

Связанные: `t004-review-issues.md`, `t011-review-issues.md`,
`t003-review-issues.md`, `review-debt-accumulates.md`,
`cycle-11-spend.md`, `adr-004-youtube-adapter-contract.md`,
`adr-005-llm-adapter-contract.md`, `adr-015-speech-adapter.md`,
`app/skills/adapter-boundary/SKILL.md`.

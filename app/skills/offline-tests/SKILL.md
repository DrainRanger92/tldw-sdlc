---
name: offline-tests
description: Как писать тесты TLDW без сети и токенов — фиктивный env, tmp_path для DATA_DIR, фейковые клиенты внешних библиотек, фейковые адаптеры и httpx.MockTransport; так, чтобы гейты pytest и ruff проходили офлайн.
---

# Офлайновые тесты TLDW

Паттерн взят из T-001 (конфиг и сборка), повторён в T-002 (модель заметки),
T-003 (YouTube-адаптер), T-004 (LLM-адаптер), T-005 (сервис конспекта) и
T-006 (Telegram-обработчик). В T-003 он дал 47 новых тестов без сети,
токенов и `tmp_path`; в T-004 — 49 новых тестов (48 адаптера плюс тест
упаковки); в T-005 — 45 новых кейсов, из которых часть пишет файлы в
`tmp_path`; в T-006 — 21 кейс на фейковой сессии aiogram (13 без `tmp_path`).
Всего в наборе **282 теста**.

## Когда применять

Любой тест в `app/tests/`, если код:
- читает конфиг из env;
- собирает aiogram-приложение;
- ходит в Telegram, YouTube или LLM;
- пишет файлы заметок/кэша.

## Базовый каркас

- `tests/conftest.py` даёт фикстуру `fake_env` с валидными, но фиктивными
  `TELEGRAM_BOT_TOKEN`, `LLM_API_KEY`, `LLM_MODEL` и `DATA_DIR` внутри
  `tmp_path`.
- Настройки собираются явным словарём: `load_settings(fake_env)`. Не
  подменяйте `os.environ`, если можно передать `environ` аргументом.
- `DATA_DIR` всегда указывает в `tmp_path` (или его подкаталог). Тест не
  должен писать в настоящий `app/data`.
- Приложение собирается через `build_application(settings)`; блокирующий
  `Application.run()` подменяется параметром `run_application` в `tldw.cli.main`.
- Для проверки приоритета env над `.env` используйте `tmp_path`-файл и
  `monkeypatch`/явный словарь, а не реальный `app/.env`.

## Внешние вызовы

- Telegram: фейковая **сессия** `BaseSession` + настоящий `Dispatcher`; не
  поднимайте polling. Рецепт — в разделе «Telegram-слой» ниже.
- LLM: `httpx.MockTransport` с заранее заданными ответами — успех, битый JSON,
  HTTP 500, таймаут, 401; проверяйте, что ошибки превращаются в `LLMError`.
  В T-004 дополнительно передаются `transport=` и `sleep=` в конструктор,
  поэтому повтор на 429/5xx проверяется без реального сна.
- YouTube: фейковые клиенты адаптера; отдельные кейсы «нет субтитров»,
  «не YouTube», сбой основного источника и срабатывание fallback.
- Никаких `time.sleep`, реальных сетевых адресов и токенов из окружения.

## Telegram-слой: фейковая сессия и `feed_update`

T-006 проверяет обработчик без сети, подменяя не `Message`, а сессию бота:

```python
class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests = []

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)   # SendMessage/SendDocument
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def close(self) -> None: ...
```

- `Bot(token="123456789:TEST-fake-token-not-a-secret", session=FakeSession())`
  — токен фиктивный, в env ничего не нужно;
- апдейт собирается `Update.model_validate({...})` с `chat`/`from`/`text`;
- сообщение прогоняется через настоящий `Dispatcher.feed_update(bot, update)`:
  проверяются фильтр `F.text`, регистрация роутера и порядок ответов;
- записанные методы дают текст и `document.filename`/`document.path`; файл
  можно проверить на диске **в момент** отправки через `on_request`-колбэк;
- обработчик отвечает «принял» и уходит в `create_task`, поэтому тест обязан
  дождаться фоновой задачи (`await handler.wait_idle()`), иначе увидит только
  первый ответ;
- «loop не заблокирован» проверяется `threading.get_ident()` и
  `threading.Event` в фейковом сервисе, а не `time.sleep`: жёсткие таймауты
  дают флаки (ревью T-006 это отметило);
- фейковый сервис реализует узкий протокол `SummaryProcessor`, а не
  `SummaryService`; один интеграционный кейс использует настоящий сервис и
  `tmp_path` для `DATA_DIR`.

## Ресурсы пакета и промпт

T-004 хранит промпт и шаблон в `tldw/adapters/resources/*.md`. В тестах:

- не читайте файлы по абсолютному пути репозитория — используйте
  `importlib.resources`/`load_summary_prompt()`, иначе editable-установка и
  собранный пакет дадут разное поведение;
- `build_system_prompt()` кэширован через `lru_cache`; если тест меняет
  ресурс, очищайте кэш (`build_system_prompt.cache_clear()`) или проверяйте
  ресурс до первого вызова;
- проверяйте, что `{{NOTE_TEMPLATE}}` действительно подставлен, а не остался
  маркером;
- тест упаковки должен смотреть не только на строку `package-data` в
  `pyproject.toml`, но и на фактический состав установленного пакета — в
  editable-режиме строка может быть, а ресурса в wheel не будет.

## Фейковые клиенты вместо библиотек

Адаптер T-003 держит внешние библиотеки за узкими протоколами
(`TranscriptClient`, `FallbackClient`). В тестах их заменяют dataclass-фейки,
которые записывают вызовы и умеют возвращать заданную ошибку:

```python
@dataclass
class FakeTranscriptClient:
    transcript: Transcript | None = None
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def fetch(self, video_id: str) -> Transcript:
        self.calls.append(video_id)
        if self.error is not None:
            raise self.error
        assert self.transcript is not None
        return self.transcript
```

- `FakeFallbackClient` отдельно управляет метаданными и транскриптом — так
  проверяются «метаданные необязательны» и fallback независимо.
- Для клиента-обёртки над библиотекой полезен отдельный `FakeTranscriptApi`:
  он повторяет только сигнатуру `fetch(video_id, languages=...)` и позволяет
  проверить маппинг исключений без установки сети.
- Фейк должен реализовывать протокол, а не наследоваться от реального класса:
  иначе тест потянет импорт тяжёлой библиотеки.

## Тесты сервиса поверх фейковых адаптеров

T-005 проверяет сценарий «ссылка → `.md`» без сети, подставляя в
`SummaryService` фейки протоколов `VideoSource`/`SummaryProvider`:

- фейк источника возвращает готовый `Video` (метаданные + `Transcript`) или
  кидает типизированную ошибку адаптера; фейк суммаризатора — заданный
  `VideoSummary` или `LLMError`;
- порог длительности проверяется тремя кейсами: больше порога →
  `VideoTooLongError` **и** ни LLM, ни файла (проверяйте `not
  data_dir.exists()`), ровно на пороге — успех, `duration is None` — не
  блокирует;
- `source` в заметке сверяйте с исходной ссылкой пользователя, а не с тем,
  что вернул фейк адаптера;
- файловую часть проверяйте через `data_dir=tmp_path`: атомарная запись,
  отказ на `../`, на имя без `.md` и на каталог вместо файла;
- чистые функции (`join_transcript`, `build_note`) тестируйте прямыми
  вызовами — это даёт покрытие усечения и таймкодов даже там, где `tmp_path`
  недоступен (read-only песочница ревьюера);
- тест «транскрипт склеен с таймкодами» проверяйте на самом промпте
  (перехваченный аргумент фейка суммаризатора), а не на внутренних
  переменных.

## Не сцепляйтесь с приватными именами

В T-003 тесты импортировали `_parse_json3` и monkeypatch’или
`_extract_info`/`_read_caption`. Ревьюер отметил это как хрупкость: любая
перестановка приватных помощников ломает тесты. Предпочтительно:

- проверять публичное поведение (`parse_video_id`, `YouTubeAdapter.fetch`,
  `YtDlpClient.fetch_transcript`) через фейки;
- если чистый парсер нужен снаружи — сделать его публичным осознанно, а не
  импортировать `_name`;
- monkeypatch приватного метода допустим только как временный компромисс и
  должен быть назван в отчёте.

## Версии библиотек

Тесты T-003 импортируют `FetchedTranscript`, `FetchedTranscriptSnippet`,
`RequestBlocked`, `TranscriptsDisabled`, `VideoUnavailable` из
`youtube-transcript-api 1.2.5`. Это сознательная привязка к версии: при
обновлении библиотеки тесты нужно править вместе с клиентом. Версия
зафиксирована в `pyproject.toml` (`>=1.2,<2`), переход на 2.x — отдельная
задача.

T-004 использует только публичный `httpx` (`Client`, `MockTransport`,
`Timeout`, `HTTPError`, `InvalidURL`) и не импортирует внутренности
провайдера; версия — `httpx>=0.27,<1`. Помните, что `httpx.InvalidURL` не
наследуется от `HTTPError` — тест на битый `base_url` должен ожидать
`LLMError`, а не «любую ошибку httpx».

## Обязательные проверки

```bash
cd app
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Тесты должны проходить на чистом окружении без `TELEGRAM_BOT_TOKEN`
и `LLM_API_KEY` в env. Если тест требует реальный секрет — он сломан.
Тесты без файловых операций (как `test_youtube_adapter.py` и
`test_llm_adapter.py`) не используют `tmp_path` и потому проходят даже в
read-only песочнице ревьюера. После T-006 полный набор — **282 теста**;
файлы адаптеров проходят в read-only целиком, а `test_summary_service.py`,
`test_storage_notes.py` и часть `test_link_handler.py` — только частично:
в цикле 6 полный прогон дал 198 passed + 84 env-errors, из нового файла
прошли 13 кейсов из 21. Поэтому сценарные кейсы, которым нужен `tmp_path`,
оставляйте гейту VERIFY, а в read-only проверяйте чистые функции и
не-файловые ветки.

## Анти-паттерны

- Читать настоящий `app/.env` в тесте.
- Оставлять `data/` после теста: только `tmp_path`.
- Мокать `httpx` глобально вместо `MockTransport`/фейкового клиента.
- Проверять только happy path: для каждого адаптера нужны сетевые ошибки,
  таймауты и битые ответы.
- Наследовать фейк от реального клиента библиотеки или импортировать её
  приватные модули.
- Подменять `Message` целиком, когда можно подменить сессию: теряются фильтры
  `F.text` и роутинг, которые как раз надо проверить.
- Ждать фоновую задачу через `sleep` или жёсткий таймаут вместо
  `wait_idle()`/`threading.Event`: это флаки на загруженном CI.

## См. также

- `app/skills/review-handoff/SKILL.md` — что ревьюер в read-only песочнице
  может прогнать, а что падает из-за недоступного `tmp_path`.
- `app/memory/facts/t003-review-issues.md` — какие кейсы добавить в тесты
  адаптера (маппинг ошибок yt-dlp, `languages`, `parse_video_id(None)`).
- `app/memory/facts/t005-review-issues.md` — какие кейсы сервиса остались
  непокрытыми (пустой транскрипт, вырожденный лимит усечения).
- `app/memory/facts/aiogram-offline-handler-tests.md` — полный разбор
  фейковой сессии, `feed_update`, `wait_idle` и проверки `to_thread`.
- `app/memory/facts/t006-review-issues.md` — что ревью T-006 нашло в тестах
  (флаки-таймауты, типы тест-дублей) и в обработчике.

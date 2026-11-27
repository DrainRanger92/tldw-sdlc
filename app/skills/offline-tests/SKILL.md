---
name: offline-tests
description: Как писать тесты TLDW без сети и токенов — фиктивный env, tmp_path для DATA_DIR, фейковые клиенты внешних библиотек, фейковые адаптеры и httpx.MockTransport, фейковая сессия aiogram, тесты ошибок и очистки логов; так, чтобы гейты pytest и ruff проходили офлайн.
---

# Офлайновые тесты TLDW

Паттерн взят из T-001 (конфиг и сборка), повторён в T-002 (модель заметки),
T-003 (YouTube-адаптер), T-004 (LLM-адаптер), T-005 (сервис конспекта),
T-006 (Telegram-обработчик), T-007 (политика доступа), T-008 (ошибки и
логи), T-009 (сквозной E2E и README), T-010 (шаблонный сценарий, 457 новых
кейсов за цикл), T-011 (адаптер распознавания речи, 52 кейса), T-012
(голосовой обработчик 15 кейсов + 5 сквозных) и T-014 (92 кейса
YouTube-адаптера, +45). В T-003 он дал 47 новых тестов без сети, токенов и `tmp_path`; в
T-004 — 49 новых тестов (48 адаптера плюс тест упаковки); в T-005 — 45 новых
кейсов, из которых часть пишет файлы в `tmp_path`; в T-006 — 21 кейс на
фейковой сессии aiogram (13 без `tmp_path`); в T-007 — 27 кейсов, включая 15
чистых тестов политики без aiogram и файлов; в T-008 — 34 кейса (26 в новом
`test_errors.py` + 9 сквозных в `test_link_handler.py` минус один
переписанный); в T-009 — 28 нетто (5 сквозных в `test_e2e.py` + 23 проверки
README, минус переписанные кейсы обработчика); в цикле 10 — **457 нетто**
(десять новых файлов, из них `test_template_handler.py` 30 сценариев,
`test_template_e2e.py` 9 кейсов, `test_template_errors.py`, `test_wiring.py`);
в T-011 — **52 кейса** `test_speech_adapter.py` на `MockTransport` и 9
кейсов конфига (4 функции с параметризацией); в T-012 — **20 нетто**
(`test_voice_handler.py` 15 + `test_voice_e2e.py` 5) и +14 в существующих
файлах; в T-013 — **84 нетто** в четырёх файлах (`test_llm_adapter.py` 68
кейсов, `test_speech_adapter.py` 80, плюс конфиг и сервис), новых файлов
нет; в T-014 — **45 нетто** в одном файле `test_youtube_adapter.py`
(92 кейса, было 47); в T-015 — **25 нетто** в 13 существующих файлах,
новых файлов нет (заголовки-ловушки в `test_llm_template.py`, DEBUG/WARNING
в `test_storage_templates.py`, сквозной рендер в `test_summary_templates.py`,
симметрия голоса/текста в `test_voice_handler.py`). Всего в наборе
**1081 тест** в 28 файлах.

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
- Распознавание речи: `httpx.MockTransport`, который записывает запрос и
  отдаёт `{"text": ...}`; проверяйте multipart (`files`/`data`), заголовок
  `Authorization`, `httpx.Timeout` в `request.extensions`, одну попытку
  (`requests == []` до сети при лимите/без конфига) и `caplog` — аудио и
  текст распознавания не в логе. T-011: 52 кейса без `tmp_path`, файл
  целиком проходит в read-only. Для обработчика (T-012) адаптер заменяется
  `FakeSpeechTranscriber` из `support.py` — см. раздел «Голосовой ввод».
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

## Сквозной E2E и общий каркас

T-009 добавила `tests/test_e2e.py`: сообщение проходит через **настоящие**
`Dispatcher` → `LinkHandler` → `SummaryService` → `storage`, подменены только
внешние границы. Рецепт:

- фейки — только `FakeSession` (Telegram), `FakeVideoSource` (YouTube) и
  `FakeSummarizer` (LLM); сервис, рендер заметки и запись файла настоящие;
- общий каркас — `tests/support.py`: `Harness`, `make_harness`,
  `run_message`, `request_types`, фейки адаптеров, `make_video`,
  `make_summary`, фиктивные секреты. **Не заводите второй `Harness` в новом
  файле** — ревью T-009 записало это как P2 (≈100 дублированных строк);
- `run_message` доводит фоновую задачу через `wait_idle`, собирает методы
  `SendMessage`/`SendDocument` и позволяет проверить файл **в момент**
  отправки (`on_request`);
- проверяйте наблюдаемый результат: порядок методов, тексты `MESSAGE_*`,
  frontmatter (7 полей в порядке), 5 секций, имя-slug, `source`, отсутствие
  `.md`/`.tmp` в `DATA_DIR` у ошибок;
- **E2E требует `tmp_path`**, поэтому в read-only песочнице ревьюера он не
  прогоняется. Если нужен read-only-путь, проверяйте рендер и чистые
  функции отдельно;
- ошибки гоняйте тем же путём (чужой ID, нет субтитров, длинное видео,
  `LLMError`) — так проверяется, что обработчик действительно доводит
  типизированное исключение до текста, а не только что сервис его кинул.

## Сценарий шаблона: FSM, in-memory репозиторий и `build_application`

T-007/T-010 добавили третий тип сценария — диалог с состоянием. Рецепт,
который работает офлайн:

- FSM — `MemoryStorage`, состояния `waiting_description` →
  `confirming_candidate` → `confirming_reset`; в тестах не нужно чистить
  диск между кейсами;
- кандидат хранится в FSM примитивами (`sections`, `tone`), а не моделью:
  тест на перезапуск — новый `build_application` с тем же `DATA_DIR`, старый
  `MemoryStorage` потерян; активный шаблон при этом сохраняется;
- репозиторий шаблонов подменяется in-memory реализацией протокола
  `TemplateRepository` (`load`/`save`/`delete`), настоящий `TemplateStore`
  проверяется отдельно на `tmp_path` (roundtrip, битый JSON, атомарность);
- **сквозной E2E — через `build_application(settings)`**: настоящие
  `Dispatcher`, `TemplateHandler`, `LinkHandler`, `TemplateService`,
  `SummaryService`, `storage`; фейки только на границах Telegram/YouTube/LLM
  (`FakeSession`, `FakeVideoSource`, `FakeSummarizer` из `support.py`).
  `run_message`/`on_request` позволяют проверить `.md` в момент отправки;
- негативные кейсы: ссылка во время `waiting_description` не вызывает
  YouTube/LLM; устаревшая кнопка не сохраняет дважды; ошибка записи
  оставляет встроенный шаблон и не создаёт `.md`; ошибка разбора не меняет
  активный шаблон;
- **не заводите второй `Harness`/`FakeSession`** — T-010 переиспользовал
  `tests/support.py`; второй каркас был P2 цикла 9.

В read-only песочнице ревьюера FSM-сценарии без `tmp_path` проходят
(в цикле 10 — 30 тестов `test_template_handler.py`), а E2E с `tmp_path` —
нет; это учитывать в отчёте.

## Голосовой ввод: аудио в `FakeSession` и `FakeSpeechTranscriber`

T-012 проверил медиа-путь без сети и файлов:

- `FakeSession(audio=...)` отвечает на `GetFile` настоящим
  `aiogram.types.File` (`file_path="voice/fake.oga"`), а `stream_content`
  отдаёт байты; так проверяется реальный `bot.download(destination=BytesIO)`
  без Telegram. Аудио — не пустое, чтобы `BytesIO` был непустым;
- `FakeSpeechTranscriber` структурно реализует протокол
  `SpeechTranscriber`: `text`, `error`, `configured`, `calls` (байты +
  длительность) и `thread_ids`. Проверяйте:
  - `calls == []`, если allowlist/состояние/`is_configured`/лимит
    отсекли сообщение **до** распознавания;
  - `calls[0][1] == float(voice.duration)` — длительность доходит до
    адаптера;
  - `thread_ids[0] != threading.get_ident()` — `asyncio.to_thread`, а не
    блокировка loop;
  - `error` → сообщение `MESSAGE_VOICE_RETRY`/`report.user_message`,
    `state` сброшен в `waiting_description`, активный шаблон не изменён;
- голосовой апдейт собирается как обычный `Update.model_validate` с
  `message.voice = {file_id, file_unique_id, duration, mime_type}`; кнопки
  и тексты проверяются тем же `AppHarness`, что и текст;
- **E2E с голосом пишет `DATA_DIR`** (`test_voice_e2e.py`), поэтому в
  read-only не исполняется — как и остальные E2E; unit-файл
  `test_voice_handler.py` (15 кейсов) проходит целиком;
- не дублируйте `get_file_requests`/`AUDIO`/`VOICE_TEXT` в двух файлах:
  место общих помощников — `tests/support.py` (P3 T-012).

## README под тестами

`tests/test_readme.py` держит документацию от расхождения с кодом:

- команды установки/запуска/гейтов — параметризованно;
- каждая переменная из `ALL_ENV_VARS` присутствует в таблице README;
- ручной сценарий содержит YouTube-ссылку и точные тексты
  `MESSAGE_ACCEPTED`/`MESSAGE_PROCESSING`/`MESSAGE_DONE`;
- сканер секретов по `app/**/*.py` и README с allow-list известных фиктивных
  значений, проверенный **мутацией**;
- не цепляйтесь за слова («frontmatter», «секций») — сверяйте константы
  `FRONTMATTER_KEYS`, `SECTION_HEADINGS`, `MESSAGE_*`, иначе тест ломается
  от переформулировки, а не от регресса (P3 T-009).

## Чистые правила без aiogram

T-007 показал, что проверку доступа не нужно тащить в Telegram-сценарий:
`AccessPolicy` — frozen dataclass с двумя методами, поэтому 15 кейсов
(`test_access.py`) проверяют пустой/заполненный список, постороннего, `None`,
дедупликацию и описание без единого `Update`, `FakeSession` или `tmp_path`.
Такой файл целиком проходит в read-only песочнице ревьюера — это лучший
первый прогон для задач с политикой/валидацией.

- Отделяйте **правило** от **доставки**: `AccessPolicy.is_allowed()` тестируйте
  напрямую, а порядок «проверка до разбора ссылки» — одним кейсом на
  `FakeSession` с `service.calls == []`.
- Проверку «до вызовов адаптеров» формулируйте как наблюдаемое следствие
  (сервис не вызван), а не как чтение исходника.
- Предупреждение при старте проверяйте через `caplog` с фильтром по
  `record.name`, а не по всем WARNING (иначе тест ломается от чужого лога —
  см. `t007-review-issues.md`).

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

## Тесты ошибок и очистки логов

T-008 добавила `tests/test_errors.py` — чистый модуль без aiogram и файлов,
поэтому он проходит в read-only целиком. Устройство, которое стоит
повторять:

- **Тестируйте слой целиком, а не обработчик:** `describe_error(exc)` и
  `log_failure(exc)` проверяются напрямую; сквозные кейсы через `FakeSession`
  нужны только чтобы подтвердить, что обработчик до них дошёл.
- **Разделяйте уровни лога:** известная ошибка — `WARNING` с причиной без
  стектрейса; неизвестная — `ERROR` со стектрейсом. Проверяйте
  `record.levelno`, `record.getMessage()` и `record.name` (в цикле 8 —
  `tldw.handlers.link`), а не «в логе что-то есть».
- **Не пишите в тесте настоящий секрет:** фиктивные токены должны быть
  явно фейковыми, но **формы** — как в коде.
- **Проверяйте мутацией:** временно уберите шаблон/ветку — тест обязан
  упасть. Тест очистки, который остаётся зелёным без проверяемого шаблона,
  бесполезен: именно так 27 зелёных тестов пропустили утечку
  `Authorization: Bearer`.
- **Параметризуйте формы входа:** заголовок (`Authorization: Bearer …`),
  свободный текст (`bearer …`), repr словаря, `Basic`/`Token`, env-строка,
  Telegram-токен `<id>:<secret>`, `sk-…`, длинный транскрипт с отступами и
  без; отдельный кейс — «похожий, но не секрет» (`max_tokens=4096`).
- **Не переиспользуйте одну фикстуру на несколько шаблонов:** значение с
  префиксом `sk-` маскирует проверку `Bearer`.
- **Слишком длинное сообщение известной ошибки** — отдельный кейс: оно должно
  откатываться к общему тексту, а не уходить пользователю целиком.

## Не сцепляйтесь с приватными именами

В T-003 тесты импортировали `_parse_json3` и monkeypatch’или
`_extract_info`/`_read_caption`. Ревьюер отметил это как хрупкость: любая
перестановка приватных помощников ломает тесты. Предпочтительно:

- проверять публичное поведение (`parse_video_id`, `YouTubeAdapter.fetch`,
  `YtDlpClient.fetch`) через фейки; `FallbackClient.fetch` возвращает
  `FallbackVideo` — фейк должен отдавать и транскрипт, и метаданные;
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

## YouTube-адаптер: фейковый `YoutubeDL`, таймаут-сессия и один `extract_info`

T-014 расширила офлайновый путь для адаптера, который не на httpx:

- **Подменяйте библиотечную границу, а не приватные методы.** Тесты
  `YtDlpClient` подменяют `tldw.adapters.youtube.YoutubeDL` фейком и
  проверяют публичные `fetch`/`fetch_metadata`; monkeypatch `_extract_info`/
  `_read_caption` убран (P3 T-003). `FakeYoutubeDL.instances` — список
  созданных экземпляров с `options`/`extract_calls`; **не держите его
  классовым мутабельным состоянием**, сбрасывайте фикстурой, иначе кейсы
  протекут друг в друга (P3 T-014).
- **Один `extract_info` проверяйте счётчиком:** сумма `extract_calls == 1`
  и на fallback-пути, и при успехе primary (там `fetch_metadata` делает одно
  извлечение, а не второе); `metadata_calls == []` после fallback.
- **Таймаут до транспорта:** `TranscriptApiClient` принимает
  `_TimeoutSession`; тест подсовывает записывающий адаптер и проверяет, что
  в `send(timeout=...)` пришло заданное число. Значения `0/−1/inf/nan/str/
  bool/None` — `TypeError`/`ValueError`; для `socket_timeout` набор уже
  (P3 T-014 — добавить `None`/`bool`/отрицательное).
- **Битый ответ библиотеки** (`None`, dict 0.x, строка, не-итерация, битые
  числа) → `YouTubeNetworkError`, а не `TypeError`; `list(fetched)` может
  бросить не только `TypeError` — ловите шире (P3 T-014).
- **Матрица приоритетов:** 9 сочетаний primary/fallback проверяют итоговую
  ошибку, а `__cause__` — что причина указывает на источник, определивший
  итог. Это дешевле, чем проверять по одному сценарию.
- **«Кэш ничего не пишет»** — единственный кейс с `tmp_path`: подменить
  `XDG_CACHE_HOME`/`HOME` на `tmp_path` и вызвать настоящий
  `YoutubeDL(options).cache.store(...)`; в read-only он не исполняется,
  поэтому доказательство дублируется ручной проверкой `Cache.enabled is
  False` на ревью.
- `requests` — транзитивная зависимость библиотеки; прямой импорт в адаптере
  требует строки в `pyproject.toml` (P2 T-014). Тест упаковки стоит
  расширить, чтобы ловить такие импорты, а не только `package-data`.

## Обязательные проверки

```bash
cd app
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Тесты должны проходить на чистом окружении без `TELEGRAM_BOT_TOKEN`
и `LLM_API_KEY` в env. Если тест требует реальный секрет — он сломан.
Тесты без файловых операций (как `test_youtube_adapter.py`,
`test_llm_adapter.py`, `test_access.py`, `test_errors.py` и `test_readme.py`)
не используют `tmp_path` и потому проходят даже в read-only песочнице
ревьюера. После T-015 полный набор — **1081 тест в 28 файлах**; новых
файлов нет — 25 кейсов добавлены в существующие, включая файловые
`test_storage_templates.py`/`test_summary_templates.py` и сценарный
`test_voice_handler.py`. Файлы адаптеров (включая `test_speech_adapter.py` —
80 кейсов, `test_llm_adapter.py` — 68, `test_youtube_adapter.py` — 92),
`test_access.py`, `test_errors.py`, `test_readme.py`, `test_template.py`,
`test_template_parser.py`, `test_template_handler.py` (30 сценариев FSM) и
`test_voice_handler.py` (сценарии FSM) проходят в read-only целиком или
почти целиком, а `test_storage_templates.py`, `test_summary_templates.py`,
`test_template_e2e.py`, `test_voice_e2e.py`, `test_summary_service.py`,
`test_storage_notes.py`, `test_e2e.py` и файловая часть
`test_link_handler.py` — только частично: в
цикле 7 полный прогон дал 34 passed + 9 env-errors в двух файлах и 5 passed +
52 env-errors в `test_config`/`test_bootstrap`/`test_cli`, в цикле 8 — 243
passed + 93 env-errors из 343, в цикле 9 полный прогон недоступен, в цикле
10 — 158 passed из 828 (19 %), а оба E2E-файла и хранение шаблонов не
исполнялись, **в цикле 11 — 685 passed + 208 env-errors из 893 (77 %)**,
`test_speech_adapter.py` прошёл целиком, **в цикле 12 — 703 passed + 224
env-errors из 927 (76 %)**, `test_voice_handler.py` 15/15, `test_readme.py`
41/41, `test_voice_e2e.py` не исполнялся; **в цикле 13 — 753 passed + 258
env-errors из 1011 (74,5 %)**, `test_llm_adapter.py` 68/68 и
`test_speech_adapter.py` 80/80, а новые кейсы `test_config`/
`test_summary_service` с `tmp_path` — нет; **в цикле 14 — 797 passed + 259
env-errors из 1056 (75,5 %)**, `test_youtube_adapter.py` — 91 из 92, «кэш
ничего не пишет» не исполнялся; **в цикле 15 — 0 из 1081**: read-only
сессия не нашла writable temp-каталог, pytest упал до сбора, `ruff` прошёл
дважды, зелёность принята по арифметике 1056 + 25, LEARN прогнал 1081.
Поэтому сценарные кейсы, которым
нужен `tmp_path`, оставляйте гейту VERIFY, а в read-only проверяйте чистые
функции и не-файловые ветки.

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
- `app/memory/facts/t011-review-issues.md` — 52 кейса `SpeechAdapter`,
  `MockTransport`, multipart и приватность логов.
- `app/memory/facts/cycle-12-spend.md` — 20 новых кейсов T-012, 927 тестов,
  `FakeSession` с аудио и E2E, который не идёт в read-only.
- `app/memory/facts/cycle-14-spend.md` — 92 кейса `test_youtube_adapter.py`,
  таймаут-сессия, один `extract_info`, 1056 тестов.
- `app/memory/facts/cycle-15-spend.md` — 25 новых кейсов в 13 файлах,
  заголовки-ловушки, DEBUG/WARNING, сквозной рендер, 1081 тест.
- `app/memory/facts/reviewer-readonly-tests.md` — почему в цикле 15
  ревьюер не смог прогнать ни одного теста и как это компенсировал LEARN.
- `app/memory/facts/t014-review-issues.md` — P3 тестов T-014: узкий
  `except TypeError`, `FakeYoutubeDL.instances`, неполный `socket_timeout`.
- `app/memory/decisions/adr-016-voice-input-flow.md` — голосовой путь и его
  проверки.
- `app/memory/decisions/adr-015-speech-adapter.md` — контракт адаптера речи.
- `app/memory/facts/t006-review-issues.md` — что ревью T-006 нашло в тестах
  (флаки-таймауты, типы тест-дублей) и в обработчике.
- `app/memory/facts/t007-review-issues.md` — хрупкие проверки логов и
  дубль дедупликации ID, которые ревью нашло в тестах T-007.
- `app/memory/facts/secret-redaction-tests.md` — как 27 зелёных тестов
  пропустили утечку `Authorization: Bearer` и обход очистки отступом.
- `app/memory/facts/t009-review-issues.md` — дублирование каркаса и хрупкие
  проверки README, которые нашло ревью E2E.
- `app/memory/decisions/adr-009-user-facing-errors.md` — контракт
  `errors.py`, который эти тесты проверяют.
- `app/memory/decisions/adr-010-e2e-test-surface.md` — решение о границе
  «настоящий код / фейк» и общем каркасе.

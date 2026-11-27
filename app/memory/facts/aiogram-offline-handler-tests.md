# Офлайновые тесты aiogram-обработчика: фейковая сессия и Dispatcher.feed_update

Дата: 2026-11-26. Урок T-006: сценарий Telegram впервые проверялся без сети и
без плагинов pytest-aiogram. Паттерн один цикл, поэтому это факт, а не скил;
в T-008/T-009 он наверняка повторится и тогда его место — в
`app/skills/offline-tests/SKILL.md` (там уже есть раздел про внешние вызовы).

## Что сработало

Фейковая **сессия** aiogram вместо фейкового `Message`:

```python
from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendDocument, SendMessage
from aiogram.types import Update

class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests = []

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)   # SendMessage/SendDocument как есть
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def close(self) -> None: ...
```

- `Bot(token="123456789:TEST-fake-token-not-a-secret", session=FakeSession())` —
  токен фиктивный, сеть не нужна;
- апдейт собирается через `Update.model_validate({...})` с `chat`/`from`/
  `text`, а не через конструкторы типов;
- сообщение прогоняется через настоящий `Dispatcher`:
  `await dispatcher.feed_update(bot, update)` — проверяются фильтры `F.text`,
  регистрация роутера и порядок ответов;
- записанные методы (`SendMessage`, `SendDocument`) дают и текст, и
  `document.filename`/`document.path`; можно проверить, что файл существовал
  **в момент** отправки (через `on_request`-колбэк сессии).

## Ловушки

- **Фоновая задача завершается после `feed_update`.** Обработчик отвечает
  «принял» и уходит в `create_task`; без ожидания тест увидит только первый
  ответ. Нужен `await handler.wait_idle()` (шов из `LinkHandler`) или
  `asyncio.gather` по задачам. Это же — причина, почему `wait_idle` живёт в
  продакшн-классе, а не в тесте.
- **Проверка «не блокирует loop» требует событий, а не секунд.** Фейковый
  сервис ставит `threading.Event` при входе и ждёт `release`; тест проверяет
  `threading.get_ident()` (не поток loop) и живость loop, пока поток
  заблокирован. Ревью flagged жёсткие `timeout=1.0` и поллинг 100×0.01 с как
  флаки-риск — надёжнее `await asyncio.to_thread(started.wait)`.
- **`tmp_path` нужен только файловым кейсам.** Успешный сценарий с настоящим
  `SummaryService` пишет `.md` в `DATA_DIR=tmp_path`; остальные кейсы идут на
  фейковом сервисе и `tmp_path` не требуют — именно поэтому 13 из 21 кейса
  прошли у ревьюера в read-only песочнице.
- **Не подменяйте `Message` целиком, если можно подменить сессию.** Фейковый
  `Message` не проверяет фильтры и роутинг; `feed_update` проверяет.
- **Фейковый сервис — узкий протокол `SummaryProcessor`**, а не
  `SummaryService`: тест не тянет адаптеры и конфиг.

## Цифры

- 21 кейс в `tests/test_link_handler.py`, 282 теста в наборе (было 257);
- ревьюер в read-only: 198 passed + 84 env-errors, новый файл — 13/13
  не-`tmp_path` кейсов; полный зелёный прогон — только в песочнице
  разработчика (см. `reviewer-readonly-tests.md`);
- цикл стоил 62 шага EXECUTE, из них 43,5 % вызовов — до первой записи кода,
  включая точечную разведку aiogram через `grep`/`sed`.

## Когда применять

- Любой новый `handlers/*.py` (T-007 allowlist, T-008 сообщения об ошибках).
- E2E T-009: тот же `FakeSession` + `Dispatcher.feed_update` плюс фейковые
  адаптеры YouTube/LLM и настоящий `SummaryService` на `tmp_path` — это
  ближайший способ собрать сквозной сценарий без сети.

## Цикл 10: FSM-сценарий шаблона на том же каркасе

T-007 добавил третий тип обработчика — диалог с состоянием, и каркас
`FakeSession` + `Dispatcher.feed_update` выдержал без изменений:

- FSM — `MemoryStorage` с тремя состояниями (`waiting_description`,
  `confirming_candidate`, `confirming_reset`); в тестах состояние между
  кейсами не протекает, потому что каждый кейс строит свой `Dispatcher`;
- кандидат хранится в FSM примитивами (`sections`, `tone`), поэтому тест на
  перезапуск — это новый `build_application` с тем же `DATA_DIR`, а не
  пересериализация модели;
- `test_template_handler.py` — 30 сценариев (полный путь «Изменить →
  описание → кандидат → Сохранить», сброс с подтверждением, «Закрыть»,
  «Исправить», устаревшая кнопка, ссылка во время ожидания описания, чужой
  ID, отсутствие секретов в ответах);
- 30 из них проходят в read-only песочнице ревьюера (нет `tmp_path`);
  `test_template_e2e.py` (9 кейсов через `build_application`) требует
  `tmp_path` и в read-only не исполняется — см. `reviewer-readonly-tests.md`;
- полный набор после T-010 — **828 тестов**, из них в цикле 10 ревьюер
  прогнал 158 (19 %).

Правило то же: подменять сессию, а не `Message`; тяжёлый разбор уводить в
`asyncio.to_thread` и проверять `threading.get_ident`; для FSM использовать
настоящий `Dispatcher` с `MemoryStorage`, чтобы проверить фильтры состояний и
порядок роутеров (`template` раньше `link`).

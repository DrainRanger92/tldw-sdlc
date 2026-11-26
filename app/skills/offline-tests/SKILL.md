---
name: offline-tests
description: Как писать тесты TLDW без сети и токенов — фиктивный env, tmp_path для DATA_DIR, фейковые клиенты внешних библиотек, фейковые адаптеры и httpx.MockTransport; так, чтобы гейты pytest и ruff проходили офлайн.
---

# Офлайновые тесты TLDW

Паттерн взят из T-001 (конфиг и сборка), повторён в T-002 (модель заметки) и
T-003 (YouTube-адаптер). В T-003 он дал 47 новых тестов без сети, токенов и
`tmp_path`; всего в наборе 163 теста.

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

- Telegram: мок aiogram-бота/`Message`; не поднимайте polling.
- LLM: `httpx.MockTransport` с заранее заданными ответами — успех, битый JSON,
  HTTP 500, таймаут; проверяйте, что ошибки превращаются в `LLMError`.
- YouTube: фейковые клиенты адаптера; отдельные кейсы «нет субтитров»,
  «не YouTube», сбой основного источника и срабатывание fallback.
- Никаких `time.sleep`, реальных сетевых адресов и токенов из окружения.

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

## Обязательные проверки

```bash
cd app
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Тесты должны проходить на чистом окружении без `TELEGRAM_BOT_TOKEN`
и `LLM_API_KEY` в env. Если тест требует реальный секрет — он сломан.
Тесты без файловых операций (как `test_youtube_adapter.py`) не используют
`tmp_path` и потому проходят даже в read-only песочнице ревьюера.

## Анти-паттерны

- Читать настоящий `app/.env` в тесте.
- Оставлять `data/` после теста: только `tmp_path`.
- Мокать `httpx` глобально вместо `MockTransport`/фейкового клиента.
- Проверять только happy path: для каждого адаптера нужны сетевые ошибки,
  таймауты и битые ответы.
- Наследовать фейк от реального клиента библиотеки или импортировать её
  приватные модули.

## См. также

- `app/skills/review-handoff/SKILL.md` — что ревьюер в read-only песочнице
  может прогнать, а что падает из-за недоступного `tmp_path`.
- `app/memory/facts/t003-review-issues.md` — какие кейсы добавить в тесты
  адаптера (маппинг ошибок yt-dlp, `languages`, `parse_video_id(None)`).

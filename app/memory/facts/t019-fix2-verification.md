# Независимая проверка FIX-2 T-016 (задача T-019)

Дата: 2026-11-27. Задача T-019 проверяла правки FIX-2 T-016, оставшиеся без
ревью из-за `max_review_iterations = 2`. Проверка — воспроизведением и
тестами, а не по отчёту: каждая правка прочитана в текущем коде (HEAD `0cee540`),
ключевые — исполнены и мутированы.

## Итог

- Прочитаны и проверены **все 29 фрагментов** FIX-2 (журнал FIX-2-сессии
  `session-853b3383-8f52-4c29-bfa0-24e836227f76`: 29 вызовов `edit`) **плюс
  3 пакетные правки скриптом** (переименование `log_denial`, две аннотации) —
  32 изменения в 12 файлах. Непроверенных пунктов нет, таблица ниже.
- **Найден один регресс FIX-2 и исправлен:** хвост `[A-Za-z0-9_]*` в
  `_SECRET_PATTERNS` начал скрывать `tokenizer=…`/`TOKENIZERS_PARALLELISM=…`,
  хотя скил `app/skills/log-sanitization` требует оставлять `tokenizer` видимым.
  Исправление — `TOKEN(?!S|IZER)` + тест-ловушка в обе стороны.
- Остальные правки FIX-2 корректны: регрессов редактора секретов с
  суффиксами, подмены `EXIT_FAILURE`, дедупликации и уровней лога нет.
- Тестов стало **1151** (было 1144; +7 новых), `pytest -q` и
  `ruff check .` в `app/` зелёные офлайн.
- Защищённые пути и тексты по умолчанию не тронуты; новых задач не заведено.

## Состав FIX-2 и как он восстановлен

`git log` не даёт диффа FIX-2: EXECUTE, FIX-1 и FIX-2 вошли в один коммит
`0cee540`. Состав восстановлен из журнала FIX-2-сессии DSH
(`.aidevteam/dsh-home/sessions/--workspace--/session-853b3383-…/session.v4.jsonl.zstd`,
95 шагов, 102 вызова, многокадровый zstd — декодер из
`skills/cycle-accounting`). Сверка с HEAD: все правки FIX-2 присутствуют
(`git show HEAD:app/tldw/cli.py | grep _close_application` и т. п.); рабочая
копия отличалась от HEAD только правками T-019 и изменениями `specs/`,
сделанными оркестратором.

Файлы FIX-2: `tldw/errors.py`, `tldw/cli.py`, `tldw/handlers/link.py`,
`tldw/handlers/template.py`, `tldw/access.py`, `tests/test_errors.py`,
`tests/test_cli.py`, `tests/test_link_handler.py`, `tests/test_access.py`,
`tests/test_logging_setup.py`, `tests/test_summary_service.py`,
`memory/facts/t016-debt-audit.md`.

## Таблица «правка → проверка (тест/команда) → результат»

Ссылки на тесты — `tests/…`, если не указано иное. «Мутация» — временная
порча кода с последующим восстановлением по sha256 (список — в разделе
«Мутации»).

Соответствие 29 вызовам `edit` из журнала FIX-2 (строка таблицы ← вызовы):
1 ← 1; 3, 4 ← 3, 4 (`tests/test_errors.py`); 5 ← 2 (`_visible_level`);
6–8 ← 5–7 (`cli.py`); 9 и 12 ← 8–11 (`handlers/link.py`: докстроки,
`_DedupeKey`, `_message_chat_id`); 17 ← 12–15 (`tests/test_link_handler.py`);
16 ← 16–21 (`tests/test_cli.py`); 18 ← 22–29 (аудит-файл T-016).
Скриптовые правки: ключ/типы/слот `link.py` — строки 9–11, переименование
`log_denial` — строка 13, две аннотации — строки 14–15.

| # | Правка FIX-2 | Проверка | Результат |
| --- | --- | --- | --- |
| 1 | `errors.py`: в первый шаблон возвращён хвост `[A-Za-z0-9_]*` после ключевого слова, `TOKEN(?!S)` сохранён | `pytest tests/test_errors.py -k env_secret` (11 ловушек); probe `sanitize_log_text` в обе стороны; мутация M1 (старый шаблон) | **PASS**: `SECRET_KEY`, `MY_SECRET_KEY`, `AWS_SECRET_ACCESS_KEY`, `PASSWORD_HASH`, `ACCESS_TOKEN_HASH`, `TELEGRAM_BOT_TOKEN` скрываются; M1 валит 5 параметров |
| 2 | `errors.py`: исключение токенизатора (найдено T-019) — `TOKEN(?!S)` → `TOKEN(?!S|IZER)` | `test_tokenizer_names_are_not_mistaken_for_a_secret`; мутация N1 (возврат `TOKEN(?!S)`) | **PASS** после исправления; N1 валит новый тест (до исправления `tokenizer=cl100k_base` → `tokenizer=[скрыто]`) |
| 3 | `tests/test_errors.py`: 6 ловушек с суффиксом в параметрах | Те же тесты; M1 | **PASS** |
| 4 | `tests/test_errors.py`: `input_tokens` в тесте `max_tokens`; T-019 добавил `MAX_TOKENS` | `test_max_tokens_is_not_mistaken_for_a_secret`; мутация M2 (`TOKEN(?!S)`→`TOKEN`) | **PASS**; M2 валит тест (`max_tokens=[скрыто]`) |
| 5 | `errors.py`: docstring `_visible_level` — принятый компромисс | `test_known_error_stays_visible_at_error_log_level`, `test_unknown_error_at_critical_threshold_is_not_lost`, новый `test_known_error_is_distinguishable_from_incident_at_error_level`; probe уровней; мутация M6 | **PASS**: при пороге ERROR известная ошибка видима, отличима текстом («Сбой …» без стека против «Неизвестный сбой …»); M6 (не поднимать уровень) валит два теста |
| 6 | `cli.py`: `_close_application` возвращает причину, не выпуская исключение | `test_close_failure_on_run_path_does_not_escape_main`, `test_close_failure_does_not_replace_exit_failure`; прямой probe `_close_application`; мутация M5 | **PASS**: `main()` возвращает код, `WARNING` в логе, `RuntimeError` наружу не выходит; M5 (старый `finally`) валит оба теста |
| 7 | `cli.py`: `--check-config` при сбое закрытия — `WARNING`, stderr и `EXIT_FAILURE` | `test_check_config_reports_close_failure_without_secret` (уровень `WARNING`, код `EXIT_FAILURE`, токен не утёк); мутация `log.warning→log.error` | **PASS**; мутация валит тест (уровень проверяется по-настоящему) |
| 8 | `cli.py`: сбой закрытия в `finally` не подменяет выбранный код возврата | Оба run-path теста; `test_close_failure_does_not_replace_exit_failure` — runner падает, close падает, код остаётся `EXIT_FAILURE` | **PASS** |
| 9 | `handlers/link.py`: ключ `(chat_id, user_id, video_id or url)`, `_message_chat_id`, типы `_DedupeKey`, docstring | `test_same_link_in_another_chat_is_not_deduplicated`; probe ключа; мутация M3 (ключ без чата) | **PASS**: в другом чате — два вызова LLM и два «Готово»; M3 валит тест |
| 10 | `handlers/link.py`: `video_id`/URL в ключе (T-019 закрепил тестом) | Новый `test_deduplication_key_uses_video_id_or_url` (2 параметра: разные query → один `video_id`; не-YouTube URL → сам URL); мутация N2 (ключ по сырому URL) | **PASS**; N2 валит параметр «same-video-id-different-query» |
| 11 | `handlers/link.py`: слот `_inflight` резервируется до первого `await` (FIX-1, перепроверено) | `test_concurrent_duplicate_links_are_deduplicated`; мутация M4 (`await asyncio.sleep(0)` перед `_start`) | **PASS**: один вызов LLM, одно «уже обрабатывается»; M4 даёт два вызова и валит тест |
| 12 | `handlers/link.py`: docstring `MAX_CONCURRENT_LINKS`/`max_concurrent` — семафор только вокруг `process`, очередь не ограничена | `test_concurrency_limit_serialises_different_links`; дифф FIX-2 содержит только докстроки и типы (поведение не менялось) | **PASS** (документация; поведение прежнее) |
| 13 | `access.py`: `DenialLogLimiter.log` → `log_denial`; обновлены `link.py` и `template.py` | `test_denial_log_limiter_writes_only_the_first_warning`, `::does_not_grow_without_bound`, новый `::does_not_shadow_module_log`; `grep -rn "denials\.log(" tldw tests` — пусто; мутация N3 | **PASS**: метод не затеняет модульный `log`; N3 валит тест |
| 14 | `tests/test_logging_setup.py`: `_restore_logging` → `Iterator[None]` + импорт `Iterator` | Новый `test_restore_logging_fixture_is_annotated_as_iterator`; мутация N4 (`-> None`) | **PASS**; N4 валит тест |
| 15 | `tests/test_summary_service.py`: `import logging` на уровень модуля | `pytest tests/test_summary_service.py`; `grep -n "import logging"` — строка 12, локального импорта нет | **PASS** |
| 16 | `tests/test_cli.py`: два теста сбоя закрытия (+чистка импортов) | Тесты #6–8; мутации M5 и `warning→error` | **PASS** |
| 17 | `tests/test_link_handler.py`: `_Message.chat`, тест другого чата, докстринги | Тесты #9, #11; M3/M4 | **PASS** |
| 18 | `memory/facts/t016-debt-audit.md`: 8 правок (убрана ссылка на `_answer_denied`, точные гейты, строки дедупликации/редактора, FIX-2) | Раздел «Проверка утверждений аудит-файла» | **PASS** с одной оговоркой (read-only песочница) |

## Редактор секретов: обе стороны (исполнение, не чтение)

`python -c`/heredoc с `sanitize_log_text` на текущем коде; значения фиктивные:

| Вход | Ожидание | Результат |
| --- | --- | --- |
| `SECRET_KEY=topsecret` | скрыть | `SECRET_KEY=[скрыто]` |
| `MY_SECRET_KEY=…` | скрыть | `[скрыто]` |
| `AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI` | скрыть | `[скрыто]` |
| `PASSWORD_HASH=$2b$12$…` | скрыть | `[скрыто]` |
| `ACCESS_TOKEN_HASH=zzz` | скрыть | `[скрыто]` |
| `TELEGRAM_BOT_TOKEN=123456789:bot-secret-value` | скрыть | `[скрыто]` |
| `llm_api_key=…`, `db_password=…`, `bot_token=…`, `MY_AUTHORIZATION=…`, `my_api_key=…` | скрыть | `[скрыто]` |
| `max_tokens=4096` | оставить | без изменений |
| `MAX_TOKENS=4096` | оставить | без изменений |
| `MAX_OUTPUT_TOKENS=4096` | оставить | без изменений |
| `input_tokens=17`, `tokens=4096` | оставить | без изменений |
| `tokenizer=cl100k_base` | оставить | **до T-019** → `tokenizer=[скрыто]`; **после** — без изменений |
| `TOKENIZERS_PARALLELISM=false` | оставить | **до T-019** → `[скрыто]`; **после** — без изменений |
| `https://api.telegram.org/bot123456789:AAH…/sendMessage` | секрет не в логе | URL сведён к `https://api.telegram.org` (маркер `[скрыто]` не нужен — путь вырезан) |

Сравнение с шаблоном FIX-1 на корпусе из 23 форм: текущий шаблон скрывает
**всё, что скрывал FIX-1**, и дополнительно 5 имён с суффиксом. Ни одна форма
из списка «должны остаться» (`max_tokens`, `MAX_TOKENS`, `MAX_OUTPUT_TOKENS`,
`input_tokens`, `tokens`, `tokenizer`) не скрывается.

## Мутации (ловушки проверяют шаблон, а не удобную форму)

Каждая мутация применялась к файлу, прогонялся целевой тест, файл
восстанавливался и сверялся по sha256.

| Мутация | Ожидание | Факт |
| --- | --- | --- |
| M1: первый шаблон без хвоста (состояние FIX-1) | падают ловушки с суффиксом | 5 параметров `test_env_secret_names_are_redacted_case_insensitively` FAILED (`SECRET_KEY`, `MY_SECRET_KEY`, `AWS_SECRET_ACCESS_KEY`, `PASSWORD_HASH`, `ACCESS_TOKEN_HASH`) |
| M2: `TOKEN(?!S)` → `TOKEN` | падает `max_tokens` | `test_max_tokens_is_not_mistaken_for_a_secret` FAILED (`max_tokens=[скрыто]`) |
| N1: `TOKEN(?!S|IZER)` → `TOKEN(?!S)` | падает токенизатор | `test_tokenizer_names_are_not_mistaken_for_a_secret` FAILED |
| M3: ключ дедупликации без чата | падает «другой чат» | `test_same_link_in_another_chat_is_not_deduplicated` FAILED |
| M4: `await asyncio.sleep(0)` перед `_start` | падает конкурентный тест | `test_concurrent_duplicate_links_are_deduplicated` FAILED (2 вызова LLM) |
| N2: ключ по сырому URL вместо `video_id` | падает параметр с разными query | `test_deduplication_key_uses_video_id_or_url[same-video-id-different-query]` FAILED |
| M5: старый `finally` (`asyncio.run(close())` без защиты) | падают тесты закрытия | оба run-path теста FAILED (`RuntimeError` из `main`) |
| `log.warning` → `log.error` в `cli.py` | падает проверка уровня | 3 теста закрытия FAILED |
| M6: `_visible_level` не поднимает уровень | падают тесты видимости | 2 теста FAILED |
| N3: `log_denial` → `log` | падает тест имени | `test_denial_log_limiter_does_not_shadow_module_log` FAILED |
| N4: `_restore_logging -> None` | падает тест аннотации | `test_restore_logging_fixture_is_annotated_as_iterator` FAILED |

## Дедупликация: ключ, чат и `video_id`/URL

`python -` probe (без сети, объекты `SimpleNamespace`):

```text
video_id(https://www.youtube.com/watch?v=dQw4w9WgXcQ)            -> dQw4w9WgXcQ
video_id(https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=private) -> dQw4w9WgXcQ
video_id(https://example.com/lecture/1)                          -> None
key(chat=100, user=42, video_id)   -> (100, 42, 'dQw4w9WgXcQ')
key(chat=100, user=42, не-YouTube) -> (100, 42, 'https://example.com/lecture/1')
```

- Конкурентные одинаковые ссылки → один вызов LLM (`test_concurrent_duplicate_links_are_deduplicated`,
  мутация M4).
- Та же ссылка в другом чате → не дубль (`test_same_link_in_another_chat_is_not_deduplicated`,
  мутация M3).
- Разные query того же ролика → дубль; не-YouTube URL → ключ URL
  (`test_deduplication_key_uses_video_id_or_url`, мутация N2).
- Резерв слота — синхронный блок `handle_message` без `await` до `_start`;
  семафор по-прежнему только вокруг `process` (`test_concurrency_limit_serialises_different_links`).

## `_visible_level`, `log_denial`, `_restore_logging`

- `_visible_level` probe: порог DEBUG/WARNING → `WARNING`; порог ERROR →
  `ERROR`; порог CRITICAL → `CRITICAL` (не выше). Компромисс задокументирован
  в docstring и `ADR-022`: по уровню известная ошибка неотличима от инцидента
  при `LOG_LEVEL=ERROR`, но отличима по тексту — новый тест
  `test_known_error_is_distinguishable_from_incident_at_error_level` фиксирует
  обе стороны; `report.level` остаётся `WARNING`.
- `log_denial`: имя метода и два места вызова (`link.py:389`,
  `template.py:811`) проверены `grep`; модульный `log = logging.getLogger(...)`
  не затенён — тест `test_denial_log_limiter_does_not_shadow_module_log`,
  мутация N3.
- `_restore_logging`: `Iterator[None]` и импорт `collections.abc.Iterator`;
  тест `test_restore_logging_fixture_is_annotated_as_iterator` (через
  `__wrapped__` — работает на pytest 8/9), мутация N4.

## Проверка утверждений аудит-файла T-016

| Утверждение `t016-debt-audit.md` | Проверка | Результат |
| --- | --- | --- |
| Гейты FIX-2: `.venv/bin/python -m pytest` → `1144 passed` | `pytest --deselect` семи новых тестов T-019 → `1144 passed, 7 deselected` | **подтверждено** |
| `ruff check .` → `All checks passed!` | Точный прогон в `app/` | **подтверждено** |
| В `link.py` есть `_answer`, ссылки на `_answer_denied` нет | `grep -n "def _answer\b"` (строка 374); `grep _answer_denied tldw` — пусто | **подтверждено** |
| `TemplateHandler._deny`/`_deny_callback` | `grep` — строки 782/790 | **подтверждено** |
| Ключ дедупликации `(chat_id, user_id, video_id or url)` | Код `link.py:230` + тесты #9–10 | **подтверждено** |
| `test_max_tokens…` расширен `input_tokens`; ловушки с суффиксом в параметрах | `tests/test_errors.py` | **подтверждено** |
| `_restore_logging` — `Iterator[None]`; `import logging` в `test_summary_service` на уровне модуля | Тесты/`grep` | **подтверждено** |
| «Ревьюер в read-only песочнице не может прогнать pytest» | Это ограничение **его** окружения; в этой (writable) сессии тесты исполнены. Оговорка относится к песочнице ревьюера, а не к коду | **подтверждено** (оговорка об окружении) |
| «Закрыто 8 замечаний из 10» | Замечания 5 (лимит) и 6 (дубли) в «Передано» → T-017, остальные 8 закрыты правками | **подтверждено** |
| «FIX-2 добавил 9 тестов: 1135 → 1144» | Арифметика сходится: 1144 − 9 = 1135; в FIX-2 вошли 6 параметров-ловушек, 1 тест другого чата и 2 CLI-теста | **подтверждено** |

## Найдено и исправлено в T-019

**Регресс FIX-2: ложное срабатывание на токенизаторе.** Хвост
`[A-Za-z0-9_]*` после `TOKEN` доводил совпадение `tokenizer=cl100k_base` до
`=`, и значение скрывалось; до FIX-2 имя должно было заканчиваться ключевым
словом, поэтому регресс появился именно в FIX-2. Скил `log-sanitization`
прямо требует оставлять `tokenizer` видимым. Исправление:

- `tldw/errors.py::_SECRET_PATTERNS`: `TOKEN(?!S)` → `TOKEN(?!S|IZER)`
  (исключение по аналогии с `*_TOKENS`: это не секрет, а имя токенизатора);
  комментарий дополнен.
- `tests/test_errors.py::test_tokenizer_names_are_not_mistaken_for_a_secret`
  (`tokenizer`, `TOKENIZERS_PARALLELISM`, `Tokenizer:`) + мутация N1.
- `tests/test_errors.py::test_max_tokens_is_not_mistaken_for_a_secret`
  расширен `MAX_TOKENS` (критерий T-019).

Новых возможностей не добавлено: правило сужено одной формой, публичные
тексты и сообщения не менялись.

## Гейты (точные команды, `app/`, окружение `app/.venv`)

```text
$ .venv/bin/python -m pytest -q
(exit code 0; сводка подавлена двойным -q: addopts=-q в pyproject + -q в команде)

$ .venv/bin/python -m pytest -o addopts="" -q
...
1151 passed in ~4.0s

$ .venv/bin/python -m pytest        # один -q из addopts — сводка видна
1151 passed

$ .venv/bin/python -m pytest -o addopts="" -q \
    --deselect tests/test_errors.py::test_tokenizer_names_are_not_mistaken_for_a_secret \
    --deselect tests/test_errors.py::test_known_error_is_distinguishable_from_incident_at_error_level \
    --deselect tests/test_cli.py::test_close_failure_does_not_replace_exit_failure \
    --deselect tests/test_link_handler.py::test_deduplication_key_uses_video_id_or_url \
    --deselect tests/test_access.py::test_denial_log_limiter_does_not_shadow_module_log \
    --deselect tests/test_logging_setup.py::test_restore_logging_fixture_is_annotated_as_iterator
1144 passed, 7 deselected        # база FIX-2 воспроизведена

$ .venv/bin/ruff check .
All checks passed!
```

База 1144 не уменьшилась: 1151 = 1144 + 7 новых тестов (tokenizer,
различимость известной ошибки, `EXIT_FAILURE` при сбое закрытия, два параметра
ключа дедупликации, имя `log_denial`, аннотация фикстуры).

## Границы, процесс и остаточные риски

- Вне `app/` изменений нет. `git status --porcelain -uall -- orchestrator dsh
  docker aidevteam.toml aidevteam` — пусто. Изменения в `specs/`
  (`plan.md`, `spec.md`, `tasks.md`, удаление `inbox/D-001`) — оркестратора,
  не разработчика (`facts/review-spec-edit-artifact.md`); статусы задач не
  менялись.
- Дифф T-019 в `app/` (без этого файла): 6 файлов, +227/−12; продакшн —
  `tldw/errors.py` (+8/−4), 5 тестовых файлов (+219/−8). Этот файл
  (untracked) добавлен `git add -N`. Публичные тексты `MESSAGE_*`,
  `.env.example`, README, версия и CHANGELOG не тронуты.
- **Остаточный риск (не регресс по контракту):** расширенный хвост скрывает и
  теоретические не-секреты вида `token_count=17`; в коде/логах проекта такой
  формы нет, документированный список «должны остаться» она не нарушает.
  Правило сознательно не расширялось дальше `IZER`, чтобы не плодить
  исключения без потребности.
- `_close_application` ловит `Exception`, а не `BaseException`: `KeyboardInterrupt`
  и `CancelledError` не гасятся намеренно (гасить отмену/прерывание хуже, чем
  показать её). Реальный `Application.close()` таких исключений не поднимает:
  задачи отменяются с `return_exceptions=True`, сессия закрывается штатно.
- Процесс: один git-вызов упал (забыл обёртку `git_ws` в составной команде с
  подавленным stderr); рабочая копия восстановлена по бэкапам и сверена
  sha256. Урок `skills/git-in-sandbox` подтверждён: обёртка нужна каждому
  вызову, включая вложенные.

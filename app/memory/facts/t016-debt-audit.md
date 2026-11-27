# Аудит долга T-016: логи, доступ и жизненный цикл

Дата: 2026-11-27. Задача T-016 закрывала P2/P3 из `t006-review-issues.md`,
`t007-review-issues.md`, `t008-review-issues.md` и `t001-t010-review-issues.md`
по темам «централизованная очистка логов, fail-open, отказ в доступе,
границы ссылки/лога, жизненный цикл фоновых задач, безопасный `video_id`,
`ExceptionGroup`, `LOG_LEVEL=ERROR`, `max_tokens`, порядок `ensure_data_dir`,
единый источник allowlist и синхронный файловый ввод-вывод».

Таблица ниже — доказательство по текущему коду и тестам. Пункты, которые не
являются кодом (handoff, границы диффа, read-only песочница, исторический
CHANGELOG), закрыты как процессные и **не превращались в правки кода**.
Пункты вне поверхности T-016 переданы в T-017/T-018 с указанием причины.

## Сводка

- **Закрыто в T-016:** 20 пунктов (4 P2, остальные P3).
- **Устарело/закрыто раньше:** 11 пунктов (T-009, T-012, T-013, T-015).
- **Процессные, без правки кода:** 4 пункта.
- **Передано:** 6 пунктов (T-017 — помощники/лимиты; T-018 — тесты/упаковка).
- **FIX после REVIEW-2:** закрыто 8 замечаний из 10 (редактор секретов с
  хвостом имени + тесты-ловушки, защита `close()` на остановке, чат в ключе
  дедупликации, документация лимита параллелизма и семантики
  `_visible_level`, три мелочи, точность этого файла); дубли помощников и
  лимит в конфиге осознанно оставлены для T-017. Гейты после FIX по ревью:
  `.venv/bin/python -m pytest` — **1144 passed**; `.venv/bin/ruff check .` —
  `All checks passed!`. Это вывод обычной (writable) песочницы; ревьюер в
  read-only песочнице pytest запустить не может
  (`facts/reviewer-readonly-tests.md`), поэтому его «0 тестов» — ограничение
  окружения, а не красный гейт.

## Основная таблица

| Пункт | Источник | Статус | Доказательство |
| --- | --- | --- | --- |
| `cli.py` пишет `log.exception` в обход `sanitize_log_text` | T-008 P2 | **исправлено** | `tldw/logging_setup.py`: `SanitizingFilter` ставится в `configure_logging` на root и его обработчики; `tldw/cli.py` дополнительно очищает текст исключения в `print(..., stderr)`. Тесты: `tests/test_logging_setup.py::test_cli_build_failure_redacts_secrets_in_log_and_stderr`, `::test_cli_stop_failure_redacts_secrets_in_log`, `::test_third_party_logger_does_not_write_secrets_or_full_transcript`. |
| Секреты и полный транскрипт у сторонних логгеров | T-008 P2 | **исправлено** | `SanitizingFilter.filter` очищает `record.msg`, `record.args`, `record.exc_text` и `stack_info`; многострочный стектрейс сохраняет строки, но обрезается по длине (`sanitize_log_block`). Тест: `tests/test_logging_setup.py::test_multiline_third_party_transcript_is_collapsed`. |
| Молчаливый fail-open: забытый `allowed_user_ids` | T-007 P2 | **исправлено** | `tldw/access.py`: `AccessPolicy.open()` и `MISSING_ALLOWLIST_WARNING`; `LinkHandler`/`TemplateHandler` при `None` пишут предупреждение и создают явную открытую политику; `create_router` предупреждает один раз и передаёт решённый кортеж дальше. Пустой кортеж остаётся документированным «доступ всем». Тесты: `tests/test_access.py::test_missing_allowlist_warns_but_keeps_mvp_access`, `tests/test_wiring.py::test_create_router_without_allowlist_warns_but_keeps_mvp_access`, `::test_create_router_with_explicit_empty_allowlist_does_not_warn`. |
| Отказ «доступ закрыт» не обёрнут от исключения aiogram | T-007 P2 | **исправлено** | `tldw/handlers/link.py`: `_answer` (единственная обёртка ответа; прежняя ссылка на `_answer_denied` в этом файле была ошибкой — REVIEW-2); `tldw/handlers/template.py`: `_deny`/`_deny_callback`. Сбой ответа гасится `log_failure` и не уходит в aiogram. Тест: `tests/test_link_handler.py::test_access_denied_reply_does_not_raise_aiogram_error`. |
| Повторные отказы заливают лог `WARNING` | T-007 P2 | **исправлено** | `tldw/access.py`: `DenialLogLimiter` — первый отказ пользователя `WARNING`, повторы `DEBUG`; память ограничена `MAX_TRACKED_DENIALS` (FIFO-вытеснение). Тесты: `tests/test_access.py::test_denial_log_limiter_writes_only_the_first_warning`, `::test_denial_log_limiter_does_not_grow_without_bound`, `tests/test_link_handler.py::test_repeated_denials_do_not_flood_warning_log`. |
| Нет левой границы `_LINK_RE`, ловится `notyoutube.com` | T-006 P3 | **исправлено** | `tldw/handlers/link.py`: обе альтернативы начинаются с `(?<![\w.-])` — учитываются `_` и не-ASCII буквы. Тест: `tests/test_link_handler.py::test_link_regex_has_left_boundary` (`notyoutube.com`, `not_youtube.com`, `foo_youtu.be`, `текстyoutube.com` → `None`). |
| Полный URL попадает в лог ошибки | T-006 P3 | **исправлено** | `tldw/handlers/link.py::_failure_context`: только безопасный `video_id` или host; `tldw/errors.py` вырезает путь/query/фрагмент URL из `str(exc)` (остаётся `схема://host`), поэтому полный URL не попадает в лог и через причину исключения. `tldw/text.py`: `video_id_from_url`/`safe_url_host`. Тесты: `test_error_log_contains_safe_video_id_without_query`, `test_error_log_for_other_domain_contains_only_host`, `test_success_log_contains_video_id` (все `tests/test_link_handler.py`), `tests/test_errors.py::test_url_in_exception_is_reduced_to_host_without_query`. |
| `Application.close()` не отменяет фоновые задачи | T-006 P3 | **исправлено** | `tldw/bootstrap.py::Application.close` вызывает `LinkHandler.aclose()` до `bot.session.close()`; `tldw/handlers/link.py::aclose` отменяет и дожидается задачи; `tldw/cli.py::main` закрывает приложение в `finally` и после штатного `application.run()`. `find_link_handler` проводки — `tldw/handlers/router.py`. Тесты: `tests/test_bootstrap.py::test_application_close_cancels_background_tasks` (после закрытия нет `MESSAGE_DONE`), `tests/test_cli.py::test_default_run_path_closes_application`. |
| Нет лимита параллелизма и дедупликации ссылок | T-006 P3 | **исправлено** | `LinkHandler`: `MAX_CONCURRENT_LINKS`/`max_concurrent` + `asyncio.Semaphore`; `_inflight[(chat_id, user_id, video_id)]` резервируется **до первого `await`**, поэтому два конкурентных апдейта не запускают LLM дважды, ответ `MESSAGE_ALREADY_PROCESSING`. Чат в ключе с FIX REVIEW-2: та же ссылка в другом чате — другая доставка заметки, а не дубль. Тесты: `tests/test_link_handler.py::test_repeated_link_is_deduplicated_and_llm_called_once`, `::test_concurrent_duplicate_links_are_deduplicated` (на старом коде — 2 вызова LLM), `::test_same_link_in_another_chat_is_not_deduplicated` (на старом ключе — «уже обрабатывается» и один вызов), `::test_concurrency_limit_serialises_different_links`. |
| `format_traceback` схлопывает `ExceptionGroup` | T-008 P3 | **исправлено** | `tldw/errors.py`: `format_traceback` различает `BaseExceptionGroup`, строит дерево `_format_exception_group` с вложенными стектрейсами; `MAX_EXCEPTION_GROUP_DEPTH`. Тесты: `tests/test_errors.py::test_traceback_preserves_exception_group`, `::test_traceback_preserves_nested_exception_groups`. |
| `format_traceback` теряет длинные кадры (>2 строк исходника) | T-008 P3 | **исправлено** | `_format_single_traceback` сохраняет **все** подряд идущие строки с отступом после `File "…"`, а не две. Тест: `tests/test_errors.py::test_traceback_keeps_all_lines_of_a_long_frame` (синтетический кадр из трёх строк). |
| В логе нет безопасного `video_id` | T-008 P3 | **исправлено** | `tldw/services/summary.py` пишет `video_id` после `fetch` и в «Конспект готов»; обработчик — `video_id`/host в контексте ошибки и в «Заметка отправлена». `tldw/text.py::video_id_from_url` — один разбор для адаптера и лога. Тесты: `tests/test_summary_service.py::test_service_logs_safe_video_id`, `tests/test_link_handler.py::test_error_log_contains_safe_video_id_without_query`. |
| Известные ошибки не видны при `LOG_LEVEL=ERROR` | T-008 P3 | **исправлено** | `tldw/errors.py::_visible_level`: уровень записи поднимается до действующего порога логгера (WARNING → ERROR/CRITICAL), текст и отсутствие стектрейса сохраняются; `ErrorReport.level` остаётся WARNING. Тесты: `tests/test_errors.py::test_known_error_stays_visible_at_error_log_level`, `::test_unknown_error_at_critical_threshold_is_not_lost`. |
| Ложное срабатывание редактора на `max_tokens=4096` | T-008 P3 | **исправлено** | `tldw/errors.py::_SECRET_PATTERNS`: env-имена распознаются без учёта регистра и включают `_`, а исключаются только счётчики `*_TOKENS`; заголовки/ключи — по границе слова. Поэтому `max_tokens`/`tokens`/`input_tokens` не скрываются, а `llm_api_key`, `db_password`, `bot_token`, `MY_AUTHORIZATION` — скрываются. FIX REVIEW-2 вернул хвост `[A-Za-z0-9_]*` после ключевого слова: без него проходили имена с суффиксом (`SECRET_KEY`, `MY_SECRET_KEY`, `AWS_SECRET_ACCESS_KEY`, `PASSWORD_HASH`, `ACCESS_TOKEN_HASH`). Тесты: `tests/test_errors.py::test_max_tokens_is_not_mistaken_for_a_secret`, `::test_secret_names_still_redacted_after_false_positive_fix`, `::test_env_secret_names_are_redacted_case_insensitively` (в параметрах — ловушки с суффиксом). |
| `Bot` создаётся до `ensure_data_dir` | T-001…T-010 P3 | **исправлено** | `tldw/bootstrap.py::build_application`: `ensure_data_dir(settings.data_dir)` вызывается до `Bot(...)`, поэтому при `OSError` закрывать нечего. Тест: `tests/test_bootstrap.py::test_data_dir_is_created_before_bot` (Bot не создан). |
| Дедупликация ID продублирована; `is_open` — второй источник | T-007 P3 | **исправлено** | `tldw/access.py::unique_user_ids` — единственная реализация; `AccessPolicy.__post_init__` и `tldw.config._parse_allowed_user_ids` используют её; `describe_allowlist`/`warn_if_open_access` проверяют `AccessPolicy.is_open`. Тесты: `tests/test_access.py::test_config_and_policy_use_the_same_dedup_function`, `::test_describe_allowlist_uses_the_policy_rule`. |
| Синхронный файловый ввод-вывод в корутинах | T-001…T-010 P3 | **исправлено** | `LinkHandler._active_template` и проверка файла `_saved_document` — через `asyncio.to_thread`; в `TemplateHandler` чтение/запись/сброс шаблона и `_failure_context` — через `asyncio.to_thread`. Тест чтения шаблона: `tests/test_link_handler.py::test_template_read_runs_in_worker_thread`; отдельный шов для проверки файла без приватного имени — T-018 (`tests/test_template_handler.py::test_template_view_reads_storage_in_worker_thread` покрывает тот же приём). |
| Хрупкие проверки логов: `warnings == []` смотрит на все `WARNING` | T-007 P3 | **исправлено** | `tests/test_bootstrap.py` фильтрует записи по `record.name == "tldw.access"`, поэтому предупреждение сторонней библиотеки не ломает тест. |
| `title`/`channel`/`</transcript>` не экранируются (prompt injection) | T-004 P3 (T-016) | **исправлено** | `tldw/adapters/llm.py::build_user_prompt`: `_data_text` для title/channel, `_escape_transcript_markers` для тела транскрипта; маркер остаётся один. Тест: `tests/test_llm_adapter.py::test_user_prompt_escapes_metadata_and_transcript_markers`. |
| `reason_phrase`/серверный текст HTTP-ошибки в логе | T-011 P3 (T-016) | **исправлено** | `tldw/adapters/llm.py::_complete` и `tldw/adapters/speech.py::transcribe` логируют только код ответа. Тесты: `tests/test_llm_adapter.py::test_http_error_log_contains_only_status_code`, `tests/test_speech_adapter.py::test_http_error_log_contains_no_reason_phrase`. |

## Устарело / закрыто раньше (без правки кода)

| Пункт | Источник | Статус | Доказательство |
| --- | --- | --- | --- |
| Handoff: untracked-файлы не попали в diffstat | T-006 P2, T-011 P2 | **устарел/процессный** | Правило `git add -N` + `git status -uall` закреплено в `skills/review-handoff`; T-012/T-014/T-015 его выполнили. В T-016 файлы аудита и тестов добавлены `git add -N` (см. финальный handoff). |
| Флаки-риск теста event loop (жёсткие 1,0 с) | T-006 P3 | **передан T-018** | Актуален как тестовая хрупкость (`tests/test_link_handler.py::test_event_loop_is_not_blocked_while_video_is_processed`), но не относится к коду T-016; T-018 — тестовая полнота/флаки. |
| Расхождения типов тест-дублей (`_UnusedService`, `broken_service`) | T-006 P4 | **передан T-018** | Поведение не ломается, mypy в проекте нет; это тестовые дубли, T-018. |
| `handlers/__init__.py` реэкспортирует шесть текстов | T-006 P4 | **устарел** | Публичный API слоя уже расширен по мере T-010–T-015 (`MESSAGE_AUDIO_TOO_LONG`, `find_link_handler`); возврат к узкому `__all__` сломал бы тесты и не даёт безопасности. Осознанно оставлено. |
| CHANGELOG: неверная версия в записи 0.9.0 | T-007 P3 | **процессный/устарел** | Историческая запись; версия и CHANGELOG — зона VERSION-стадии (`skills/release-metadata`), T-016 их не трогала. |
| Память приложения не обновлена к моменту ревью | T-007 заметка | **процессный** | Обновляет LEARN после цикла; T-016 создаёт `facts/t016-debt-audit.md`, строку индекса добавит LEARN. |
| Read-only pytest у ревьюера | T-007 заметка, T-015 P3 | **процессный** | Ограничение песочницы, не код. Точный вывод этой сессии (writable): `.venv/bin/python -m pytest` → `1144 passed` (см. «Гейты»); ревьюер в read-only получает `FileNotFoundError: No usable temporary directory` и 0 тестов (`facts/reviewer-readonly-tests.md`). |
| Дубль `FAKE_TELEGRAM_TOKEN` в двух тестах | T-008 P3 | **устарел** | Закрыт в T-009: `tests/support.py::FAKE_BOT_TOKEN`. |
| Заголовок шаблона в `WARNING` | T-001…T-010 P2 | **устарел** | Закрыт в T-015: `_check_section_heading` не пишет текст заголовка; проверено ревью T-015. |
| `WARNING` на отсутствии файла шаблона | T-001…T-010 P2 | **устарел** | Закрыт в T-015: `TemplateStore.load` — `DEBUG` для `FileNotFoundError`, `WARNING` для битого/неизвестного. |
| Чужой текст ошибки в `/template` | T-001…T-010 P2 | **устарел** | Закрыт в T-015: `MESSAGE_UNEXPECTED_TEMPLATE` во всех пяти точках. |
| Формат «Таймкодов»/«Цитат» в динамическом пути | T-001…T-010 P2 | **устарел** | Закрыт в T-015/ADR-021: `_split_timecode`, `- [01:23] текст`, без пустого `> —`. |
| `httpx.InvalidURL` мимо typed-ошибок | T-011 P2 | **устарел** | Закрыт в T-013: `validate_http_url`, обёртки `_make_client`/`post`, typed `LLMProviderError`/`SpeechProviderError`. |
| `inf`/`nan` в конфиге | T-011 P3 | **устарел** | Закрыт в T-012/T-013: `math.isfinite` в `_parse_float`. |
| Порядок «длительность → is_configured» | T-011 P3 | **устарел** | Закрыт в T-012: `handle_voice` проверяет `is_configured` до лимита. |
| Аудит-таблица вне диффа | T-013 P2 | **устарел** | Формат закрыт T-014/T-015: таблица лежит в `app/memory/facts/tNNN-debt-audit.md` и попадает в дифф через `git add -N`. |

## Передано (актуально, но вне поверхности T-016)

| Пункт | Источник | Куда | Причина |
| --- | --- | --- | --- |
| Дубли `_template_current_sections`/`_requested_sections`, `_tone_text`, метки «встроенный/пользовательский» | T-001…T-010 P3 | **T-017** | Единый источник помощников/валидаторов; T-016 не трогает домен шаблона. |
| `MIN_SECTION_ITEMS`/`MAX_SECTION_ITEMS` в домене не используются для валидации | T-001…T-010 P3 | **T-017** | Лимиты и один источник правды — T-017. |
| Риск обрезания `MAX_PATCH_OUTPUT_TOKENS = 1024` | T-001…T-010 P3 | **T-017** | Лимиты промпта/ответа и тест максимального патча — T-017. |
| `_media_type` кидает plain `ValueError` | T-013 P2 | **T-018** | Сейчас тип задаёт код, не Telegram; typed fallback — тесты/адаптер, T-018. |
| Дубли `BASE_ENV`, `BAD_BASE_URLS`, `isspace`/`Cc` в тестах | T-013 P3, T-011 P3 | **T-018** | Тестовые дубли и общий `tests/support.py` — T-018. |
| `requests` не объявлен в `pyproject.toml` | T-014 P2 | **T-018** | Упаковка/зависимости, не код T-016. |
| Дубли обёрток «лог + ответ, не падать»: `LinkHandler._answer`, `TemplateHandler._deny`/`_deny_callback`, два `_log_denied`, копипаста `DenialLogLimiter` | REVIEW-2 T-016 | **T-017** | Общий помощник затрагивает два обработчика целиком — это рефакторинг, а не исправление дефекта; T-016 фиксирует дубль как кандидата. |
| `MAX_CONCURRENT_LINKS` захардкожен, очередь ожидающих задач не ограничена | REVIEW-2 T-016 | **T-017** | В T-016 лимит задокументирован (семафор только вокруг `process`, «Принял»/«Обрабатываю» до слота). Вынос в `Settings` — это env-переменная, `.env.example`, README и тесты синхронизации: новая поверхность, а не FIX. |

## Что проверено отдельно

- **Секреты в логах T-016.** Ловушки `FAKE_BEARER_TOKEN`, `FAKE_ENV_KEY`,
  `FAKE_API_KEY`, `FAKE_BOT_TOKEN` и 400-сегментный «транскрипт» не попадают
  в лог ни на пути сборки, ни на пути остановки, ни у стороннего логгера
  (`tests/test_logging_setup.py`).
- **Fail-open не молчит, но MVP сохранён.** `create_router(service)` и
  `LinkHandler(service)` дают `WARNING` и открытую политику, явный `()` —
  прежнее документированное «доступ всем» без предупреждения о забытом
  аргументе.
- **Жизненный цикл.** `Application.close()` сначала отменяет задачи, потом
  закрывает сессию; повторный вызов безопасен (существующий
  `test_close_is_safe_to_call_twice`).
- **Границы.** В диффе есть `specs/003-review-debt/tasks.md`: это артефакт
  оркестратора — `pull_from_backlog` перенёс `## Бэклог` под T-016 (см.
  `facts/review-spec-edit-artifact.md`), а не правка разработчика; `specs/`
  сознательно не откатывалась. `orchestrator/`, `dsh/`, `docker/`,
  `aidevteam.toml` не менялись; версия и CHANGELOG — зона VERSION-стадии.

## Гейты

Точные команды (рабочий каталог `app/`, окружение `app/.venv`):

```text
$ .venv/bin/python -m pytest
...
1144 passed

$ .venv/bin/ruff check .
All checks passed!
```

Оговорка: вывод получен в обычной песочнице с writable `TMPDIR`. У ревьюера
песочница read-only: pytest не стартует (`FileNotFoundError: No usable
temporary directory`), поэтому он не может воспроизвести это число — это
ограничение окружения, а не расхождение; см.
`facts/reviewer-readonly-tests.md`.

## FIX после REVIEW-1

Ревьюер вернул `ISSUES` по десяти пунктам; правки не расширяли поверхность T-016.

| Замечание | Что сделано | Проверка |
| --- | --- | --- |
| 1. Гонка дедупликации | Слот `_inflight` резервируется синхронно, до первого `await`; «принял» и снимок шаблона перенесены в фоновую задачу. | `test_concurrent_duplicate_links_are_deduplicated` (мутация старого кода даёт 2 вызова LLM), `test_repeated_link_is_deduplicated_and_llm_called_once`. |
| 2. Регресс редактора секретов | Env-имена распознаются без учёта регистра и с `_`; исключены только `max_tokens`/`*_TOKENS`. | `test_env_secret_names_are_redacted_case_insensitively`, `test_max_tokens_is_not_mistaken_for_a_secret`. |
| 3. URL/query в `log_failure` | `_SECRET_PATTERNS` сводит URL к `схема://host`, вырезая путь/query/фрагмент. | `test_url_in_exception_is_reduced_to_host_without_query`; тесты обработчика теперь кладут URL в текст исключения, а не `RuntimeError("boom")`. |
| 4. `close()` не подключён к штатной остановке | `cli.main` закрывает приложение в `finally` на любом пути, включая настоящий `application.run()`. | `test_default_run_path_closes_application`. |
| 5. Границы `specs/` и утверждение аудита | `specs/003-review-debt/tasks.md` назван артефактом `pull_from_backlog`; `specs/` не откатывалась. | Раздел «Границы» выше, `facts/review-spec-edit-artifact.md`. |
| 6. Левая граница `_LINK_RE` | `(?<![\w.-])` вместо `(?<![a-z0-9.-])`. | `test_link_regex_has_left_boundary` (`not_youtube.com`, `foo_youtu.be`, `текстyoutube.com`). |
| 7. Дубль записи в лог конспекта | Оставлена одна запись «Конспект: видео …». | `test_service_logs_safe_video_id`. |
| 8. Неограниченный `DenialLogLimiter._reported` | Предел `MAX_TRACKED_DENIALS`, FIFO-вытеснение старых ID, `tracked`. | `test_denial_log_limiter_does_not_grow_without_bound`. |
| 9. Мелочи качества | `empty_env_file` — в `tests/support.py`; новые тесты не используют приватные имена; `parse_video_id` снова поднимает typed-ошибку `from exc`. | Импорт общего помощника в трёх тестах, `test_parse_video_id_preserves_cause_for_malformed_url`. |
| 10. Гейты | Прогнаны в окружении `app/.venv`. | На момент REVIEW-1 — `1135 passed`, затем FIX добавил 9 тестов: `1144 passed`, `ruff check .` — чисто (см. «Гейты»). |

## FIX после REVIEW-2

Второй раунд ревью вернул `ISSUES` по десяти пунктам. Закрыто восемь; объём
T-016 не расширялся: правки — в тех же модулях и тестах, дубли помощников и
перенос лимита в конфиг оставлены T-017 (см. «Передано»).

| Замечание | Что сделано | Проверка |
| --- | --- | --- |
| 1. Регресс редактора секретов: имена с суффиксом | В первый шаблон `_SECRET_PATTERNS` возвращён хвост `[A-Za-z0-9_]*` после ключевого слова; `TOKEN(?!S)` сохранён, поэтому `max_tokens`/`*_TOKENS` по-прежнему видны. | `test_env_secret_names_are_redacted_case_insensitively` — шесть ловушек с суффиксом; мутация прежнего шаблона возвращает значения как есть. |
| 2. Нет теста-ловушки на этот путь | В параметры добавлены `SECRET_KEY=…`, `MY_SECRET_KEY=…`, `AWS_SECRET_ACCESS_KEY=…`, `PASSWORD_HASH=…`, `ACCESS_TOKEN_HASH=…`, `TELEGRAM_BOT_TOKEN=…`. | Те же тесты; `test_max_tokens_is_not_mistaken_for_a_secret` расширен `input_tokens`. |
| 3. Необработанное исключение на пути остановки | `cli._close_application` возвращает причину вместо исключения; в `finally` она пишется `WARNING` и не подменяет код возврата, в `--check-config` — диагностика в stderr и `EXIT_FAILURE`. | `tests/test_cli.py::test_close_failure_on_run_path_does_not_escape_main` (без правки — `RuntimeError` из `main`), `::test_check_config_reports_close_failure_without_secret`. |
| 4. Ключ дедупликации без чата | Ключ — `(chat_id, user_id, video_id or url)`; `_message_chat_id`. | `test_same_link_in_another_chat_is_not_deduplicated` (на старом ключе — один вызов и «уже обрабатывается»). |
| 5. Лимит есть, очередь не ограничена | Задокументировано в `MAX_CONCURRENT_LINKS` и `LinkHandler`: семафор только вокруг `process`, очередь не ограничена, «Принял»/«Обрабатываю» уходят до слота; вынос в `Settings` — T-017. | Docstring; поведение не менялось (тесты `test_concurrency_limit_serialises_different_links`). |
| 6. Дубли обёрток «лог + ответ» | Не правились: рефакторинг двух обработчиков — T-017. | Строка в «Передано». |
| 7. Семантика `_visible_level` | Зафиксирована как принятый компромисс в docstring: уровень поднимается до порога, запись отличима текстом («Сбой» без стека против «Неизвестный сбой» со стеком). | `test_known_error_stays_visible_at_error_log_level`, `test_unknown_error_at_critical_threshold_is_not_lost`. |
| 8. Неточности в этом файле | Убрана ссылка на несуществующий `_answer_denied`; гейты приведены точными командами и выводом с оговоркой про read-only песочницу ревьюера; обновлены строки дедупликации и редактора. | Этот файл. |
| 9. Мелочи | `_restore_logging` — `Iterator[None]`; `import logging` поднят на уровень модуля в `test_summary_service.py`; `DenialLogLimiter.log` переименован в `log_denial` (не затеняет модульный `log`). | `ruff check .`, тесты `test_access.py`/`test_logging_setup.py`/`test_summary_service.py`. |
| 10. Границы | Код не менялся: `specs/003-review-debt/tasks.md` — артефакт `pull_from_backlog`, защищённые пути не тронуты, статусы задач не менялись. | `git status --porcelain -uall`, `git diff --stat HEAD`. |

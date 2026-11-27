# Замечания ревью T-016: два `ISSUES`, 20 пунктов, 2 передано в T-017

Дата: 2026-11-27. Ревью цикла 16 проверяло рабочую копию против `e2e48bc`
(31 файл, +2158/−231). Два прохода, оба `VERDICT: ISSUES`, по 10
замечаний; FIX-1 и FIX-2 закрыли 18, два пункта осознанно переданы в
T-017. Третий проход не запускался: `max_review_iterations = 2`
исчерпан, остаток вынесен в `specs/inbox/D-001`. Поэтому **правки FIX-2
(29 изменённых файловых фрагментов, +9 тестов, `1144 passed`) не
проверены независимо** — см. `review-two-rounds-leftover.md`.

Ревьюер оба раза не смог прогнать pytest (read-only, `FileNotFoundError:
No usable temporary directory`, по 2 попытки), поэтому «гейт не
подтверждён» стоит в обоих вердиктах. Дефекты он воспроизводил
**исполнением в памяти**: гонку — `asyncio.gather` двух апдейтов,
регресс секретов — вызовом `sanitize_log_text` на старом и новом шаблоне,
URL в логе — `RuntimeError` с настоящим URL.

## REVIEW-1 (40 шагов, 40,3 тыс. output)

| № | Замечание | Класс | Статус | Доказательство после FIX |
|---|---|---|---|---|
| 1 | Гонка дедупликации: `_start` после `await _answer`, два LLM-вызова; тест подаёт апдейты последовательно и гонку не ловит | P1/P2 | **исправлено** | слот `_inflight` резервируется до первого `await`; `test_concurrent_duplicate_links_are_deduplicated` (на старом коде — 2 вызова) |
| 2 | Регресс редактора секретов: `llm_api_key`, `db_password`, `bot_token`, `MY_AUTHORIZATION` больше не скрываются | P2 | **исправлено** | env-имена без учёта регистра и с `_`; `test_env_secret_names_are_redacted_case_insensitively` |
| 3 | `log_failure` пишет полный URL/query из `str(exc)` | P2 | **исправлено** | `_SECRET_PATTERNS` сводит URL к `схема://host`; `test_url_in_exception_is_reduced_to_host_without_query` |
| 4 | `Application.close()` не подключён к штатной остановке: `cli.py` закрывает только в тестовой ветке | P2 | **исправлено** | `cli.main` закрывает в `finally` на любом пути; `test_default_run_path_closes_application` |
| 5 | Граница диффа: `specs/.../tasks.md` изменён, а аудит утверждает «`specs/` не менялись» | процессный | **исправлено в аудите** | строка «Границы» называет артефакт `pull_from_backlog`; `review-spec-edit-artifact.md` |
| 6 | Левая граница `_LINK_RE` не учитывает `_` и не-ASCII (`not_youtube.com`, `foo_youtu.be`) | P3 | **исправлено** | `(?<![\w.-])`; `test_link_regex_has_left_boundary` расширен |
| 7 | Дубль записи «Конспект: …» в `summary.py` | P3 | **исправлено** | оставлена одна запись «Конспект: видео …» |
| 8 | `DenialLogLimiter._reported` растёт без предела | P3 | **исправлено** | `MAX_TRACKED_DENIALS = 1024`, FIFO; тест на ограниченность памяти |
| 9 | Мелочи: третий дубль `_empty_env_file`, приватные имена в тестах, потерян `from exc` в `parse_video_id` | P3 | **исправлено** | `tests/support.py`, `test_parse_video_id_preserves_cause_for_malformed_url` |
| 10 | Гейт не подтверждён: pytest в read-only не стартует | процессный | **принято с оговоркой** | `1144 passed` в writable-песочнице; аудит помечает ограничение |

## REVIEW-2 (73 шага, 58,4 тыс. output)

| № | Замечание | Класс | Статус | Доказательство после FIX-2 |
|---|---|---|---|---|
| 1 | **Регресс FIX-1:** шаблон секретов требует секретное слово в конце, поэтому `SECRET_KEY`, `MY_SECRET_KEY`, `AWS_SECRET_ACCESS_KEY`, `PASSWORD_HASH`, `ACCESS_TOKEN_HASH` не скрываются (на базе скрывались) | P2 | **исправлено** | хвост `[A-Za-z0-9_]*` + `TOKEN(?!S)`; 6 тестов-ловушек; мутация старого шаблона возвращает значения |
| 2 | Нет теста-ловушки на этот путь (все кейсы заканчивались секретным словом) | P2 | **исправлено** | параметры `SECRET_KEY=…`, `PASSWORD_HASH=…`, `AWS_SECRET_ACCESS_KEY=…`, `ACCESS_TOKEN_HASH=…` |
| 3 | `asyncio.run(application.close())` в `finally` без защиты: исключение вылетает из `main()` и подменяет `EXIT_FAILURE` | P2 | **исправлено** | `cli._close_application` возвращает причину, `WARNING` без подмены кода; `test_close_failure_on_run_path_does_not_escape_main` |
| 4 | Ключ дедупликации `(user_id, video_id)` без чата: та же ссылка в другом чате получает «уже обрабатывается» | P2 | **исправлено** | ключ `(chat_id, user_id, video_id or url)`; `test_same_link_in_another_chat_is_not_deduplicated` |
| 5 | `MAX_CONCURRENT_LINKS` захардкожен, очередь ожидания не ограничена | P3 | **передано T-017** | задокументировано в docstring; вынос в `Settings` — новая env-поверхность (`.env.example`, README, тесты) |
| 6 | Дубли обёрток «лог + ответ»: `LinkHandler._answer`, `TemplateHandler._deny`/`_deny_callback`, два `_log_denied`, копипаста `DenialLogLimiter` | P3 | **передано T-017** | рефакторинг двух обработчиков целиком — не FIX; строка в «Передано» |
| 7 | `_visible_level` поднимает известную ошибку до `ERROR`/`CRITICAL` — неотличима от инцидента | P3 | **зафиксировано как компромисс** | docstring: уровень поднимается до порога, запись отличается текстом |
| 8 | Неточности аудит-файла: ссылка на несуществующий `_answer_denied`, «1135 passed» без оговорки | P3 | **исправлено** | файл переписан, гейты — точные команды и вывод, раздел «FIX после REVIEW-2» |
| 9 | Мелочи: `_restore_logging` помечен `-> None`, локальный `import logging`, `DenialLogLimiter.log` затеняет модульный `log` | P3 | **исправлено** | `Iterator[None]`, импорт на уровне модуля, `log_denial` |
| 10 | Границы: `specs/.../tasks.md` — артефакт `pull_from_backlog`, защищённые пути не тронуты | процессный | **подтверждено** | `git status`, `git diff --stat HEAD`; статусы задач не менялись |

## Что осталось открытым

1. **Дубли обёрток «лог + ответ»** — T-017 (единый источник помощников):
   `LinkHandler._answer`, `TemplateHandler._deny`/`_deny_callback`,
   `_log_denied`, подключение `DenialLogLimiter` в двух обработчиках.
2. **`MAX_CONCURRENT_LINKS` в `Settings`** — T-017: env-переменная,
   `.env.example`, README, тесты синхронизации; очередь ожидания и
   «Принял»/«Обрабатываю» до слота пока только задокументированы.
3. **Правки FIX-2 не отревьюены** — процессный долг: следующий цикл
   должен прочитать последние 29 правок (в первую очередь
   `errors.py::_SECRET_PATTERNS`, `cli.py::_close_application`,
   `handlers/link.py` — ключ дедупликации).
4. **Гейт подтверждён только разработчиком** — pytest 1144 passed
   получен в writable-песочнице; ревьюер не мог его воспроизвести ни в
   одном проходе.

## Куда смотреть следующему циклу

- T-017 — дубли помощников/лимитов (пункты 5–6 выше, плюс `_media_type`
  из T-013 и `MAX_PATCH_OUTPUT_TOKENS`).
- T-018 — тесты/README/упаковка (`requests`, `BASE_ENV`, общие
  помощники, флаки).
- Черновик D-001: пункты уже в T-017, новых задач по заголовку не
  заводить; сверить с этим файлом и «Передано» в `t016-debt-audit.md`.

Связанные: `cycle-16-spend.md`, `t016-debt-audit.md`,
`review-two-rounds-leftover.md`, `estimate-audit-exceeds-budget.md`,
`review-debt-accumulates.md`, `reviewer-readonly-tests.md`,
`review-spec-edit-artifact.md`.

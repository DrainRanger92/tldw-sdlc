# Память приложения — индекс

Факты и решения, накопленные этапом LEARN. Одна строка на файл. Обновлён
после цикла 9 (T-009: сквозной E2E и README, 371 тест, версия 0.13.0; задача
без продакшн-кода стоила 178 шагов; второй `ISSUES` подряд).

## Факты

- [Расход цикла 1 и калибровка оценки M](facts/cycle-01-spend.md) — T-001 (M): 81 шаг, 73,5 тыс. output, 3,85 млн токенов; первый замер, гипотеза «M ведёт себя как L».
- [Расход цикла 2: specs, T-002 и ревью](facts/cycle-02-spend.md) — подготовка specs + T-002: 83 шага, 3,20 млн токенов до LEARN; T-002 вдвое дешевле T-001, ревью — 40 % расхода задачи.
- [Расход цикла 3: T-003 и ревью](facts/cycle-03-spend.md) — T-003 (M): 89 шагов, 94,1 тыс. output, 5,86 млн токенов; 46 % вызовов — до первой строки кода.
- [Расход цикла 4: T-004 и ревью](facts/cycle-04-spend.md) — T-004 (M): 100 шагов, 113,4 тыс. output, 7,31 млн токенов; самый дорогой по output цикл, ревью — 40 шагов и 37 % output.
- [Расход цикла 5: T-005 и ревью](facts/cycle-05-spend.md) — T-005 (M): 94 шага (66 + 28), 79,2 тыс. output, 7,10 млн токенов; первое попадание прогноза, ревью — 28 шагов и 25 % output.
- [Расход цикла 6: T-006 и ревью](facts/cycle-06-spend.md) — T-006 (M): 101 шаг (62 + 39), 80,7 тыс. output, 6,90 млн токенов; прогноз попал; его прогноз для T-007 (≤ 20 шагов) опровергнут.
- [Расход цикла 7: T-007 (allowlist) и ревью](facts/cycle-07-spend.md) — T-007 (S): 106 шагов (70 + 36), 51,7 тыс. output, 5,89 млн токенов; максимум шагов при минимуме output.
- [Расход цикла 8: T-008 и два раунда ревью](facts/cycle-08-spend.md) — T-008 (S): 178 шагов (48 + 23 + 55 + 52), 142,1 тыс. output, 10,81 млн токенов; первый `ISSUES`, FIX 55 и REVIEW-2 52.
- [Расход цикла 9: T-009 (E2E + README) без продакшн-кода](facts/cycle-09-spend.md) — 178 шагов (41 + 28 + 49 + 60), 154,7 тыс. output, 11,82 млн токенов, 1 строка продакшна; ревью+FIX — 77 % шагов.
- [LEARN стоит как половина задачи и в бюджет не входит](facts/learn-cost-unaccounted.md) — циклы 1–9: 51–126+ шагов и 50–93 тыс. output; цикл 9 — ≥ 88 шагов и ≥ 74 тыс., LEARN в обычном бюджете при «широкой» задаче.
- [Оценка M стабилизировалась, но раунд ревью ломает вилку](facts/estimate-m-stabilized.md) — семь замеров EXECUTE 58–70 / REVIEW 23–40; T-008 (48 + 23) в вилке, а FIX (55) и REVIEW-2 (52) — вне её; output на шаг 512–1021.
- [Калибровка размеров: M стабильна, S не дешевле M, раунд ревью не учтён](facts/estimate-calibration-m.md) — девять замеров; три S дали 106, 178 и 178 шагов; медиана 100 шагов / 81 тыс.; вилка M с полными FIX и REVIEW-2.
- [Прогнозы занижены для новой интеграции и для «S» с широкими тестами](facts/estimate-forecast-underestimates.md) — T-004: 100 против 50–70; T-007: 106 против ≤ 20; T-008: 178 против 85–110; T-009: 178 против 95–140; добавлено слагаемое FIX.
- [Метка S рухнула: три «S» стоили 106, 178 и 178 шагов](facts/estimate-size-s-collapsed-into-m.md) — T-007, T-008 и T-009 (1 строка продакшна); виновата ширина тестов и поверхности проверки, а не строки.
- [Оценки размера систематически занижены: 9 замеров](facts/estimate-misses-fix-round.md) — S в среднем дороже M в 1,8 раза; FIX 1,0–1,2 × EXECUTE и REVIEW-2 1,5–2,1 × REVIEW-1, а не резерв 0,3–0,5.
- [Ревью с ISSUES: раунд исправлений стоит как сама работа](facts/review-fix-round-cost.md) — T-008 FIX 1,15 × EXECUTE, T-009 1,20 ×; REVIEW-2 в 2,1–2,3 раза дороже первого прохода; 73–77 % шагов цикла.
- [Тесты очистки логов были зелёными по чужой причине](facts/secret-redaction-tests.md) — фикстура с `sk-` маскировала `Bearer`, отступ обходил очистку; правило форм из реального кода, мутации и тестов в обе стороны.
- [Версию и CHANGELOG ведут два автора](facts/version-changelog-two-authors.md) — VERSION-стадия поднимает только `pyproject.toml`; цикл 9 подтвердил: HEAD после 8 был красным, после 9 снова будет; 4 дубля CHANGELOG + пятый.
- [Композиция готовых контрактов дешевле новой интеграции](facts/composition-is-cheaper-than-integration.md) — T-005: 94 шага и 79 тыс. output без нового внешнего контракта; триггер L — новая поверхность.
- [Задача без продакшн-кода стоит как M](facts/tests-and-docs-cost-like-code.md) — T-009: 1 строка кода, 662 строки тестов, README, 178 шагов; планировать по тестовым файлам и сквозным кейсам, E2E — отдельное слагаемое.
- [Базовая линия TLDW после T-001](facts/tldw-baseline-after-t001.md) — исторический срез: каркас, env-конфиг, 58 тестов.
- [Базовая линия TLDW после T-002](facts/tldw-baseline-after-t002.md) — исторический срез: модель `Note`, Obsidian-рендер, 116 тестов.
- [Базовая линия TLDW после T-003](facts/tldw-baseline-after-t003.md) — исторический срез: YouTube-адаптер, 163 теста.
- [Базовая линия TLDW после T-004](facts/tldw-baseline-after-t004.md) — исторический срез: LLM-адаптер, промпт, повторы, 212 тестов.
- [Базовая линия TLDW после T-005](facts/tldw-baseline-after-t005.md) — исторический срез: `SummaryService.process`, `storage/notes.py`, 257 тестов.
- [Базовая линия TLDW после T-006](facts/tldw-baseline-after-t006.md) — исторический срез: `handlers/link.py`, `to_thread`, отправка `.md`, 282 теста.
- [Базовая линия TLDW после T-007](facts/tldw-baseline-after-t007.md) — исторический срез: `access.py`, 309 тестов, 0.10.0.
- [Базовая линия TLDW после T-008](facts/tldw-baseline-after-t008.md) — исторический срез: `errors.py`, 343 теста; P1 T-004/T-005 открыты.
- [Базовая линия TLDW после T-009](facts/tldw-baseline-after-t009.md) — актуальный срез: E2E + README, `tests/support.py`, 371 тест, 0.13.0; MVP закрыт, P1 T-004/T-005 не покрыт E2E.
- [spec.md и plan.md 001-mvp — заготовки](facts/specs-001-artifacts-stubs.md) — исторический факт цикла 1; закрыто в цикле 2.
- [specs/001-mvp: spec.md и plan.md заполнены](facts/specs-001-filled-in-cycle-02.md) — FR-1…FR-11, план этапов 1–7, источник истины; `plan.md` хардкодит текущий инкремент.
- [`plan.md` фиксирует устаревший «текущий инкремент»](facts/plan-md-increment-drift.md) — 8/8 циклов: строка всё ещё называет T-002, отметки «сделано» только у T-001; MVP закрыт, план отстал сильнее всего.
- [Мелкие замечания ревью T-001](facts/t001-review-minor-issues.md) — inf/nan в timeout, дубли тестовых помощников, имя теста, порядок в build_application.
- [Замечания ревью T-002, оставшиеся в коде](facts/t002-review-issues.md) — строка вместо кортежа, bool-таймкод, noncharacters в YAML, slug в байтах, дублирование.
- [Замечания ревью T-003, оставшиеся в коде](facts/t003-review-issues.md) — P1: нет таймаута transcript-api, двойной `_extract_info`, приоритет ошибок, кэш yt-dlp.
- [Замечания ревью T-004, оставшиеся в коде](facts/t004-review-issues.md) — P1: `InvalidURL`/`RuntimeError` мимо `LLMError` (открыт); `MAX_FOLLOW_UPS` убран из `__all__`; третья копия `_one_line`, prompt injection, версии.
- [Замечания ревью T-005, оставшиеся в коде](facts/t005-review-issues.md) — P1: пустой транскрипт не отвергается (открыт), вырожденный маркер усечения, учёт при `budget == 0`; P2 — дубль фильтрации, `int()` вместо округления, `str(item)`, `url`.
- [Замечания ревью T-006, оставшиеся в коде](facts/t006-review-issues.md) — P2: untracked-файлы не попали в diffstat; P3: граница `_LINK_RE`, URL в логе, `close()` без отмены задач, нет лимита параллелизма, флаки-тест.
- [Замечания ревью T-007, оставшиеся в коде](facts/t007-review-issues.md) — P2: fail-open default, отказ без обёртки и лимита частоты; P3: дубль дедупликации ID, хрупкие проверки логов, неточная версия в CHANGELOG.
- [Замечания ревью T-008, оставшиеся в коде](facts/t008-review-issues.md) — P2: очистка в обход `cli.py`; P3: нет `video_id` в логе, `ExceptionGroup`, `WARNING` при `LOG_LEVEL=ERROR`; P1 T-004/T-005 не закрыты.
- [Замечания ревью T-009, оставшиеся в коде](facts/t009-review-issues.md) — закрыто 5 групп (handoff, дублирование каркаса, сканер секретов, e2e-шум, токен); осталось: README не сверяет негативные `MESSAGE_*`, формулировка про «заметка не создаётся», версия P3; P1 не покрыт E2E.
- [Долг ревью копится девять циклов](facts/review-debt-accumulates.md) — 69 пунктов и групп; T-009 добавила 7 и закрыла 5; P1 T-004/T-005 всё ещё открыты и невидимы для E2E.
- [Ревью и untracked-файлы](facts/review-diff-untracked.md) — 2/2 сломано до `git add -N`, 6/6 чистых после, 2 регрессии (T-006, T-009); правка `specs/` — 4 цикла подряд, решение «не откатывать» зафиксировано.
- [Ревьюер в read-only песочнице не может прогнать pytest](facts/reviewer-readonly-tests.md) — 9/9 циклов; в цикле 9 главный артефакт (`test_e2e.py`) целиком упирается в `tmp_path`, сценарий воспроизведён вручную.
- [Интеграция со сторонними библиотеками: сначала проба API](facts/third-party-api-discovery.md) — T-003: 46 % вызовов до кода; T-004: 22 %; T-006: 43,5 %; T-007: 32,6 %; T-008: 35,4 %; T-009: 43,9 % без новой библиотеки.
- [Офлайновые тесты aiogram-обработчика](facts/aiogram-offline-handler-tests.md) — `FakeSession(BaseSession)` + `Dispatcher.feed_update`, `wait_idle`, `threading.Event` вместо sleep, `tmp_path` только файловым кейсам.
- [Git в DSH-сессиях сломан двумя переменными](facts/git-env-broken-in-dsh.md) — 9/9 циклов; в цикле 9 — 6 упавших git-вызовов, обёртка не с первого вызова; скил `git-in-sandbox`.

## Решения (ADR)

- [ADR-001: конфигурация только из env](decisions/adr-001-config-env-only.md) — fail-fast, секреты вне repr, `.env` не перетирает env.
- [ADR-002: слои пакета и адаптеры](decisions/adr-002-layered-adapters.md) — handlers → services → adapters/storage, сборка без сети.
- [ADR-003: модель заметки и ручной YAML-рендер](decisions/adr-003-note-model-manual-yaml.md) — frozen dataclasses и чистые функции в `services/note.py`, YAML вручную, PyYAML только в тестах.
- [ADR-004: контракт YouTube-адаптера](decisions/adr-004-youtube-adapter-contract.md) — два источника за протоколами, fallback yt-dlp, типизированные ошибки и их приоритет.
- [ADR-005: контракт LLM-адаптера](decisions/adr-005-llm-adapter-contract.md) — промпт/шаблон как ресурсы пакета, строгая JSON-валидация, повтор только на 429/5xx, `LLMError`.
- [ADR-006: контракт сервиса конспекта](decisions/adr-006-summary-service.md) — композиция за протоколами `VideoSource`/`SummaryProvider`, порог до LLM, атомарная запись.
- [ADR-007: Telegram-слой](decisions/adr-007-telegram-handler.md) — тонкий обработчик, «принял» до тяжёлых вызовов, фоновая задача + `to_thread`.
- [ADR-008: allowlist и правило доступа](decisions/adr-008-allowlist-access-policy.md) — `AccessPolicy` в `access.py`, проверка первой строкой `handle_message`, без aiogram-middleware.
- [ADR-009: пользовательские ошибки и очистка лога](decisions/adr-009-user-facing-errors.md) — нейтральный `errors.py`, `UserFacingError`, `describe_error`/`log_failure`, шаблоны секретов.
- [ADR-010: сквозной тест и общий тестовый каркас](decisions/adr-010-e2e-test-surface.md) — настоящие `Dispatcher`/`Handler`/`Service`/`storage`, фейки только на трёх внешних границах, общий `tests/support.py`, README под тестами.

## Скилы

- [intake](../skills/intake/SKILL.md) — как уточнять заявки; «Контекст проекта» обновлён после T-009 (MVP закрыт, 371 тест, E2E, 0.13.0, P1-долг и правило FIX/REVIEW-2).
- [release-metadata](../skills/release-metadata/SKILL.md) — кто владеет версией и CHANGELOG; цикл 9 подтвердил возврат рассинхрона в HEAD.
- [size-forecast](../skills/size-forecast/SKILL.md) — оценка размера по тестовым файлам и поверхности проверки; пять стадий, FIX 1,0–1,2 × EXECUTE, REVIEW-2 1,5–2,1 ×.
- [fix-round](../skills/fix-round/SKILL.md) — что делать при `ISSUES`: разметка пяти стадий, бюджет FIX/REVIEW-2, не расширять объём, не откатывать чужое.
- [cycle-accounting](../skills/cycle-accounting/SKILL.md) — как восстановить расход по кэшу DSH: шаги, токены, роли, стадии FIX/REVIEW-2; в цикле 9 — без продакшн-кода.
- [offline-tests](../skills/offline-tests/SKILL.md) — офлайновые тесты: fake_env, фейковые адаптеры, aiogram `FakeSession`, сквозной E2E через настоящие слои, README под тестами.
- [review-handoff](../skills/review-handoff/SKILL.md) — `git add -N` + `git diff HEAD`, сверка статуса с diffstat, read-only pytest, граница `specs/`, два раунда ревью.
- [git-in-sandbox](../skills/git-in-sandbox/SKILL.md) — обёртка `git_ws`; в цикле 9 применялась после первой ошибки, 6 упавших вызовов.
- [shared-helpers](../skills/shared-helpers/SKILL.md) — правило трёх копий для `_one_line`/`format_timecode`, нормализации env и **тестового каркаса** (`tests/support.py`).

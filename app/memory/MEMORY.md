# Память приложения — индекс

Факты и решения, накопленные этапом LEARN. Одна строка на файл. Обновлён
после цикла 10: весь инкремент `specs/002` (T-001–T-010) выполнен одним
циклом — 881 шаг, 773 тыс. output, одно ревью (56 шагов, `APPROVE` с восемью
пунктами, FIX=0), 828 тестов, версия 0.14.0 → 0.15.0; M покрыла 32–105 шагов, поэтому
введены подклассы M1/M2/M3 и правило «2–3 задачи на инкремент» (ADR-014).

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
- [Расход цикла 10: весь инкремент 002 (T-001–T-010)](facts/cycle-10-spend.md) — 881 шаг, 773,2 тыс. output, 93,58 млн токенов, 2 ч; ревью одно (56 шагов, 6,4 %), FIX=0; на задачу 88 шагов и 77 тыс. — на 18–19 % дешевле, но проверка отложена.
- [Инкремент из десяти задач = один цикл, одно ревью, ноль FIX](facts/batch-increment-single-review.md) — оркестратор исполняет весь «Текущий инкремент»; ревью 37,9 мин и 158/828 тестов, 8 замечаний без FIX, 10,4 тыс. строк без промежуточных коммитов; правило — 2–3 задачи на инкремент (ADR-014).
- [LEARN стоит как половина задачи и в бюджет не входит](facts/learn-cost-unaccounted.md) — циклы 1–9: 51–126+ шагов и 50–93 тыс. output; цикл 10 — ≥ 168 шагов и ≥ 132 тыс. при 881 шаге цикла (19 %): бюджет LEARN зависит от памяти и числа обновляемых файлов, а на задачу — от размера инкремента.
- [Оценка M стабилизировалась, но раунд ревью ломает вилку](facts/estimate-m-stabilized.md) — семь замеров EXECUTE 58–70 / REVIEW 23–40; T-008 (48 + 23) в вилке, а FIX (55) и REVIEW-2 (52) — вне её; output на шаг 512–1021.
- [Калибровка размеров: M стабильна, S не дешевле M, раунд ревью не учтён](facts/estimate-calibration-m.md) — девять замеров + десять M цикла 10 (32–105 шагов); медиана 68,5; введены подклассы M1/M2/M3; вилка M с полными FIX и REVIEW-2.
- [Метка M покрывает 32–105 шагов: систематический промах оценок](facts/estimate-m-bucket-wide.md) — десять M: 4 в вилке 55–70, T-002 32 (M1), T-009 103 и T-010 105 (M3); драйвер — слои, ошибки/логи, E2E/README; агрегат «попал» вычитанием ревью.
- [Прогнозы занижены для новой интеграции и для «S» с широкими тестами](facts/estimate-forecast-underestimates.md) — T-004: 100 против 50–70; T-007: 106 против ≤ 20; T-008: 178 против 85–110; T-009: 178 против 95–140; в цикле 10 агрегат «10 × M» попал случайно — EXECUTE перебрал, ревью недобрало.
- [Метка S рухнула: три «S» стоили 106, 178 и 178 шагов](facts/estimate-size-s-collapsed-into-m.md) — T-007, T-008 и T-009 (1 строка продакшна); виновата ширина тестов и поверхности проверки, а не строки.
- [Оценки размера систематически занижены: 9 замеров](facts/estimate-misses-fix-round.md) — S в среднем дороже M в 1,8 раза; FIX 1,0–1,2 × EXECUTE и REVIEW-2 1,5–2,1 × REVIEW-1, а не резерв 0,3–0,5.
- [Ревью с ISSUES: раунд исправлений стоит как сама работа](facts/review-fix-round-cost.md) — T-008 FIX 1,15 × EXECUTE, T-009 1,20 ×; REVIEW-2 в 2,1–2,3 раза дороже первого прохода; 73–77 % шагов; цикл 10 — обратный случай `APPROVE` без FIX (6,4 % шагов, долг +8).
- [Тесты очистки логов были зелёными по чужой причине](facts/secret-redaction-tests.md) — фикстура с `sk-` маскировала `Bearer`, отступ обходил очистку; правило форм из реального кода, мутации и тестов в обе стороны.
- [Версия и CHANGELOG: двух авторов свели к одному](facts/version-changelog-two-authors.md) — sdlc-фикс `902283c` синхронизирует `__init__` и гоняет гейты после bump; цикл 10 не менял версию в задачах — VERSION поднимет обе до 0.15.0 и даст одну запись CHANGELOG.
- [Композиция готовых контрактов дешевле новой интеграции](facts/composition-is-cheaper-than-integration.md) — T-005: 94 шага и 79 тыс. output без нового внешнего контракта; триггер L — новая поверхность.
- [Задача без продакшн-кода стоит как M](facts/tests-and-docs-cost-like-code.md) — T-009: 1 строка кода, 662 строки тестов, README, 178 шагов; T-010: E2E 657 строк + README, 105 шагов и 14,2 млн токенов; планировать по тестовым файлам и сквозным кейсам, E2E — отдельное слагаемое.
- [Базовая линия TLDW после T-001](facts/tldw-baseline-after-t001.md) — исторический срез: каркас, env-конфиг, 58 тестов.
- [Базовая линия TLDW после T-002](facts/tldw-baseline-after-t002.md) — исторический срез: модель `Note`, Obsidian-рендер, 116 тестов.
- [Базовая линия TLDW после T-003](facts/tldw-baseline-after-t003.md) — исторический срез: YouTube-адаптер, 163 теста.
- [Базовая линия TLDW после T-004](facts/tldw-baseline-after-t004.md) — исторический срез: LLM-адаптер, промпт, повторы, 212 тестов.
- [Базовая линия TLDW после T-005](facts/tldw-baseline-after-t005.md) — исторический срез: `SummaryService.process`, `storage/notes.py`, 257 тестов.
- [Базовая линия TLDW после T-006](facts/tldw-baseline-after-t006.md) — исторический срез: `handlers/link.py`, `to_thread`, отправка `.md`, 282 теста.
- [Базовая линия TLDW после T-007](facts/tldw-baseline-after-t007.md) — исторический срез: `access.py`, 309 тестов, 0.10.0.
- [Базовая линия TLDW после T-008](facts/tldw-baseline-after-t008.md) — исторический срез: `errors.py`, 343 теста; P1 T-004/T-005 открыты.
- [Базовая линия TLDW после T-009](facts/tldw-baseline-after-t009.md) — исторический срез: E2E + README, `tests/support.py`, 371 тест, 0.13.0; MVP закрыт, P1 T-004/T-005 не покрыт E2E.
- [Базовая линия TLDW после T-010](facts/tldw-baseline-after-t010.md) — актуальный срез: пользовательский шаблон (`/template`, FSM, JSON v1 на user ID, динамический рендер и промпт), 828 тестов, 0.14.0 → 0.15.0; 8 пунктов ревью и P1 T-004/T-005 открыты.
- [spec.md и plan.md 001-mvp — заготовки](facts/specs-001-artifacts-stubs.md) — исторический факт цикла 1; закрыто в цикле 2.
- [specs/001-mvp: spec.md и plan.md заполнены](facts/specs-001-filled-in-cycle-02.md) — FR-1…FR-11, план этапов 1–7, источник истины; «текущий инкремент» в `plan.md` был исправлен ANALYZE цикла 10.
- [`plan.md` фиксирует устаревший «текущий инкремент»](facts/plan-md-increment-drift.md) — 8 циклов расхождения; закрыто ANALYZE цикла 10: план 001 обновлён, план 002 не хардкодит номер задачи.
- [Мелкие замечания ревью T-001](facts/t001-review-minor-issues.md) — inf/nan в timeout, дубли тестовых помощников, имя теста, порядок в build_application.
- [Замечания ревью T-002, оставшиеся в коде](facts/t002-review-issues.md) — строка вместо кортежа, bool-таймкод, noncharacters в YAML, slug в байтах, дублирование.
- [Замечания ревью T-003, оставшиеся в коде](facts/t003-review-issues.md) — P1: нет таймаута transcript-api, двойной `_extract_info`, приоритет ошибок, кэш yt-dlp.
- [Замечания ревью T-004, оставшиеся в коде](facts/t004-review-issues.md) — P1: `InvalidURL`/`RuntimeError` мимо `LLMError` (открыт); `MAX_FOLLOW_UPS` убран из `__all__`; третья копия `_one_line`, prompt injection, версии.
- [Замечания ревью T-005, оставшиеся в коде](facts/t005-review-issues.md) — P1: пустой транскрипт не отвергается (открыт), вырожденный маркер усечения, учёт при `budget == 0`; P2 — дубль фильтрации, `int()` вместо округления, `str(item)`, `url`.
- [Замечания ревью T-006, оставшиеся в коде](facts/t006-review-issues.md) — P2: untracked-файлы не попали в diffstat; P3: граница `_LINK_RE`, URL в логе, `close()` без отмены задач, нет лимита параллелизма, флаки-тест.
- [Замечания ревью T-007, оставшиеся в коде](facts/t007-review-issues.md) — P2: fail-open default, отказ без обёртки и лимита частоты; P3: дубль дедупликации ID, хрупкие проверки логов, неточная версия в CHANGELOG.
- [Замечания ревью T-008, оставшиеся в коде](facts/t008-review-issues.md) — P2: очистка в обход `cli.py`; P3: нет `video_id` в логе, `ExceptionGroup`, `WARNING` при `LOG_LEVEL=ERROR`; P1 T-004/T-005 не закрыты.
- [Замечания ревью T-009, оставшиеся в коде](facts/t009-review-issues.md) — закрыто 5 групп (handoff, дублирование каркаса, сканер секретов, e2e-шум, токен); осталось: README не сверяет негативные `MESSAGE_*`, формулировка про «заметка не создаётся», версия P3; P1 не покрыт E2E.
- [Замечания ревью T-001…T-010: `APPROVE` с восемью пунктами](facts/t001-t010-review-issues.md) — P2: заголовок шаблона в `WARNING`, шум на отсутствии файла, «видео»-текст в `/template`, формат «Таймкодов»/«Цитат»; P3: дубли, `MAX_PATCH_OUTPUT_TOKENS`, sync I/O, границы; FIX не было — всё осталось.
- [Долг ревью копится десять циклов](facts/review-debt-accumulates.md) — 77 пунктов и групп; цикл 10 добавил 8 и не закрыл ни одного (`APPROVE` без FIX); P1 T-004/T-005 всё ещё открыты и невидимы для E2E.
- [Ревью и untracked-файлы](facts/review-diff-untracked.md) — 2/2 сломано до `git add -N`, 7/7 чистых после, 2 регрессии (T-006, T-009); цикл 10 — чистый на 38 файлах, `specs/002` untracked; решение «не откатывать» зафиксировано.
- [Ревьюер в read-only песочнице не может прогнать pytest](facts/reviewer-readonly-tests.md) — 10/10 циклов; в цикле 10 прогнано 158 тестов из 828 (19 %), E2E и хранение не исполнялись, ревью шло 37,9 мин.
- [Интеграция со сторонними библиотеками: сначала проба API](facts/third-party-api-discovery.md) — T-003: 46 % вызовов до кода; T-004: 22 %; T-006: 43,5 %; T-007: 32,6 %; T-008: 35,4 %; T-009: 43,9 % без новой библиотеки.
- [Офлайновые тесты aiogram-обработчика](facts/aiogram-offline-handler-tests.md) — `FakeSession(BaseSession)` + `Dispatcher.feed_update`, `wait_idle`, `threading.Event`; цикл 10 — FSM `MemoryStorage` и 30 сценариев `/template`, `tmp_path` только файловым кейсам.
- [Git в DSH-сессиях сломан двумя переменными](facts/git-env-broken-in-dsh.md) — 10/10 циклов; в цикле 10 — 18 упавших вызовов в 13 из 13 сессий, обёртка не с первого вызова; скил `git-in-sandbox`.

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
- [ADR-011: домен шаблона и хранение JSON v1](decisions/adr-011-template-domain-storage.md) — `NoteTemplate`/`ActiveTemplate`/`TemplateService`, `TemplateRepository` примитивами, `<DATA_DIR>/templates/<user_id>.json`, общий `atomic_write_text`.
- [ADR-012: динамическая заметка и шаблонный промпт](decisions/adr-012-dynamic-note-llm-template.md) — `SectionKind`/`NoteSection` при сохранённом встроенном пути, строгая валидация ответа, тон как данные, `parse_template` и снимок шаблона.
- [ADR-013: Telegram-сценарий шаблона и проводка](decisions/adr-013-telegram-template-flow.md) — FSM `MemoryStorage`, кандидат только в памяти, allowlist первой строкой, порядок template → link, снимок `ActiveTemplate` в `SummaryService`.
- [ADR-014: гранулярность инкремента](decisions/adr-014-increment-granularity.md) — инкремент = единица ревью/коммита/версии; 2–3 задачи на инкремент, иначе одно ревью и отложенный долг.

## Скилы

- [intake](../skills/intake/SKILL.md) — как уточнять заявки; «Контекст проекта» обновлён после T-010 (шаблон на пользователя, 828 тестов, 0.15.0, P1/P2-долг, подклассы M и 2–3 задачи на инкремент).
- [release-metadata](../skills/release-metadata/SKILL.md) — кто владеет версией и CHANGELOG; sdlc-фикс `902283c` синхронизирует `__init__`; цикл 10 не менял версию в задачах, VERSION поднимет 0.15.0 и даст одну запись.
- [size-forecast](../skills/size-forecast/SKILL.md) — оценка по тестовым файлам и поверхности проверки; M1/M2/M3 (25–40 / 50–75 / 80–115 шагов), батч-ревью, FIX 1,0–1,2 × EXECUTE, REVIEW-2 1,5–2,1 ×.
- [fix-round](../skills/fix-round/SKILL.md) — что делать при `ISSUES` (пять стадий, FIX/REVIEW-2) и при `APPROVE` с замечаниями: FIX не запускается, долг выписывается отдельно.
- [cycle-accounting](../skills/cycle-accounting/SKILL.md) — как восстановить расход по кэшу DSH: батч-цикл (подготовка + N × EXECUTE + одно ревью), FIX/REVIEW-2, ctypes-zstd и честный счёт падений git.
- [offline-tests](../skills/offline-tests/SKILL.md) — офлайновые тесты: fake_env, фейковые адаптеры, aiogram `FakeSession`, E2E через `build_application`, FSM-сценарий шаблона, 828 тестов, README под тестами.
- [review-handoff](../skills/review-handoff/SKILL.md) — `git add -N` + `git diff HEAD`, сверка статуса с diffstat, read-only pytest (158/828), батч-ревью, граница `specs/`, два раунда ревью.
- [git-in-sandbox](../skills/git-in-sandbox/SKILL.md) — обёртка `git_ws`; в цикле 10 — 18 упавших вызовов в 13 из 13 сессий, обёртка снова не с первого вызова.
- [shared-helpers](../skills/shared-helpers/SKILL.md) — правило трёх копий для `_one_line`/`format_timecode`, нормализации env и тестового каркаса; `text.py` появился, но лимиты и метки шаблона снова разошлись.

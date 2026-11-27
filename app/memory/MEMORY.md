# Память приложения — индекс

Факты и решения, накопленные этапом LEARN. Одна строка на файл. Обновлён
после цикла 12: T-012 (голосовой ввод шаблона, M→M4) выполнен одним циклом
из бэклога — 134 шага EXECUTE + 63 REVIEW, 99,1/36,5 тыс. output, 26,58 млн
токенов, 34,9 мин; `APPROVE` с семью P3 (FIX=0), 927 тестов, версия
0.16.0 → 0.17.0; долг 83 → ≈87 (3 группы T-011 закрыты), handoff чистый
(8/8), но потолок M3 сломан (197 шагов), а бэклог пуст — следующий
инкремент нужно планировать из долга, 2–3 задачи (ADR-014).

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
- [Расход цикла 11: одиночная M3 — адаптер речи (T-011)](facts/cycle-11-spend.md) — 125 шагов (90 + 35), 45,7/35,6 тыс. output, 9,87 млн токенов, 19,6 мин; ревью 28 % шагов и 43,8 % output, `APPROVE`, FIX=0; 741 строка вне diffstat.
- [Расход цикла 12: одиночная M4 — голосовой ввод (T-012)](facts/cycle-12-spend.md) — 197 шагов (134 + 63), 99,1/36,5 тыс. output, 26,58 млн токенов, 34,9 мин; 16 файлов и +1547/−43, 927 тестов, ревью 32 % шагов / 26,9 % output, `APPROVE` с 7 P3, FIX=0; handoff чистый, git 2/23.
- [Инкремент из десяти задач = один цикл, одно ревью, ноль FIX](facts/batch-increment-single-review.md) — оркестратор исполняет весь «Текущий инкремент»; ревью 37,9 мин и 158/828 тестов, 8 замечаний без FIX, 10,4 тыс. строк без промежуточных коммитов; правило — 2–3 задачи на инкремент (ADR-014).
- [Одиночная задача из бэклога: циклы 11–12 и цена режима «одна M»](facts/single-task-pull-from-backlog.md) — `pull_from_backlog` берёт одну задачу; T-011 — 125 шагов (+42 % к батчу на задачу), T-012 — 197 (+124 %), ревью 63 против 5,6; две подряд дают 161 шаг на задачу (+83 %); бэклог пуст — планировать 2–3 задачи.
- [LEARN стоит как половина задачи и в бюджет не входит](facts/learn-cost-unaccounted.md) — циклы 1–9: 51–126+ шагов; цикл 10 — ≥ 168/132 тыс.; цикл 11 — 144 шага, 110,4 тыс. output; цикл 12 — ≥ 181 шага и ≥ 108 тыс. при 197 шагах цикла (≈ весь цикл); цена LEARN растёт с памятью (62 факта, 16 ADR, 11 скилов).
- [Оценка M стабилизировалась, но раунд ревью ломает вилку](facts/estimate-m-stabilized.md) — EXECUTE 58–70 / REVIEW 23–40 до T-008; T-011 — REVIEW 1 018 output/шаг против 508 у EXECUTE, T-012 — 740 и 579 (структура вернулась к «EXECUTE дороже»); output/шаг 488–1 021, ревью считать по диффу.
- [Калибровка размеров: M стабильна, S не дешевле M, раунд ревью не учтён](facts/estimate-calibration-m.md) — замеры M: 32–134 шагов (T-012 — 134 + 63), медиана последних 12 — ≈78; подклассы M1/M2/M3 и новый M4 (120–160); T-011 — M3, T-012 — M4.
- [Метка M покрывает 32–134 шага: систематический промах оценок](facts/estimate-m-bucket-wide.md) — замеры M 32–134: T-002 32 (M1), T-009 103 и T-010 105 (M3), T-011 90 + 35 = 125, T-012 134 + 63 = 197 (M4); драйвер — тестовые файлы, E2E/README, закрытие долгов; M3 — не потолок.
- [Оценка M3 систематически занижена: T-012 — 134 EXECUTE и 197 за цикл](facts/estimate-m3-ceiling-broken.md) — прогноз M3 105–155 / 65–95 тыс. промахнулся на +27 % / +43 %; причина — 1161 строка тестов, 2 новых файла, `support.py`, E2E и README; новый M4 — 120–160 EXECUTE + 50–75 REVIEW, иначе дробить.
- [Метка M из бэклога — не прогноз: T-011 оказалась M3, T-012 — M4](facts/estimate-backlog-label-stale.md) — задача лежала в бэклоге без подкласса, ANALYZE не было; EXECUTE 90 = M3 (промах плоской M 1,14–1,56×), T-012 — 197 при прогнозе 105–155; подкласс писать уже в бэклоге, M3 расширить до M4.
- [Прогнозы занижены для новой интеграции и для «S» с широкими тестами](facts/estimate-forecast-underestimates.md) — T-004: 100 против 50–70; T-007: 106 против ≤ 20; T-008: 178 против 85–110; T-009: 178 против 95–140; в цикле 10 агрегат «10 × M» попал случайно — EXECUTE перебрал, ревью недобрало.
- [Метка S рухнула: три «S» стоили 106, 178 и 178 шагов](facts/estimate-size-s-collapsed-into-m.md) — T-007, T-008 и T-009 (1 строка продакшна); виновата ширина тестов и поверхности проверки, а не строки.
- [Оценки размера систематически занижены: 9 замеров](facts/estimate-misses-fix-round.md) — S в среднем дороже M в 1,8 раза; FIX 1,0–1,2 × EXECUTE и REVIEW-2 1,5–2,1 × REVIEW-1, а не резерв 0,3–0,5.
- [Ревью с ISSUES: раунд исправлений стоит как сама работа](facts/review-fix-round-cost.md) — T-008 FIX 1,15 × EXECUTE, T-009 1,20 ×; REVIEW-2 в 2,1–2,3 раза дороже первого прохода; 73–77 % шагов; цикл 10 — обратный случай `APPROVE` без FIX (6,4 % шагов, долг +8).
- [Тесты очистки логов были зелёными по чужой причине](facts/secret-redaction-tests.md) — фикстура с `sk-` маскировала `Bearer`, отступ обходил очистку; правило форм из реального кода, мутации и тестов в обе стороны.
- [Версия и CHANGELOG: двух авторов свели к одному](facts/version-changelog-two-authors.md) — sdlc-фикс `902283c` синхронизирует `__init__` и гоняет гейты после bump; цикл 11 — 0.15.0 → 0.16.0, цикл 12 — 0.16.0 → 0.17.0, по одной записи; дублей нет.
- [Композиция готовых контрактов дешевле новой интеграции](facts/composition-is-cheaper-than-integration.md) — T-005: 94 шага и 79 тыс. output без нового внешнего контракта; триггер L — новая поверхность.
- [Задача без продакшн-кода стоит как M](facts/tests-and-docs-cost-like-code.md) — T-009: 1 строка кода, 662 строки тестов, 178 шагов; T-010: E2E 657 строк + README, 105 шагов; T-012: 1161 строка тестов против 347 кода, 134 шага; планировать по тестовым файлам, E2E и `support.py` — отдельные слагаемые.
- [Базовая линия TLDW после T-001](facts/tldw-baseline-after-t001.md) — исторический срез: каркас, env-конфиг, 58 тестов.
- [Базовая линия TLDW после T-002](facts/tldw-baseline-after-t002.md) — исторический срез: модель `Note`, Obsidian-рендер, 116 тестов.
- [Базовая линия TLDW после T-003](facts/tldw-baseline-after-t003.md) — исторический срез: YouTube-адаптер, 163 теста.
- [Базовая линия TLDW после T-004](facts/tldw-baseline-after-t004.md) — исторический срез: LLM-адаптер, промпт, повторы, 212 тестов.
- [Базовая линия TLDW после T-005](facts/tldw-baseline-after-t005.md) — исторический срез: `SummaryService.process`, `storage/notes.py`, 257 тестов.
- [Базовая линия TLDW после T-006](facts/tldw-baseline-after-t006.md) — исторический срез: `handlers/link.py`, `to_thread`, отправка `.md`, 282 теста.
- [Базовая линия TLDW после T-007](facts/tldw-baseline-after-t007.md) — исторический срез: `access.py`, 309 тестов, 0.10.0.
- [Базовая линия TLDW после T-008](facts/tldw-baseline-after-t008.md) — исторический срез: `errors.py`, 343 теста; P1 T-004/T-005 открыты.
- [Базовая линия TLDW после T-009](facts/tldw-baseline-after-t009.md) — исторический срез: E2E + README, `tests/support.py`, 371 тест, 0.13.0; MVP закрыт, P1 T-004/T-005 не покрыт E2E.
- [Базовая линия TLDW после T-010](facts/tldw-baseline-after-t010.md) — срез: пользовательский шаблон (`/template`, FSM, JSON v1 на user ID, динамический рендер и промпт), 828 тестов в отчёте (фактический baseline 832, уточнено в цикле 11), 0.14.0 → 0.15.0; 8 пунктов ревью и P1 T-004/T-005 открыты.
- [Базовая линия TLDW после T-011](facts/tldw-baseline-after-t011.md) — исторический срез: `SpeechAdapter` (331 строка, необязательные `SPEECH_*`, лимит 60 с, typed ошибки, приватные логи), 893 теста, 0.15.0 → 0.16.0; голос в Telegram ещё не подключён (T-012), 83 пункта долга.
- [Базовая линия TLDW после T-012](facts/tldw-baseline-after-t012.md) — актуальный срез: голосовое описание шаблона (`handle_voice`, allowlist → состояние → `is_configured` → лимит 60 с → `BytesIO` → `to_thread` → общий `_present_description`), 927 тестов, 0.16.0 → 0.17.0; P1 T-004/T-005 и P2 `InvalidURL` речи открыты, ≈87 пунктов долга, бэклог пуст.
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
- [Замечания ревью T-011: `APPROVE` с шестью пунктами](facts/t011-review-issues.md) — P2: 741 строка вне diffstat и `httpx.InvalidURL` мимо `SpeechError`; P3: `inf/nan` в конфиге, порядок «длительность → конфигурация», `media_type`, логи/тесты; в цикле 12 закрыты 3 группы (handoff, inf/nan, порядок), P2 `InvalidURL` и `media_type` остались.
- [Замечания ревью T-012: `APPROVE` с семью P3](facts/t012-review-issues.md) — неполнота тестов (нет «ненастроенный + 61 с»), дубль текста лимита, два пути импорта константы, дубль списка состояний, ссылка в голосовом описании не отбивается, порядок «состояние → `is_configured`», дубль `get_file_requests`; FIX не было — всё осталось.
- [Долг ревью копится двенадцать циклов](facts/review-debt-accumulates.md) — ≈87 пунктов и групп; цикл 12 добавил 7 P3 и закрыл 3 группы T-011 (нетто +4), FIX снова не было; P1 T-004/T-005 и `InvalidURL` в LLM/речи открыты, бэклог пуст.
- [Правка `specs/*/tasks.md` в диффе — артефакт `pull_from_backlog`](facts/review-spec-edit-artifact.md) — перенос `## Бэклог` под выполненную задачу повторялся в T-011 и T-012; проверять `git show <base> -- specs/`, не считать нарушением границ; ревьюер тратит 3–4 шага зря.
- [Ревью и untracked-файлы](facts/review-diff-untracked.md) — 2/2 сломано до `git add -N`, 8/8 чистых после, 3 регрессии (T-006, T-009, T-011); цикл 12 — `add -N` для двух новых тестовых файлов, 16 файлов в diffstat; решение «не откатывать specs» зафиксировано.
- [Ревьюер в read-only песочнице не может прогнать pytest](facts/reviewer-readonly-tests.md) — 12/12 циклов; цикл 12 — 703 из 927 (76 %) и 7,3 мин, E2E не исполнялся (пишет `DATA_DIR`); в цикле 11 — 685/893 (77 %) и 6,6 мин, в батче — 158/828 (19 %).
- [Интеграция со сторонними библиотеками: сначала проба API](facts/third-party-api-discovery.md) — T-003: 46 % вызовов до кода; T-004: 22 %; T-006: 43,5 %; T-007: 32,6 %; T-008: 35,4 %; T-009: 43,9 %; T-011: 33,7 % без новой библиотеки.
- [`httpx.InvalidURL` вылетает мимо типизированных ошибок в каждом адаптере](facts/invalidurl-escapes-every-adapter.md) — T-004 (LLM, P1) и T-011 (speech, P2): `except HTTPError` не ловит `InvalidURL`; конфиг проверяет только префикс URL; в цикле 12 не закрыт — правило — ловить `InvalidURL` и валидировать host/port.
- [Офлайновые тесты aiogram-обработчика](facts/aiogram-offline-handler-tests.md) — `FakeSession(BaseSession)` + `Dispatcher.feed_update`, `wait_idle`, `threading.Event`; цикл 10 — FSM `MemoryStorage` и 30 сценариев `/template`, цикл 12 — аудио в `FakeSession` и `FakeSpeechTranscriber`, `tmp_path` только файловым кейсам.
- [Git в DSH-сессиях сломан двумя переменными](facts/git-env-broken-in-dsh.md) — 12/12 циклов; в цикле 12 — 2 упавших вызова из 23 (developer 0/9, обёртка с первого вызова; reviewer 2/14), самый чистый цикл; скил `git-in-sandbox`.

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
- [ADR-014: гранулярность инкремента](decisions/adr-014-increment-granularity.md) — инкремент = единица ревью/коммита/версии; 2–3 задачи на инкремент, иначе одно ревью и отложенный долг; одна задача допустима для риска/безопасности.
- [ADR-015: распознавание речи — необязательная граница и текстовый fallback](decisions/adr-015-speech-adapter.md) — `SpeechAdapter`, необязательные `SPEECH_*`, один запрос, лимит 60 с, typed `SpeechError`, аудио/текст не в логе; T-012 встраивает голос в сценарий шаблона.
- [ADR-016: голосовое описание переиспользует текстовый сценарий](decisions/adr-016-voice-input-flow.md) — `handle_voice` в том же `TemplateHandler`, те же FSM-состояния и `_present_description`; allowlist → состояние → `is_configured` → лимит → `BytesIO` → `to_thread`; без `SPEECH_*` — текстовый fallback, аудио в памяти.

## Скилы

- [intake](../skills/intake/SKILL.md) — как уточнять заявки; «Контекст проекта» обновлён после T-012 (голос подключён к сценарию шаблона, 927 тестов, 0.16.0 → 0.17.0, ≈87 пунктов долга, бэклог пуст, одиночная M4 стоила 197 шагов).
- [optional-boundary](../skills/optional-boundary/SKILL.md) — как включать необязательную возможность (голос, медиа) поверх готового адаптера: `is_configured` до расхода, порядок проверок, текстовый fallback, аудио в памяти, приватные логи, тесты «без настройки» и E2E.
- [adapter-boundary](../skills/adapter-boundary/SKILL.md) — рецепт нового внешнего HTTP-адаптера: необязательные env, полная валидация URL и `InvalidURL`, typed ошибки, один запрос, приватные логи, 40–60 тестов на `MockTransport`, чек-лист ревью.
- [release-metadata](../skills/release-metadata/SKILL.md) — кто владеет версией и CHANGELOG; sdlc-фикс `902283c` синхронизирует `__init__`; цикл 12 — 0.16.0 → 0.17.0, одна запись.
- [size-forecast](../skills/size-forecast/SKILL.md) — оценка по тестовым файлам и поверхности проверки; M1/M2/M3/M4 (25–40 / 50–75 / 80–115 / 120–160 шагов), подкласс уже в бэклоге, ревью по диффу (63 на 16 файлов), FIX 1,0–1,2 × EXECUTE, REVIEW-2 1,5–2,1 ×.
- [fix-round](../skills/fix-round/SKILL.md) — что делать при `ISSUES` (пять стадий, FIX/REVIEW-2) и при `APPROVE` с замечаниями: FIX не запускается, долг выписывается отдельно; P1/P2 прошлого ревью — в критерии следующей задачи.
- [cycle-accounting](../skills/cycle-accounting/SKILL.md) — как восстановить расход по кэшу DSH: батч-цикл, одиночный цикл из бэклога (EXECUTE + REVIEW), FIX/REVIEW-2, ctypes-zstd (многокадровый файл!), честный счёт падений git.
- [offline-tests](../skills/offline-tests/SKILL.md) — офлайновые тесты: fake_env, фейковые адаптеры, aiogram `FakeSession`, E2E через `build_application`, FSM-сценарий шаблона, `MockTransport`/`FakeSpeechTranscriber` для речи, 927 тестов, README под тестами.
- [review-handoff](../skills/review-handoff/SKILL.md) — `git add -N` + `git diff HEAD`, сверка статуса с diffstat, read-only pytest (цикл 12 — 703/927), батч-ревью, граница `specs/` как артефакт оркестратора, два раунда ревью.
- [git-in-sandbox](../skills/git-in-sandbox/SKILL.md) — обёртка `git_ws`; в цикле 12 — 2 упавших вызова из 23, developer применил обёртку с первого вызова (0/9), reviewer — только после двух ошибок.
- [shared-helpers](../skills/shared-helpers/SKILL.md) — правило трёх копий для `_one_line`/`format_timecode`, нормализации env и тестового каркаса; в T-012 разошлись текст лимита, два пути импорта константы и список состояний — P3.

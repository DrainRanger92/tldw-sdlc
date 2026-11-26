# Память приложения — индекс

Факты и решения, накопленные этапом LEARN. Одна строка на файл. Обновлён
после цикла 8 (T-008: понятные ошибки и очистка логов, 343 теста, версия
0.12.0; первый цикл с `ISSUES` и раундом исправлений).

## Факты

- [Расход цикла 1 и калибровка оценки M](facts/cycle-01-spend.md) — T-001 (M): 81 шаг, 73,5 тыс. output, 3,85 млн токенов; первый замер, гипотеза «M ведёт себя как L».
- [Расход цикла 2: specs, T-002 и ревью](facts/cycle-02-spend.md) — подготовка specs + T-002: 83 шага, 3,20 млн токенов до LEARN; T-002 вдвое дешевле T-001, ревью — 40 % расхода задачи.
- [Расход цикла 3: T-003 и ревью](facts/cycle-03-spend.md) — T-003 (M): 89 шагов, 94,1 тыс. output, 5,86 млн токенов; 46 % вызовов — до первой строки кода.
- [Расход цикла 4: T-004 и ревью](facts/cycle-04-spend.md) — T-004 (M): 100 шагов, 113,4 тыс. output, 7,31 млн токенов; самый дорогой по output цикл, ревью — 40 шагов и 37 % output.
- [Расход цикла 5: T-005 и ревью](facts/cycle-05-spend.md) — T-005 (M): 94 шага (66 + 28), 79,2 тыс. output, 7,10 млн токенов; первое попадание прогноза, ревью — 28 шагов и 25 % output.
- [Расход цикла 6: T-006 и ревью](facts/cycle-06-spend.md) — T-006 (M): 101 шаг (62 + 39), 80,7 тыс. output, 6,90 млн токенов; прогноз 80–120/70–100 попал; его прогноз для T-007 (≤ 20 шагов) опровергнут циклом 7.
- [Расход цикла 7: T-007 (allowlist) и ревью](facts/cycle-07-spend.md) — T-007 (S): 106 шагов (70 + 36), 51,7 тыс. output, 5,89 млн токенов; максимум шагов при минимуме output, ревью — 34 % шагов и 39,5 % output.
- [Расход цикла 8: T-008 и два раунда ревью](facts/cycle-08-spend.md) — T-008 (S): 178 шагов (48 + 23 + 55 + 52), 142,1 тыс. output, 10,81 млн токенов; ревью + FIX — 73 % цикла, EXECUTE и REVIEW-1 попали в вилку M, промах в FIX.
- [LEARN стоит как половина задачи и в бюджет не входит](facts/learn-cost-unaccounted.md) — циклы 1–7: 51–117 шагов и 51–78 тыс. output; цикл 7 точно — 99 шагов и 91,5 тыс. (177 % output задачи); LEARN, измеренный до финала, занижает себя на 5–10 %.
- [Оценка M стабилизировалась, но раунд ревью ломает вилку](facts/estimate-m-stabilized.md) — семь замеров EXECUTE 58–70 / REVIEW 23–40; T-008 (48 + 23) в вилке, а FIX (55) и REVIEW-2 (52) — вне её; output на шаг 512–1021.
- [Калибровка размеров: M стабильна, S не дешевле M](facts/estimate-calibration-m.md) — M = EXECUTE 55–70 + REVIEW 20–30 + FIX-резерв; S — только ≤ 2 продакшн-файла и ≤ 1 тестовый; восемь замеров, оба S дали 106 и 178 шагов.
- [Прогнозы занижены для новой интеграции и для «S» с широкими тестами](facts/estimate-forecast-underestimates.md) — T-004: 100 против 50–70; T-007: 106 против ≤ 20; T-008: 178 против 85–110; добавлено пропущенное слагаемое FIX.
- [Метка S рухнула: T-007 стоила как M, и виновата ширина тестов](facts/estimate-size-s-collapsed-into-m.md) — 5 тестовых файлов, 27 кейсов; T-008 — вторая S с 178 шагами и тем же выводом.
- [Оценки размера систематически занижены: 8 замеров](facts/estimate-misses-fix-round.md) — S в среднем дороже M; прогноз без FIX и REVIEW-2 занижен в 1,6–2,1 раза; правило четырёх слагаемых.
- [Ревью с ISSUES: раунд исправлений стоит как половина задачи](facts/review-fix-round-cost.md) — T-008: REVIEW-1 23 шага, FIX 55, REVIEW-2 52; второй проход дороже первого в 2,3 раза по шагам и 4,2 по output.
- [Тесты очистки логов были зелёными по чужой причине](facts/secret-redaction-tests.md) — фикстура с `sk-` маскировала `Bearer`, отступ обходил очистку; правило форм из реального кода, мутации и тестов в обе стороны.
- [Версию и CHANGELOG ведут два автора](facts/version-changelog-two-authors.md) — VERSION-стадия оркестратора поднимает только `pyproject.toml`; HEAD после циклов 6–7 был красным, дубли CHANGELOG 3/8, T-008 выровнял 0.12.0.
- [Композиция готовых контрактов дешевле новой интеграции](facts/composition-is-cheaper-than-integration.md) — T-005: 94 шага и 79 тыс. output без нового внешнего контракта; триггер L — новая поверхность, но S/M зависит ещё и от ширины среза.
- [Базовая линия TLDW после T-001](facts/tldw-baseline-after-t001.md) — исторический срез: каркас, env-конфиг, 58 тестов.
- [Базовая линия TLDW после T-002](facts/tldw-baseline-after-t002.md) — исторический срез: модель `Note`, Obsidian-рендер, имя файла, 116 тестов.
- [Базовая линия TLDW после T-003](facts/tldw-baseline-after-t003.md) — исторический срез: YouTube-адаптер, `parse_video_id`, transcript-api + fallback yt-dlp, 163 теста.
- [Базовая линия TLDW после T-004](facts/tldw-baseline-after-t004.md) — исторический срез: LLM-адаптер (`LLMClient`, `VideoSummary`, ресурсы промпта, повторы 429/5xx), 212 тестов.
- [Базовая линия TLDW после T-005](facts/tldw-baseline-after-t005.md) — исторический срез: `SummaryService.process`, `storage/notes.py`, 257 тестов.
- [Базовая линия TLDW после T-006](facts/tldw-baseline-after-t006.md) — исторический срез: `handlers/link.py`, `to_thread`, отправка `.md`, 282 теста; прогноз S для T-007 опровергнут.
- [Базовая линия TLDW после T-007](facts/tldw-baseline-after-t007.md) — исторический срез: `access.py` (`AccessPolicy`), проверка доступа до парсинга, 309 тестов, версия 0.10.0.
- [Базовая линия TLDW после T-008](facts/tldw-baseline-after-t008.md) — актуальный срез: `errors.py` (`UserFacingError`, `describe_error`, `log_failure`, очистка лога), тексты ошибок, 343 теста, 0.12.0; P1 T-004/T-005 и очистка в `cli.py` ещё открыты.
- [spec.md и plan.md 001-mvp — заготовки](facts/specs-001-artifacts-stubs.md) — исторический факт цикла 1; закрыто в цикле 2.
- [specs/001-mvp: spec.md и plan.md заполнены](facts/specs-001-filled-in-cycle-02.md) — FR-1…FR-11, план этапов 1–7, источник истины; `plan.md` хардкодит текущий инкремент.
- [`plan.md` фиксирует устаревший «текущий инкремент»](facts/plan-md-increment-drift.md) — 7/7 циклов: строка всё ещё называет T-002, отметки «сделано» только у T-001; убрать номер задачи из фразы.
- [Мелкие замечания ревью T-001](facts/t001-review-minor-issues.md) — inf/nan в timeout, дубли тестовых помощников, имя теста, порядок в build_application.
- [Замечания ревью T-002, оставшиеся в коде](facts/t002-review-issues.md) — строка вместо кортежа, bool-таймкод, noncharacters в YAML, slug в байтах, дублирование.
- [Замечания ревью T-003, оставшиеся в коде](facts/t003-review-issues.md) — P1: нет таймаута transcript-api, двойной `_extract_info`, приоритет ошибок, кэш yt-dlp.
- [Замечания ревью T-004, оставшиеся в коде](facts/t004-review-issues.md) — P1: `InvalidURL`/`RuntimeError` мимо `LLMError` (открыт после T-008); `MAX_FOLLOW_UPS` убран из `__all__`; третья копия `_one_line`, prompt injection, версии.
- [Замечания ревью T-005, оставшиеся в коде](facts/t005-review-issues.md) — P1: пустой транскрипт не отвергается (открыт после T-008), вырожденный маркер усечения, учёт при `budget == 0`; P2 — дубль фильтрации, `int()` вместо округления, `str(item)`, `url`.
- [Замечания ревью T-006, оставшиеся в коде](facts/t006-review-issues.md) — P2: untracked-файлы не попали в diffstat; P3: граница `_LINK_RE`, URL в логе, `close()` без отмены задач, нет лимита параллелизма, флаки-тест; долг P1 T-004/T-005 не закрыт.
- [Замечания ревью T-007, оставшиеся в коде](facts/t007-review-issues.md) — P2: fail-open default, отказ без обёртки и лимита частоты; P3: дубль дедупликации ID, хрупкие проверки логов, неточная версия в CHANGELOG; P1-долг T-004/T-005 не закрыт.
- [Долг ревью копится восемь циклов](facts/review-debt-accumulates.md) — 62 пункта за 8 циклов; T-008 закрыла 5 и добавила 6 (P2 очистка в обход `cli.py`, P3 редактор секретов, `video_id`, `ExceptionGroup`); P1 T-004/T-005 всё ещё открыты.
- [Ревью и untracked-файлы](facts/review-diff-untracked.md) — 2/2 сломано до `git add -N`, 5/5 чистых после (T-003–T-005, T-007, T-008), регрессия в цикле 6; в цикле 8 дифф совпал, но правка `specs/` была в диффе 3-й цикл подряд.
- [Ревьюер в read-only песочнице не может прогнать pytest](facts/reviewer-readonly-tests.md) — 8/8 циклов; в цикле 8 полный прогон 243 passed + 93 env-errors, целевые тесты 26 + 9 passed; зелёный прогон не заменяет чтение кода, и наоборот.
- [Интеграция со сторонними библиотеками: сначала проба API](facts/third-party-api-discovery.md) — T-003: 46 % вызовов до кода; T-004: 22 %, стоимость ушла в контракт; T-006: 43,5 %, но aiogram уже стоял; T-007: 32,6 %; T-008: 35,4 % и без новой библиотеки.
- [Офлайновые тесты aiogram-обработчика](facts/aiogram-offline-handler-tests.md) — `FakeSession(BaseSession)` + `Dispatcher.feed_update`, `wait_idle` для фоновых задач, `threading.Event` вместо sleep, `tmp_path` только файловым кейсам.
- [Git в DSH-сессиях сломан двумя переменными](facts/git-env-broken-in-dsh.md) — 8/8 циклов: `GIT_CONFIG_KEY_0` и `dubious ownership`; в цикле 8 — 7 упавших git-вызовов, обёртка не с первого вызова; скил `git-in-sandbox`.

## Решения (ADR)

- [ADR-001: конфигурация только из env](decisions/adr-001-config-env-only.md) — fail-fast, секреты вне repr, `.env` не перетирает env.
- [ADR-002: слои пакета и адаптеры](decisions/adr-002-layered-adapters.md) — handlers → services → adapters/storage, сборка без сети.
- [ADR-003: модель заметки и ручной YAML-рендер](decisions/adr-003-note-model-manual-yaml.md) — frozen dataclasses и чистые функции в `services/note.py`, YAML вручную, PyYAML только в тестах.
- [ADR-004: контракт YouTube-адаптера](decisions/adr-004-youtube-adapter-contract.md) — два источника за протоколами, fallback yt-dlp, типизированные ошибки и их приоритет, метаданные необязательны, адаптер синхронный.
- [ADR-005: контракт LLM-адаптера](decisions/adr-005-llm-adapter-contract.md) — промпт/шаблон как ресурсы пакета, строгая JSON-валидация в `VideoSummary`, повтор только на 429/5xx, `LLMError` и подклассы, синхронный клиент для `to_thread`.
- [ADR-006: контракт сервиса конспекта](decisions/adr-006-summary-service.md) — композиция за протоколами `VideoSource`/`SummaryProvider`, порог до LLM, усечение по сегментам с маркером в лимите, `source` — ссылка пользователя, атомарная запись в `storage/`.
- [ADR-007: Telegram-слой](decisions/adr-007-telegram-handler.md) — тонкий обработчик, «принял» до тяжёлых вызовов, фоновая задача + `asyncio.to_thread`, `FSInputFile` по сохранённому пути, протокол `SummaryProcessor`, инъекция сервиса в сборку.
- [ADR-008: allowlist и правило доступа](decisions/adr-008-allowlist-access-policy.md) — разбор ID в `config`, нейтральный `AccessPolicy` в `access.py`, проверка первой строкой `handle_message`, пустой список = открытый доступ с предупреждением, без aiogram-middleware.
- [ADR-009: пользовательские ошибки и очистка лога](decisions/adr-009-user-facing-errors.md) — нейтральный `errors.py`, `UserFacingError` как маркер известных ошибок, `describe_error`/`log_failure`, ручной формат стектрейса, шаблоны секретов, без глобального `logging.Filter` пока.

## Скилы

- [intake](../skills/intake/SKILL.md) — как уточнять заявки в этом проекте; «Контекст проекта» обновлён после T-008 (ошибки и очистка лога, 343 теста, S = M, FIX-резерв, T-009 и P1-долг).
- [release-metadata](../skills/release-metadata/SKILL.md) — кто владеет версией и CHANGELOG (VERSION-стадия оркестратора), почему тест версии красный в HEAD, что делать разработчику в app-задаче.
- [size-forecast](../skills/size-forecast/SKILL.md) — как оценивать размер и прогнозировать шаги/output по точкам интеграции и тестовым файлам; четыре слагаемых (EXECUTE + REVIEW + FIX + REVIEW-2).
- [cycle-accounting](../skills/cycle-accounting/SKILL.md) — как восстановить расход цикла по кэшу DSH-сессий: шаги, токены, роли, стадии FIX/REVIEW-2 при `ISSUES`, LEARN после закрытия сессии.
- [offline-tests](../skills/offline-tests/SKILL.md) — офлайновые тесты: fake_env, tmp_path, фейковые адаптеры и `MockTransport`, aiogram `FakeSession`, тесты ошибок и очистки логов с мутацией и формами из реального кода.
- [review-handoff](../skills/review-handoff/SKILL.md) — `git add -N` + `git diff HEAD`, сверка `git status -uall` с diffstat, read-only pytest, граница `specs/`, проверка тестов на ложноположительность, бюджет ревью с двумя раундами.
- [git-in-sandbox](../skills/git-in-sandbox/SKILL.md) — обёртка `git_ws`: обход `missing config key GIT_CONFIG_KEY_0` и `dubious ownership`; в цикле 8 применялась после первой ошибки в каждой из 4 сессий.
- [shared-helpers](../skills/shared-helpers/SKILL.md) — правило трёх копий для `_one_line`/`format_timecode`; в T-008 появился общий `_condense`, но `FAKE_TELEGRAM_TOKEN` продублирован в двух тестовых файлах.

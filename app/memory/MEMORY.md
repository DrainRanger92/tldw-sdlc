# Память приложения — индекс

Факты и решения, накопленные этапом LEARN. Одна строка на файл. Обновлён
после цикла 7 (T-007, allowlist, 309 тестов, версия 0.10.0).

## Факты

- [Расход цикла 1 и калибровка оценки M](facts/cycle-01-spend.md) — T-001 (M): 81 шаг, 73,5 тыс. output, 3,85 млн токенов; первый замер, гипотеза «M ведёт себя как L».
- [Расход цикла 2: specs, T-002 и ревью](facts/cycle-02-spend.md) — подготовка specs + T-002: 83 шага, 3,20 млн токенов до LEARN; T-002 вдвое дешевле T-001, ревью — 40 % расхода задачи.
- [Расход цикла 3: T-003 и ревью](facts/cycle-03-spend.md) — T-003 (M): 89 шагов, 94,1 тыс. output, 5,86 млн токенов; 46 % вызовов — до первой строки кода.
- [Расход цикла 4: T-004 и ревью](facts/cycle-04-spend.md) — T-004 (M): 100 шагов, 113,4 тыс. output, 7,31 млн токенов; самый дорогой по output цикл, ревью — 40 шагов и 37 % output.
- [Расход цикла 5: T-005 и ревью](facts/cycle-05-spend.md) — T-005 (M): 94 шага (66 + 28), 79,2 тыс. output, 7,10 млн токенов; первое попадание прогноза, ревью — 28 шагов и 25 % output.
- [Расход цикла 6: T-006 и ревью](facts/cycle-06-spend.md) — T-006 (M): 101 шаг (62 + 39), 80,7 тыс. output, 6,90 млн токенов; прогноз 80–120/70–100 попал; его прогноз для T-007 (≤ 20 шагов) опровергнут циклом 7.
- [Расход цикла 7: T-007 (allowlist) и ревью](facts/cycle-07-spend.md) — T-007 (S): 106 шагов (70 + 36), 51,7 тыс. output, 5,89 млн токенов; максимум шагов при минимуме output, ревью — 34 % шагов и 39,5 % output.
- [LEARN стоит как половина задачи и в бюджет не входит](facts/learn-cost-unaccounted.md) — циклы 1–6: 51–117 шагов и 51–78 тыс. output (63–117 % шага задачи); цикл 7 — ≈95 шагов и ≈87 тыс. (вышел за бюджет из-за широкого обновления), LEARN, измеренный до финала, занижает себя на 5–10 %.
- [Оценка M стабилизировалась, а метка S — нет](facts/estimate-m-stabilized.md) — семь замеров: EXECUTE 58–70, REVIEW 23–40; T-007 (70 + 36) попала в вилку M, хотя была помечена S; output на шаг упал до 488.
- [Калибровка размеров: M стабильна, S не дешевле M](facts/estimate-calibration-m.md) — M = EXECUTE 55–70 + REVIEW 25–40 (80–110 шагов, 55–110 тыс. output); S — только ≤ 2 продакшн-файла и ≤ 1 тестовый, иначе M; старый потолок 40 шагов убран.
- [Прогнозы занижены для новой интеграции и для «S» с широкими тестами](facts/estimate-forecast-underestimates.md) — T-004: 100 против 50–70; T-007: 106 против ≤ 20 (5,3×) и 51,7 тыс. против ≤ 15 тыс. (3,4×); коррекция 1,5–2× — для чужого API, а «S» с 4+ тестовыми файлами — это M.
- [Метка S рухнула: T-007 стоила как M, и виновата ширина тестов](facts/estimate-size-s-collapsed-into-m.md) — 14 файлов, 5 тестовых файлов, 27 кейсов, 285 строк тестов; вилка M (70 + 36) попала бы, ярлык S — нет.
- [Композиция готовых контрактов дешевле новой интеграции](facts/composition-is-cheaper-than-integration.md) — T-005: 94 шага и 79 тыс. output без нового внешнего контракта; триггер L — новая поверхность, но S/M зависит ещё и от ширины среза (T-007).
- [Базовая линия TLDW после T-001](facts/tldw-baseline-after-t001.md) — исторический срез: каркас, env-конфиг, 58 тестов.
- [Базовая линия TLDW после T-002](facts/tldw-baseline-after-t002.md) — исторический срез: модель `Note`, Obsidian-рендер, имя файла, 116 тестов.
- [Базовая линия TLDW после T-003](facts/tldw-baseline-after-t003.md) — исторический срез: YouTube-адаптер, `parse_video_id`, transcript-api + fallback yt-dlp, 163 теста.
- [Базовая линия TLDW после T-004](facts/tldw-baseline-after-t004.md) — исторический срез: LLM-адаптер (`LLMClient`, `VideoSummary`, ресурсы промпта, повторы 429/5xx), 212 тестов.
- [Базовая линия TLDW после T-005](facts/tldw-baseline-after-t005.md) — исторический срез: `SummaryService.process`, `storage/notes.py`, 257 тестов.
- [Базовая линия TLDW после T-006](facts/tldw-baseline-after-t006.md) — исторический срез: `handlers/link.py`, `to_thread`, отправка `.md`, инъекция сервиса в сборку, 282 теста; прогноз S для T-007 опровергнут.
- [Базовая линия TLDW после T-007](facts/tldw-baseline-after-t007.md) — актуальный срез: `access.py` (`AccessPolicy`), проверка доступа до парсинга, проводка allowlist, 309 тестов, версия 0.10.0; T-008–T-009 ещё нет.
- [spec.md и plan.md 001-mvp — заготовки](facts/specs-001-artifacts-stubs.md) — исторический факт цикла 1; закрыто в цикле 2.
- [specs/001-mvp: spec.md и plan.md заполнены](facts/specs-001-filled-in-cycle-02.md) — FR-1…FR-11, план этапов 1–7, источник истины; `plan.md` хардкодит текущий инкремент.
- [`plan.md` фиксирует устаревший «текущий инкремент»](facts/plan-md-increment-drift.md) — 6/6 циклов: строка всё ещё называет T-002, отметки «сделано» только у T-001; убрать номер задачи из фразы.
- [Мелкие замечания ревью T-001](facts/t001-review-minor-issues.md) — inf/nan в timeout, дубли тестовых помощников, имя теста, порядок в build_application.
- [Замечания ревью T-002, оставшиеся в коде](facts/t002-review-issues.md) — строка вместо кортежа, bool-таймкод, noncharacters в YAML, slug в байтах, дублирование.
- [Замечания ревью T-003, оставшиеся в коде](facts/t003-review-issues.md) — P1: нет таймаута transcript-api, двойной `_extract_info`, приоритет ошибок, кэш yt-dlp.
- [Замечания ревью T-004, оставшиеся в коде](facts/t004-review-issues.md) — P1: `InvalidURL`/`RuntimeError` мимо `LLMError` (открыты после T-007); `MAX_FOLLOW_UPS` убран из `__all__`; третья копия `_one_line`, prompt injection, версии.
- [Замечания ревью T-005, оставшиеся в коде](facts/t005-review-issues.md) — P1: пустой транскрипт не отвергается (открыт после T-007), вырожденный маркер усечения, учёт при `budget == 0`; P2 — дубль фильтрации, `int()` вместо округления, `str(item)`, `url`.
- [Замечания ревью T-006, оставшиеся в коде](facts/t006-review-issues.md) — P2: untracked-файлы не попали в diffstat; P3: граница `_LINK_RE`, URL в логе, `close()` без отмены задач, нет лимита параллелизма, флаки-тест; долг P1 T-004/T-005 не закрыт.
- [Замечания ревью T-007, оставшиеся в коде](facts/t007-review-issues.md) — P2: fail-open default, отказ без обёртки и лимита частоты; P3: дубль дедупликации ID, хрупкие проверки логов, неточная версия в CHANGELOG; P1-долг T-004/T-005 не закрыт.
- [Долг ревью копится семь циклов](facts/review-debt-accumulates.md) — 51 пункт за 7 циклов, все APPROVE, отдельной задачи на долг нет; T-007 добавила 2 P2 и 3 P3, P1 T-004/T-005 маскируются `except Exception`.
- [Ревью и untracked-файлы](facts/review-diff-untracked.md) — 2/2 цикла сломано до `git add -N`, 4/4 чистых после (T-003–T-005, T-007), регрессия в цикле 6; в цикле 7 14 путей совпали с diffstat.
- [Ревьюер в read-only песочнице не может прогнать pytest](facts/reviewer-readonly-tests.md) — 7/7 циклов; в цикле 7 новый `test_access.py` прошёл целиком (15 кейсов), 34 passed + 9 env-errors в двух файлах; APPROVE не равен зелёным тестам.
- [Интеграция со сторонними библиотеками: сначала проба API](facts/third-party-api-discovery.md) — T-003: 46 % вызовов до кода; T-004: 22 %, стоимость ушла в контракт; T-006: 43,5 %, но aiogram уже стоял; T-007: 32,6 % и без новой библиотеки.
- [Офлайновые тесты aiogram-обработчика](facts/aiogram-offline-handler-tests.md) — `FakeSession(BaseSession)` + `Dispatcher.feed_update`, `wait_idle` для фоновых задач, `threading.Event` вместо sleep, `tmp_path` только файловым кейсам.
- [Git в DSH-сессиях сломан двумя переменными](facts/git-env-broken-in-dsh.md) — 7/7 циклов: `GIT_CONFIG_KEY_0` и `dubious ownership`; в цикле 7 developer — 7 git-вызовов, reviewer — 10, без диагностики; скил `git-in-sandbox`.

## Решения (ADR)

- [ADR-001: конфигурация только из env](decisions/adr-001-config-env-only.md) — fail-fast, секреты вне repr, `.env` не перетирает env.
- [ADR-002: слои пакета и адаптеры](decisions/adr-002-layered-adapters.md) — handlers → services → adapters/storage, сборка без сети.
- [ADR-003: модель заметки и ручной YAML-рендер](decisions/adr-003-note-model-manual-yaml.md) — frozen dataclasses и чистые функции в `services/note.py`, YAML вручную, PyYAML только в тестах.
- [ADR-004: контракт YouTube-адаптера](decisions/adr-004-youtube-adapter-contract.md) — два источника за протоколами, fallback yt-dlp, типизированные ошибки и их приоритет, метаданные необязательны, адаптер синхронный.
- [ADR-005: контракт LLM-адаптера](decisions/adr-005-llm-adapter-contract.md) — промпт/шаблон как ресурсы пакета, строгая JSON-валидация в `VideoSummary`, повтор только на 429/5xx, `LLMError` и подклассы, синхронный клиент для `to_thread`.
- [ADR-006: контракт сервиса конспекта](decisions/adr-006-summary-service.md) — композиция за протоколами `VideoSource`/`SummaryProvider`, порог до LLM, усечение по сегментам с маркером в лимите, `source` — ссылка пользователя, атомарная запись в `storage/`.
- [ADR-007: Telegram-слой](decisions/adr-007-telegram-handler.md) — тонкий обработчик, «принял» до тяжёлых вызовов, фоновая задача + `asyncio.to_thread`, `FSInputFile` по сохранённому пути, протокол `SummaryProcessor`, инъекция сервиса в `build_application`, общее сообщение об ошибке до T-008.
- [ADR-008: allowlist и правило доступа](decisions/adr-008-allowlist-access-policy.md) — разбор ID в `config`, нейтральный `AccessPolicy` в `access.py`, проверка первой строкой `handle_message`, пустой список = открытый доступ с предупреждением, без aiogram-middleware.

## Скилы

- [intake](../skills/intake/SKILL.md) — как уточнять заявки в этом проекте; «Контекст проекта» обновлён после T-007 (allowlist, 309 тестов, S = M, fail-open default, T-008/T-009 остались).
- [size-forecast](../skills/size-forecast/SKILL.md) — как оценивать размер и прогнозировать шаги/output/ревью по точкам интеграции и тестовым файлам; вилки M, красные флаги «S» с 4+ тестовыми файлами.
- [cycle-accounting](../skills/cycle-accounting/SKILL.md) — как восстановить расход цикла по кэшу DSH-сессий и журналам: шаги, токены, роли, LEARN отдельно и после закрытия сессии, сверка с прогнозом двумя слагаемыми.
- [offline-tests](../skills/offline-tests/SKILL.md) — офлайновые тесты: fake_env, tmp_path для DATA_DIR, фейковые адаптеры и `httpx.MockTransport`, фейковая сессия aiogram + `Dispatcher.feed_update`, `wait_idle`, чистые тесты политики без aiogram.
- [review-handoff](../skills/review-handoff/SKILL.md) — `git add -N` + `git diff HEAD`, сверка `git status -uall` с diffstat, read-only pytest (`-p no:cacheprovider -s`), граница `specs/`, бюджет ревью по числу тестовых файлов.
- [git-in-sandbox](../skills/git-in-sandbox/SKILL.md) — обёртка `git_ws`: обход `missing config key GIT_CONFIG_KEY_0` и `dubious ownership` в DSH-сессиях; в цикле 7 применялась с первого вызова.
- [shared-helpers](../skills/shared-helpers/SKILL.md) — правило трёх копий для `_one_line`/`format_timecode`, единый источник числовых лимитов и нормализации списков из env; в T-007 дедупликация ID продублирована в `config` и `AccessPolicy`.

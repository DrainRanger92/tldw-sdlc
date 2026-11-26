# Память приложения — индекс

Факты и решения, накопленные этапом LEARN. Одна строка на файл.

## Факты

- [Расход цикла 1 и калибровка оценки M](facts/cycle-01-spend.md) — T-001 (M): 81 шаг, 73,5 тыс. output, 3,85 млн токенов; первый замер, гипотеза «M ведёт себя как L».
- [Расход цикла 2: specs, T-002 и ревью](facts/cycle-02-spend.md) — подготовка specs + T-002: 83 шага, 3,20 млн токенов до LEARN; T-002 вдвое дешевле T-001, ревью — 40 % расхода задачи.
- [Расход цикла 3: T-003 и ревью](facts/cycle-03-spend.md) — T-003 (M): 89 шагов, 94,1 тыс. output, 5,86 млн токенов; 46 % вызовов — до первой строки кода.
- [Расход цикла 4: T-004 и ревью](facts/cycle-04-spend.md) — T-004 (M): 100 шагов, 113,4 тыс. output, 7,31 млн токенов; самый дорогой по output цикл, ревью — 40 шагов и 37 % output.
- [Расход цикла 5: T-005 и ревью](facts/cycle-05-spend.md) — T-005 (M): 94 шага (66 + 28), 79,2 тыс. output, 7,10 млн токенов; первый цикл, где прогноз не занижен, ревью — 28 шагов и 25 % output.
- [LEARN стоит как половина задачи и в бюджет не входит](facts/learn-cost-unaccounted.md) — циклы 1–4: 51–117 шагов и 50–70 тыс. output на ретроспективу (63–117 % шага задачи); считать «до LEARN» и «с LEARN» отдельно.
- [Калибровка размера M: четыре промаха и первое попадание](facts/estimate-calibration-m.md) — M = 53–100 шагов; композиция готовых контрактов — M (94/79 тыс.), новая внешняя поверхность — L и дробить.
- [Прогнозы занижены для новых интеграций, а не для любой M](facts/estimate-forecast-underestimates.md) — T-004: 100 шагов против вилки 50–70; T-005: 94 против 80–120 и 79 тыс. против 90–130 тыс.; корректировать на 1,5–2× только при новой поверхности.
- [Композиция готовых контрактов дешевле новой интеграции](facts/composition-is-cheaper-than-integration.md) — T-005: 94 шага и 79 тыс. output без единого нового внешнего контракта; триггер L — новая поверхность, а не число слоёв; строки тестов не считать.
- [Базовая линия TLDW после T-001](facts/tldw-baseline-after-t001.md) — исторический срез: каркас, env-конфиг, 58 тестов.
- [Базовая линия TLDW после T-002](facts/tldw-baseline-after-t002.md) — исторический срез: модель `Note`, Obsidian-рендер, имя файла, 116 тестов.
- [Базовая линия TLDW после T-003](facts/tldw-baseline-after-t003.md) — исторический срез: YouTube-адаптер, `parse_video_id`, transcript-api + fallback yt-dlp, 163 теста.
- [Базовая линия TLDW после T-004](facts/tldw-baseline-after-t004.md) — исторический срез: LLM-адаптер (`LLMClient`, `VideoSummary`, ресурсы промпта, повторы 429/5xx), 212 тестов.
- [Базовая линия TLDW после T-005](facts/tldw-baseline-after-t005.md) — актуальный срез: `SummaryService.process` (порог 2 ч, транскрипт с таймкодами, усечение с маркером), `storage/notes.py` (атомарная запись), 257 тестов; T-006–T-009 ещё нет.
- [spec.md и plan.md 001-mvp — заготовки](facts/specs-001-artifacts-stubs.md) — исторический факт цикла 1; закрыто в цикле 2.
- [specs/001-mvp: spec.md и plan.md заполнены](facts/specs-001-filled-in-cycle-02.md) — FR-1…FR-11, план этапов 1–7, источник истины; `plan.md` хардкодит текущий инкремент.
- [`plan.md` фиксирует устаревший «текущий инкремент»](facts/plan-md-increment-drift.md) — 4/4 цикла после появления плана: строка всё ещё называет T-002; убрать номер задачи из фразы.
- [Мелкие замечания ревью T-001](facts/t001-review-minor-issues.md) — inf/nan в timeout, дубли тестовых помощников, имя теста, порядок в build_application.
- [Замечания ревью T-002, оставшиеся в коде](facts/t002-review-issues.md) — строка вместо кортежа, bool-таймкод, noncharacters в YAML, slug в байтах, дублирование.
- [Замечания ревью T-003, оставшиеся в коде](facts/t003-review-issues.md) — P1: нет таймаута transcript-api, двойной `_extract_info`, приоритет ошибок, кэш yt-dlp.
- [Замечания ревью T-004, оставшиеся в коде](facts/t004-review-issues.md) — P1: `InvalidURL`/`RuntimeError` мимо `LLMError` (открыты после T-005); `MAX_FOLLOW_UPS` из `__all__` убран; третья копия `_one_line`, prompt injection, версии.
- [Замечания ревью T-005, оставшиеся в коде](facts/t005-review-issues.md) — P1: пустой транскрипт не отвергается, вырожденный маркер усечения, учёт при `budget == 0`; P2 — дубль фильтрации, `int()` вместо округления, `str(item)`, `url`.
- [Долг ревью копится пять циклов](facts/review-debt-accumulates.md) — 4/7/10/7/8 замечаний, 36 всего, все APPROVE, отдельной задачи на долг нет; закрыт 1 пункт из T-004.
- [Ревью и untracked-файлы](facts/review-diff-untracked.md) — 2/2 цикла до `git add -N`, 3/3 после; в цикле 5 добавилась обратная сторона: `git diff` пуст, нужен `git diff HEAD`.
- [Ревьюер в read-only песочнице не может прогнать pytest](facts/reviewer-readonly-tests.md) — 5/5 циклов; в цикле 5 pytest падал в capture, `-p no:cacheprovider -s` дал 16 passed + 29 env-errors; APPROVE не равен зелёным тестам.
- [Интеграция со сторонними библиотеками: сначала проба API](facts/third-party-api-discovery.md) — T-003: 46 % вызовов до кода; T-004: 22 %, но стоимость ушла в контракт и валидацию; в T-005 новых библиотек не было.
- [Git в DSH-сессиях сломан двумя переменными](facts/git-env-broken-in-dsh.md) — 5/5 циклов: `GIT_CONFIG_KEY_0` и `dubious ownership`; обёртка `git_ws`, 5–15 шагов на сессию; скил `git-in-sandbox`.

## Решения (ADR)

- [ADR-001: конфигурация только из env](decisions/adr-001-config-env-only.md) — fail-fast, секреты вне repr, `.env` не перетирает env.
- [ADR-002: слои пакета и адаптеры](decisions/adr-002-layered-adapters.md) — handlers → services → adapters/storage, сборка без сети.
- [ADR-003: модель заметки и ручной YAML-рендер](decisions/adr-003-note-model-manual-yaml.md) — frozen dataclasses и чистые функции в `services/note.py`, YAML вручную, PyYAML только в тестах.
- [ADR-004: контракт YouTube-адаптера](decisions/adr-004-youtube-adapter-contract.md) — два источника за протоколами, fallback yt-dlp, типизированные ошибки и их приоритет, метаданные необязательны, адаптер синхронный.
- [ADR-005: контракт LLM-адаптера](decisions/adr-005-llm-adapter-contract.md) — промпт/шаблон как ресурсы пакета, строгая JSON-валидация в `VideoSummary`, повтор только на 429/5xx, `LLMError` и подклассы, синхронный клиент для `to_thread`.
- [ADR-006: контракт сервиса конспекта](decisions/adr-006-summary-service.md) — композиция за протоколами `VideoSource`/`SummaryProvider`, порог до LLM, усечение по сегментам с маркером в лимите, `source` — ссылка пользователя, атомарная запись в `storage/`.

## Скилы

- [intake](../skills/intake/SKILL.md) — как уточнять заявки в этом проекте; «Контекст проекта» обновлён после T-005 (сервис конспекта, 257 тестов, композиция → M, новая поверхность → L).
- [cycle-accounting](../skills/cycle-accounting/SKILL.md) — как восстановить расход цикла по кэшу DSH-сессий и журналам: шаги, токены, роли, LEARN отдельно, сверка с оценкой.
- [offline-tests](../skills/offline-tests/SKILL.md) — офлайновые тесты: fake_env, tmp_path для DATA_DIR, фейковые клиенты и адаптеры сервиса, `httpx.MockTransport`, ресурсы пакета, инъекция `sleep`.
- [review-handoff](../skills/review-handoff/SKILL.md) — `git add -N` + `git diff HEAD`, сверка `git status -uall` с diffstat, read-only pytest (`-p no:cacheprovider -s`), граница `specs/`, бюджет ревью.
- [git-in-sandbox](../skills/git-in-sandbox/SKILL.md) — обёртка `git_ws`: обход `missing config key GIT_CONFIG_KEY_0` и `dubious ownership` в DSH-сессиях.
- [shared-helpers](../skills/shared-helpers/SKILL.md) — правило трёх копий для `_one_line`/`format_timecode`, единый источник числовых лимитов, нейтральный модуль без инверсии слоёв; в T-005 новых копий не появилось.

# Память приложения — индекс

Факты и решения, накопленные этапом LEARN. Одна строка на файл.

## Факты

- [Расход цикла 1 и калибровка оценки M](facts/cycle-01-spend.md) — T-001 (M): 81 шаг, 73,5 тыс. output, 3,85 млн токенов; первый замер, гипотеза «M ведёт себя как L».
- [Расход цикла 2: specs, T-002 и ревью](facts/cycle-02-spend.md) — подготовка specs + T-002: 83 шага, 3,20 млн токенов до LEARN; T-002 вдвое дешевле T-001, ревью — 40 % расхода задачи.
- [Расход цикла 3: T-003 и ревью](facts/cycle-03-spend.md) — T-003 (M): 89 шагов, 94,1 тыс. output, 5,86 млн токенов; 46 % вызовов — до первой строки кода.
- [Расход цикла 4: T-004 и ревью](facts/cycle-04-spend.md) — T-004 (M): 100 шагов, 113,4 тыс. output, 7,31 млн токенов; самый дорогой цикл, ревью — 40 шагов и 37 % суммарного output.
- [Калибровка размера M: четыре промаха подряд](facts/estimate-calibration-m.md) — M = де-факто L: 53–100 шагов и 57–113 тыс. output; планировать 60–100/60–110 тыс., дробить при новой внешней интеграции, повторах или строгой валидации.
- [Прогнозы стоимости систематически занижены](facts/estimate-forecast-underestimates.md) — прогноз T-004 50–70 шагов / 60–80 тыс. output против факта 100/113 тыс.; корректировать прогноз на 1,5–2× и переоценивать размер перед EXECUTE.
- [Базовая линия TLDW после T-001](facts/tldw-baseline-after-t001.md) — исторический срез: каркас, env-конфиг, 58 тестов.
- [Базовая линия TLDW после T-002](facts/tldw-baseline-after-t002.md) — исторический срез: модель `Note`, Obsidian-рендер, имя файла, 116 тестов.
- [Базовая линия TLDW после T-003](facts/tldw-baseline-after-t003.md) — исторический срез: YouTube-адаптер, `parse_video_id`, transcript-api + fallback yt-dlp, 163 теста.
- [Базовая линия TLDW после T-004](facts/tldw-baseline-after-t004.md) — актуальный срез: LLM-адаптер (`LLMClient`, `VideoSummary`, промпт/шаблон в ресурсах, повторы 429/5xx), 212 тестов; T-005–T-009 ещё нет.
- [spec.md и plan.md 001-mvp — заготовки](facts/specs-001-artifacts-stubs.md) — исторический факт цикла 1; закрыто в цикле 2.
- [specs/001-mvp: spec.md и plan.md заполнены](facts/specs-001-filled-in-cycle-02.md) — FR-1…FR-11, план этапов 1–7, источник истины; `plan.md` хардкодит текущий инкремент.
- [`plan.md` фиксирует устаревший «текущий инкремент»](facts/plan-md-increment-drift.md) — 3/3 цикла после появления плана: строка всё ещё называет T-002; убрать номер задачи из фразы.
- [Мелкие замечания ревью T-001](facts/t001-review-minor-issues.md) — inf/nan в timeout, дубли тестовых помощников, имя теста, порядок в build_application.
- [Замечания ревью T-002, оставшиеся в коде](facts/t002-review-issues.md) — строка вместо кортежа, bool-таймкод, noncharacters в YAML, slug в байтах, дублирование; кандидаты в T-005.
- [Замечания ревью T-003, оставшиеся в коде](facts/t003-review-issues.md) — P1: нет таймаута transcript-api, двойной `_extract_info`, приоритет ошибок, кэш yt-dlp; закрыть до T-006.
- [Замечания ревью T-004, оставшиеся в коде](facts/t004-review-issues.md) — P1: `InvalidURL`/`RuntimeError` мимо `LLMError`; `MAX_FOLLOW_UPS` в `__all__`, третья копия `_one_line`, пробелы тестов, prompt injection, версии.
- [Долг ревью копится четыре цикла](facts/review-debt-accumulates.md) — 4/7/10/7 замечаний, 28 всего, все APPROVE, отдельной задачи на долг нет; P1 уйдут в T-005/T-006.
- [Ревью не видит untracked-файлы](facts/review-diff-untracked.md) — 2/2 цикла до `git add -N`; в циклах 3–4 процедура сработала, ревьюер увидел все пути; скил `review-handoff`.
- [Ревьюер в read-only песочнице не может прогнать pytest](facts/reviewer-readonly-tests.md) — 4/4 цикла; в цикле 4 полный прогон недоступен, `test_llm_adapter.py` — 48 passed; APPROVE не равен зелёным тестам.
- [Интеграция со сторонними библиотеками: сначала проба API](facts/third-party-api-discovery.md) — T-003: 46 % вызовов до кода; T-004: 22 %, но стоимость ушла в контракт, валидацию и тесты; проба на 2–3 шага.
- [Git в DSH-сессиях сломан двумя переменными](facts/git-env-broken-in-dsh.md) — 4/4 цикла: `GIT_CONFIG_KEY_0` и `dubious ownership`; обёртка `git_ws`, 5–15 шагов на сессию; скил `git-in-sandbox`.

## Решения (ADR)

- [ADR-001: конфигурация только из env](decisions/adr-001-config-env-only.md) — fail-fast, секреты вне repr, `.env` не перетирает env.
- [ADR-002: слои пакета и адаптеры](decisions/adr-002-layered-adapters.md) — handlers → services → adapters/storage, сборка без сети.
- [ADR-003: модель заметки и ручной YAML-рендер](decisions/adr-003-note-model-manual-yaml.md) — frozen dataclasses и чистые функции в `services/note.py`, YAML вручную, PyYAML только в тестах.
- [ADR-004: контракт YouTube-адаптера](decisions/adr-004-youtube-adapter-contract.md) — два источника за протоколами, fallback yt-dlp, типизированные ошибки и их приоритет, метаданные необязательны, адаптер синхронный.
- [ADR-005: контракт LLM-адаптера](decisions/adr-005-llm-adapter-contract.md) — промпт/шаблон как ресурсы пакета, строгая JSON-валидация в `VideoSummary`, повтор только на 429/5xx, `LLMError` и подклассы, синхронный клиент для `to_thread`.

## Скилы

- [intake](../skills/intake/SKILL.md) — как уточнять заявки в этом проекте; «Контекст проекта» обновлён после T-004 (LLM-адаптер, 212 тестов, M → дробить, прогноз ×1,5–2).
- [offline-tests](../skills/offline-tests/SKILL.md) — офлайновые тесты: fake_env, tmp_path для DATA_DIR, фейковые клиенты библиотек, `httpx.MockTransport`, ресурсы пакета и инъекция `sleep`.
- [review-handoff](../skills/review-handoff/SKILL.md) — `git add -N` для новых файлов, сверка `git status -uall` с diffstat, ограничения read-only pytest, граница `specs/` и бюджет ревью.
- [git-in-sandbox](../skills/git-in-sandbox/SKILL.md) — обёртка `git_ws`: обход `missing config key GIT_CONFIG_KEY_0` и `dubious ownership` в DSH-сессиях.
- [shared-helpers](../skills/shared-helpers/SKILL.md) — правило трёх копий для `_one_line`/`format_timecode`, единый источник числовых лимитов промпта и констант, нейтральный модуль без инверсии слоёв.

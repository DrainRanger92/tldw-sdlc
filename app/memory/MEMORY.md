# Память приложения — индекс

Факты и решения, накопленные этапом LEARN. Одна строка на файл.

## Факты

- [Расход цикла 1 и калибровка оценки M](facts/cycle-01-spend.md) — T-001 (M): 81 шаг, 73,5 тыс. output, 3,85 млн токенов; первый замер, гипотеза «M ведёт себя как L».
- [Расход цикла 2: specs, T-002 и ревью](facts/cycle-02-spend.md) — подготовка specs + T-002: 83 шага, 3,20 млн токенов до LEARN; T-002 вдвое дешевле T-001, ревью — 40 % расхода задачи.
- [Расход цикла 3: T-003 и ревью](facts/cycle-03-spend.md) — T-003 (M): 89 шагов, 94,1 тыс. output, 5,86 млн токенов; самый дорогой цикл, 46 % вызовов — до первой строки кода.
- [Калибровка размера M: три промаха подряд](facts/estimate-calibration-m.md) — M = де-факто L: 81/53/89 шагов и 73,5/56,8/94,1 тыс. output; дробить при новой внешней интеграции, >400 строк, >5 критериев или fallback.
- [Базовая линия TLDW после T-001](facts/tldw-baseline-after-t001.md) — исторический срез: каркас, env-конфиг, 58 тестов; актуальнее — после T-003.
- [Базовая линия TLDW после T-002](facts/tldw-baseline-after-t002.md) — модель `Note`, Obsidian-рендер, имя файла, 116 тестов; исторический срез.
- [Базовая линия TLDW после T-003](facts/tldw-baseline-after-t003.md) — YouTube-адаптер: `parse_video_id`, `YouTubeAdapter.fetch`, transcript-api + fallback yt-dlp, типизированные ошибки, 163 теста.
- [spec.md и plan.md 001-mvp — заготовки](facts/specs-001-artifacts-stubs.md) — исторический факт цикла 1; закрыто в цикле 2.
- [specs/001-mvp: spec.md и plan.md заполнены](facts/specs-001-filled-in-cycle-02.md) — FR-1…FR-11, план этапов 1–7, источник истины; `plan.md` хардкодит текущий инкремент.
- [`plan.md` фиксирует устаревший «текущий инкремент»](facts/plan-md-increment-drift.md) — повтор 2/2: после T-003 план всё ещё называет текущим T-002; убрать номер задачи из фразы.
- [Мелкие замечания ревью T-001](facts/t001-review-minor-issues.md) — inf/nan в timeout, дубли тестовых помощников, имя теста, порядок в build_application.
- [Замечания ревью T-002, оставшиеся в коде](facts/t002-review-issues.md) — строка вместо кортежа, bool-таймкод, noncharacters в YAML, slug в байтах, дублирование; кандидаты в T-005.
- [Замечания ревью T-003, оставшиеся в коде](facts/t003-review-issues.md) — P1: нет таймаута transcript-api, двойной `_extract_info`, приоритет ошибок, кэш yt-dlp; закрыть до T-006.
- [Долг ревью копится три цикла](facts/review-debt-accumulates.md) — 4/7/10 замечаний, все APPROVE, отдельной задачи на долг нет; поведенческие P1 уйдут в T-005/T-006.
- [Ревью не видит untracked-файлы](facts/review-diff-untracked.md) — 2/2 цикла до `git add -N`, в цикле 3 паттерн закрыт: ревьюер увидел все 6 путей; скил `review-handoff`.
- [Ревьюер в read-only песочнице не может прогнать pytest](facts/reviewer-readonly-tests.md) — 3/3 цикла: в цикле 3 116 passed + 47 env-errors `tmp_path`; APPROVE не равен зелёным тестам.
- [Интеграция со сторонними библиотеками: сначала проба API](facts/third-party-api-discovery.md) — 38/82 вызова до кода, 12 на исходники youtube-библиотек; версии, сигнатуры и правило пробы на 2–3 шага.
- [Git в DSH-сессиях сломан двумя переменными](facts/git-env-broken-in-dsh.md) — `GIT_CONFIG_KEY_0` и `dubious ownership` в каждой сессии; обёртка `git_ws`, 5–15 шагов на сессию; скил `git-in-sandbox`.

## Решения (ADR)

- [ADR-001: конфигурация только из env](decisions/adr-001-config-env-only.md) — fail-fast, секреты вне repr, `.env` не перетирает env.
- [ADR-002: слои пакета и адаптеры](decisions/adr-002-layered-adapters.md) — handlers → services → adapters/storage, сборка без сети.
- [ADR-003: модель заметки и ручной YAML-рендер](decisions/adr-003-note-model-manual-yaml.md) — frozen dataclasses и чистые функции в `services/note.py`, YAML вручную, PyYAML только в тестах.
- [ADR-004: контракт YouTube-адаптера](decisions/adr-004-youtube-adapter-contract.md) — два источника за протоколами, fallback yt-dlp, типизированные ошибки и их приоритет, метаданные необязательны, адаптер синхронный.

## Скилы

- [intake](../skills/intake/SKILL.md) — как уточнять заявки в этом проекте; «Контекст проекта» обновлён после T-003 (YouTube-адаптер, 163 теста, M → дробить).
- [offline-tests](../skills/offline-tests/SKILL.md) — офлайновые тесты: fake_env, tmp_path для DATA_DIR, фейковые клиенты библиотек, `httpx.MockTransport`.
- [review-handoff](../skills/review-handoff/SKILL.md) — `git add -N` для новых файлов, сверка `git status -uall` с diffstat, ограничения read-only pytest.
- [git-in-sandbox](../skills/git-in-sandbox/SKILL.md) — обёртка `git_ws`: обход `missing config key GIT_CONFIG_KEY_0` и `dubious ownership` в DSH-сессиях.

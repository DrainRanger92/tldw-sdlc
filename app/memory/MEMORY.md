# Память приложения — индекс

Факты и решения, накопленные этапом LEARN. Одна строка на файл.

## Факты

- [Расход цикла 1 и калибровка оценки M](facts/cycle-01-spend.md) — T-001 (M): 81 шаг, 73,5 тыс. output, 3,85 млн токенов; гипотеза «M ведёт себя как L» подтверждена в цикле 2.
- [Расход цикла 2: specs, T-002 и ревью](facts/cycle-02-spend.md) — подготовка specs + T-002: 83 шага, 3,20 млн токенов до LEARN; T-002 вдвое дешевле T-001, ревью — 40 % расхода задачи.
- [Калибровка размера M: два промаха подряд](facts/estimate-calibration-m.md) — M = де-факто L: 81 и 53 шага, 73,5 и 56,8 тыс. output; дробить при >5 критериев, новом слое или >400 строк.
- [Базовая линия TLDW после T-001](facts/tldw-baseline-after-t001.md) — исторический срез: каркас, env-конфиг, 58 тестов; актуальнее — после T-002.
- [Базовая линия TLDW после T-002](facts/tldw-baseline-after-t002.md) — модель `Note`, Obsidian-рендер, имя файла, 116 тестов; адаптеров и Telegram ещё нет.
- [spec.md и plan.md 001-mvp — заготовки](facts/specs-001-artifacts-stubs.md) — исторический факт цикла 1; закрыто в цикле 2, см. следующий пункт.
- [specs/001-mvp: spec.md и plan.md заполнены](facts/specs-001-filled-in-cycle-02.md) — FR-1…FR-11, план этапов 1–7, источник истины; `plan.md` пока хардкодит текущий инкремент.
- [Ревью не видит untracked-файлы](facts/review-diff-untracked.md) — повторилось в цикле 2: `note.py`/`test_note.py` не попали в diffstat; скил `review-handoff`.
- [Ревьюер в read-only песочнице не может прогнать pytest](facts/reviewer-readonly-tests.md) — повторилось в цикле 2: 57 passed + 1 env-error `tmp_path`; APPROVE не равен зелёным тестам.
- [Мелкие замечания ревью T-001](facts/t001-review-minor-issues.md) — inf/nan в timeout, дубли тестовых помощников, порядок в build_application.
- [Замечания ревью T-002, оставшиеся в коде](facts/t002-review-issues.md) — строка вместо кортежа, bool-таймкод, noncharacters в YAML, slug в байтах, дублирование; кандидаты в T-005.

## Решения (ADR)

- [ADR-001: конфигурация только из env](decisions/adr-001-config-env-only.md) — fail-fast, секреты вне repr, `.env` не перетирает env.
- [ADR-002: слои пакета и адаптеры](decisions/adr-002-layered-adapters.md) — handlers → services → adapters/storage, сборка без сети.
- [ADR-003: модель заметки и ручной YAML-рендер](decisions/adr-003-note-model-manual-yaml.md) — frozen dataclasses и чистые функции в `services/note.py`, YAML вручную, PyYAML только в тестах.

## Скилы

- [intake](../skills/intake/SKILL.md) — как уточнять заявки в этом проекте; «Контекст проекта» обновлён после T-002.
- [offline-tests](../skills/offline-tests/SKILL.md) — офлайновые тесты: fake_env, tmp_path для DATA_DIR, фейковые адаптеры и `httpx.MockTransport`.
- [review-handoff](../skills/review-handoff/SKILL.md) — `git add -N` для новых файлов, сверка `git status -uall` с diffstat, ограничения read-only pytest у ревьюера.

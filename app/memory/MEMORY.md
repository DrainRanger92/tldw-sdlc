# Память приложения — индекс

Факты и решения, накопленные этапом LEARN. Одна строка на файл.

## Факты

- [Расход цикла 1 и калибровка оценки M](facts/cycle-01-spend.md) — T-001 (M): 81 шаг, 73,5 тыс. output, 3,85 млн токенов; M недооценена, проверить на T-002.
- [Базовая линия TLDW после T-001](facts/tldw-baseline-after-t001.md) — что уже работает: env-конфиг, сборка без сети, слои, 58 тестов; чего ещё нет.
- [spec.md и plan.md 001-mvp — заготовки](facts/specs-001-artifacts-stubs.md) — задачи живут в tasks.md, планировщик для существующей спеки не запускался.
- [Ревью не видит untracked-файлы](facts/review-diff-untracked.md) — git diff показал только tasks.md; новый код был untracked, нужен git add -N.
- [Ревьюер в read-only песочнице не может прогнать pytest](facts/reviewer-readonly-tests.md) — 12 passed / 46 env-ошибок `tmp_path`; APPROVE не равен зелёным тестам.
- [Мелкие замечания ревью T-001](facts/t001-review-minor-issues.md) — inf/nan в timeout, дубли тестовых помощников, порядок в build_application.

## Решения (ADR)

- [ADR-001: конфигурация только из env](decisions/adr-001-config-env-only.md) — fail-fast, секреты вне repr, `.env` не перетирает env.
- [ADR-002: слои пакета и адаптеры](decisions/adr-002-layered-adapters.md) — handlers → services → adapters/storage, сборка без сети.

## Скилы

- [offline-tests](../skills/offline-tests/SKILL.md) — офлайновые тесты: fake_env, tmp_path для DATA_DIR, фейковые адаптеры и `httpx.MockTransport`.

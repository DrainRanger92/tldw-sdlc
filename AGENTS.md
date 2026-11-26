# Правила для всех агентов aidevteam

- Проект: **TLDW** (Too Long; Didn't Watch) — Telegram-бот, который превращает
  образовательное видео с YouTube в структурированную заметку для Obsidian.
  Vision — `specs/product.md`.
- AI-native SDLC. Оркестратор — `orchestrator/` (Python-пакет aidevteam),
  бэкенд агентов — DSH (headless-профиль), роли — оверлеи `dsh/roles/`.
  Приложение — `app/` (Python 3.11+), артефакты — `specs/`
  (Spec Kit: spec → plan → tasks).
- ЗАЩИЩЕНО (менять только в задачах типа `sdlc:`): `orchestrator/`, `dsh/`,
  `docker/`, `aidevteam.toml`, файл `aidevteam` в корне.
- Бэкенд переключается в `aidevteam.toml` (`[agents] backend = "dsh" | "opencode"`).
  Не меняйте его в рамках задач типа `app`.
- Статусы задач в `specs/*/tasks.md` меняет только оркестратор. Не переводите
  задачи в done самостоятельно.
- Формат задач: `### T-NNN: название`, поля `- тип: app|sdlc`, `- статус: pending|done`,
  `- зависит: …`, `- размер: S|M|L`, секции `**Описание**` и
  `**Критерии приёмки**` (чекбоксы).
- Черновики: `specs/inbox/D-NNN-*.md` (frontmatter id/title/status,
  `## Описание`, `## Открытые вопросы` с `- [ ] …`).
- Память приложения: перед работой над `app/` читайте `app/AGENTS.md`,
  `app/memory/MEMORY.md` и применимые скилы `app/skills/*/SKILL.md`.
- Секреты (токен Telegram-бота, ключи API) — только через env и `.env`; в код,
  фикстуры, спеки и коммиты они не попадают.
- Коммиты не делаете — коммитит оркестратор.

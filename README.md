# tldw-sdlc — TLDW на AI-native SDLC

Репозиторий проекта **TLDW** (Telegram-бот: видео с YouTube → заметка для
Obsidian), который разрабатывается сам: внутри — оркестратор **aidevteam**
(Python) и мультиагентная команда на базе **DeepSeek Harness (DSH)**
(аналитик, планировщик, разработчик, ревьюер). Один запуск `./aidevteam run` =
один цикл разработки: оркестратор находит работу (задачи спек, черновики,
новые коммиты, отчёты о сбоях) и доводит её до автономного коммита в main —
через ревью, тесты и smoke-гейты.

Сейчас в репозитории нет кода приложения: есть vision (`specs/product.md`),
каркас спеки MVP (`specs/001-mvp/`) и AI-native структура — скилы
(`app/skills/`, стандарт SKILL.md) и память (`app/memory/`, Memory Bank).
Приложение рождается и «умнеет» цикл за циклом.

Роли — оверлеи headless-профиля DSH в `dsh/roles/`; бэкенд переключается в
`aidevteam.toml` (`[agents] backend = "dsh" | "opencode"`).

## Быстрый старт

1. Установите DSH CLI так, чтобы `dsh --version` работал. Wrapper сам находит
   CLI Desktop-приложения по стандартному пути; для другого пути задайте
   `AIDEVTEAM_DSH_BIN`:

```bash
export AIDEVTEAM_DSH_BIN="/path/to/dsh"
```
2. Положите ключ в `.env`:

```bash
cp .env.example .env
# DEEPSEEK_API_KEY=...
```

3. Запустите из корня проекта:

```bash
./aidevteam doctor   # проверка dsh, версии, ролей, ключа, артефактов
./aidevteam status   # что в проекте
./aidevteam next     # что сделает первый цикл (без LLM)
./aidevteam add      # первая работа: аналитик оформит заявку в задачи
./aidevteam run      # один цикл SDLC
```

Каркас `specs/001-mvp/` намеренно без задач: первый цикл получит работу из
черновика (`aidevteam add` / `add task "…"`) или из правок `specs/product.md`.

Всё работает в текущем шелле: нужны python3 (≥3.9), git и DSH на хосте —
окружение wrapper создаёт сам при первом запуске (`.aidevteam/venv`).
Приложению нужен свой интерпретатор: стек и команды описаны в `app/AGENTS.md`.

Запуск в контейнере: `AIDEVTEAM_DOCKER=1 ./aidevteam …` — для DSH собирается
образ из `docker/Dockerfile.dsh` (Python 3.12 + Node 24 + `@deepseek-ai/dsh`).
Первый запуск собирает образ сам (несколько минут, ~2,8 ГБ), профиль и сессии
DSH живут в `.aidevteam/dsh-home` на хосте. Код оркестратора читается из
смонтированного workspace (`PYTHONPATH=/workspace/orchestrator/src`), поэтому
правки в `orchestrator/` не требуют пересборки; пересобирать нужно только при
смене зависимостей или версии DSH:

```bash
docker image rm aidevteam-dsh:0.1.0   # следующий AIDEVTEAM_DOCKER=1 соберёт заново
```

CLI Docker Desktop лежит в `~/.docker/bin` — wrapper добавляет его в `PATH`, если
`docker` не найден. Ревьюер в контейнере работает в read-only песочнице; если
ядро/контейнер не поддерживает sandbox-local, поставьте
`agents.dsh.reviewer_permission_mode = "danger-full-access"` — неизменность
worktree всё равно проверит цикл.

### Переключение на opencode

```toml
[agents]
backend = "opencode"
```

Роли для этого пути лежат в `.opencode/agent/` (в этом репозитории их нет —
добавьте при необходимости), вход — `opencode auth login`. Основной бэкенд — DSH.

## Команды

| Команда | Что делает |
|---|---|
| `run` | выполнить один цикл SDLC (`--dry-run`, `--verbose`) |
| `next` | предпросмотр следующего цикла — детерминированно, без LLM |
| `status` | диагностика: окружение, спеки, бэклог, память |
| `doctor` | проверка компонентов: python, git, бэкенд агентов, роли, артефакты |
| `list tasks\|specs\|skills\|memory` | просмотр артефактов |
| `show task <ID>` | карточка задачи с критериями приёмки |
| `add` / `add task "…"` | интерактивная / быстрая постановка заявки |
| `refine <D-ID>` | диалог с аналитиком по черновику |
| `plan [D-ID]` | оформить готовые черновики в задачи без полного цикла |
| `move <T-ID> current\|backlog` | сдвинуть «ватерлинию» инкремента |
| `watch` | наблюдение: цикл при появлении работы |
| `log [N]` | журнал циклов |
| `app dev\|test\|stop` | запуск/тесты/остановка приложения |

Коды выхода: `0` — успех (в т.ч. «работы нет»), `1` — preflight/гейт,
`2` — авария цикла (отчёт в `.aidevteam/last-failure.md`).

## Гейты приложения

`VERIFY` запускает детерминированные проверки по стеку `app/`:

- **npm-приложение** (`app/package.json`): `npm test`, `npm run lint` и smoke —
  `npm run dev` + `GET /health` → 200.
- **Python-приложение** (`app/pyproject.toml`, `app/requirements.txt` или `*.py`):
  `pytest` по найденным `test_*.py` (интерпретатор — `app/.venv`, если он есть)
  и `ruff check .`, если ruff настроен в `app/pyproject.toml`; smoke —
  `compileall` без сети и токенов.
- приложения ещё нет — гейты пропускаются.

## Модели ролей

Модель задаётся строкой `provider/model[@effort]` в `aidevteam.toml`; отсутствующая
роль берёт `default`. Effort — это `reasoningEffort` DSH: без `@` работает дефолт
провайдера, с `@high`/`@max` роль платит больше reasoning-токенов, но думает
глубже. Уровни проверяет сам DSH по своему каталогу: для `deepseek-flash` это
`off|low|high|max`, неподдерживаемый уровень отклоняется на старте
(`UNSUPPORTED_REASONING_EFFORT`).

```toml
[agents.models]
default  = "deepseek-official/deepseek-flash"
analyst  = "deepseek-official/deepseek-flash@high"
reviewer = "deepseek-official/deepseek-flash@high"
```

Оркестратор передаёт модель отдельным `--patch`; `aidevteam doctor` печатает
карту ролей с effort. При пустой секции работает дефолт профиля DSH.

## Ограничения безопасности

Ключ лежит в `.env` внутри рабочей копии, а роли имеют `read`, `bash` и `web`
при `danger-full-access`. Агент (в том числе под влиянием содержимого черновика)
может прочитать ключ и отправить его наружу. Для локальной работы это принятый
риск; для CI или чужих заявок держите ключ вне workspace (например,
`agents.dsh.env_file = "~/.dsh/.env"`), ограничьте `tool-web` или используйте
credential service DSH. Ревьюер работает в `read-only` песочнице, а цикл
проверяет, что ревью не изменило worktree, — это защищает код, но не скрывает
ключ от остальных ролей.

В публичном репозитории секретов нет: `.env` и `app/.env` в `.gitignore`,
токены бота и ключи API задаются только локально.

## Как поставить работу

- **Диалогом**: `./aidevteam add` — аналитик задаст 2–3 вопроса и оформит заявку.
- **Однострочно**: `./aidevteam add task "…" --desc "…"` → черновик в
  `specs/inbox/`; уточнить потом: `./aidevteam refine D-001`.
- **Через git**: правьте `specs/product.md` или спеки и коммитьте — следующий
  цикл заметит.

## Правила автономии

Агент коммитит в main сам, но только после: approve независимого ревьюера,
зелёных тестов/линта и smoke-проверки. Пути SDLC (`orchestrator/`, `dsh/`,
`docker/`, `aidevteam.toml`, wrapper) защищены от изменений вне задач типа
`sdlc:`. Автономные коммиты подписаны `aidevteam-agent` — в истории всегда видно,
что сделал агент, а что человек.

## Структура

```
aidevteam              wrapper: окружение + запуск CLI
aidevteam.toml         конфигурация цикла, бэкенда и моделей
orchestrator/          Python-пакет aidevteam (+ pytest)
dsh/roles/             оверлеи ролей headless-профиля DSH
docker/                образы для запуска в контейнере
specs/                 product.md, каркас 001-mvp, inbox черновиков
app/                   приложение: AGENTS.md, skills/, memory/ (кода пока нет)
```

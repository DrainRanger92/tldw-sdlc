# DSH-бэкенд aidevteam

Здесь лежат оверлеи ролей для headless-профиля DSH. Оркестратор вызывает:

```sh
dsh --profile headless \
    --patch dsh/roles/<role>.yml \
    --patch .aidevteam/dsh/model-<role>.yml \
    --permission-mode danger-full-access \
    --approval-policy never \
    --json -
```

## Контракт оверлея

| Строка | Что задаёт | Почему так |
|---|---|---|
| `system-prompt` | `personaPrefix` — текст роли | `@deepseek-ai/dsh-persona` нельзя монтировать глобально: он конфликтует с персоной промпт-реестра и падает |
| `tool-fs` | `read`, `write`, `edit`, `applyPatch` | analyst/planner пишут артефакты specs/ и память, developer — код, reviewer — только читает; `config` строки заменяется целиком, поэтому `applyPatch` повторяется |
| `tool-subagent` | `disabled: true` | роли не делегируют дальше |
| `tool-subagent-fork` | `disabled: true` | то же для fork-варианта |
| `tool-workflow` | `disabled: true` | роли не запускают workflow |

Ревьюер запускается с `--permission-mode read-only` (`agents.dsh.reviewer_permission_mode`):
`git diff` читает, но `sed -i`, `git checkout` и другие записи блокирует песочница.
Дополнительно `cycle.py` сравнивает worktree до и после ревью и падает, если
файлы или diff изменились. Если в контейнере/ядре недоступен sandbox-local,
DSH с `read-only` падает закрыто: для такого окружения можно явно поставить
`agents.dsh.reviewer_permission_mode = "danger-full-access"` — проверка worktree
всё равно останется.

Идентификаторы строк — часть API патчей DSH. Они собраны только в этих YAML,
в Python их нет. При обновлении DSH `aidevteam doctor` поднимает композицию через
`--dump-config` и проверяет, что строки на месте, персона непустая, а права
соответствуют роли. Если DSH переименовал строку, проверка падает с именем
строки, а не пропускает ошибку молча.

## Модель

Модель роли задаётся в `aidevteam.toml`:

```toml
[agents.models]
default  = "deepseek-official/deepseek-flash"
reviewer = "deepseek-official/deepseek-reasoner"
```

Оркестратор пишет `.aidevteam/dsh/model-<role>.yml` и передаёт его вторым
`--patch`. Без записи в `[agents.models]` используется дефолт профиля DSH.
Формат значения — `provider/model`.

## Проверка вручную

```sh
dsh --profile headless --patch dsh/roles/analyst.yml --dump-config
```

Ожидаемо: `system-prompt.personaPrefix` содержит текст аналитика, у `tool-fs`
`write: true` и `edit: true`, `tool-subagent` — `disabled: true`.

## Обновление DSH

Версия пиннится в `aidevteam.toml` (`[agents.dsh] version`) и в Docker-образе.
После обновления:

1. `./aidevteam doctor` — проверит версию и состав каждой роли.
2. Если строки переименованы — поправить только YAML в этом каталоге.
3. Прогнать `pytest` — тест состава использует реальный `dsh --dump-config`,
   если бинарь доступен, и пропускается, если нет.

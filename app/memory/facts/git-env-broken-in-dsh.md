# Git в DSH-сессиях сломан двумя переменными окружения

Дата: 2026-11-26. Наблюдение по циклам 1–3: практически каждая сессия
(developer, reviewer, analyst) тратит первые шаги на две одинаковые ошибки git.
Это не дефект проекта, а окружение харнесса, но оно систематически съедает
шаги и внимание агентов.

## Симптом 1: битый `GIT_CONFIG_*`

```
error: missing config key GIT_CONFIG_KEY_0
fatal: unable to parse command-line config
```

В окружении стоит `GIT_CONFIG_COUNT=1` и `GIT_CONFIG_VALUE_0=/workspace`, но
`GIT_CONFIG_KEY_0` отсутствует. Git пытается прочитать конфиг из переменных и
падает ещё до команды. Лечение — убрать эти переменные для вызова:

```bash
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_KEY_0 git status
```

## Симптом 2: `dubious ownership`

```
fatal: detected dubious ownership in repository at '/workspace'
```

Репозиторий принадлежит другому uid, чем процесс агента. Лечение — one-shot
исключение, без правки глобального конфига:

```bash
git -c safe.directory=/workspace status
```

## Рабочая обёртка

Обе проблемы встречаются вместе, поэтому удобнее функция на сессию:

```bash
git_ws() {
  env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_KEY_0 \
    git -c safe.directory=/workspace "$@"
}

git_ws status --porcelain -uall
git_ws --no-pager diff --stat e82aef6
git_ws --no-pager log --oneline -5
```

- Не пытайтесь «починить» окружение, задав `GIT_CONFIG_KEY_0`: переменные
  приходят из харнесса, их набор может меняться. Надёжнее отключить их на
  время вызова.
- Не добавляйте `safe.directory` в глобальный конфиг и не меняйте `.git/config`:
  это общая копия репозитория, а не ваша.
- После обёртки проверяйте `exit code`; пустой вывод `git status` — это норма,
  а не ошибка.

## Цена

- Цикл 3, developer: ~10 вызовов на диагностику и обход git до и во время
  работы; reviewer: первые ~15 из 40 вызовов инструментов.
- Циклы 1–2: та же картина в каждой сессии; в цикле 2 разработчик и ревьюер
  независимо нашли `env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0`.
- Итого 3 из 3 циклов; у разработчика это 5–10 шагов (6–16 % задачи), у
  ревьюера в цикле 3 — ~15 из 26 шагов. Инфраструктура, не связанная с
  продуктом, но она систематически съедает начало каждой сессии.

Паттерн вынесен в скил `app/skills/git-in-sandbox/SKILL.md`. Поскольку
`orchestrator/`, `dsh/` и `docker/` защищены, в LEARN меняются только память
и скил; чинить сам харнесс — отдельная задача типа `sdlc:`.

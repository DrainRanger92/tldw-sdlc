---
name: git-in-sandbox
description: Как запускать git в DSH-сессиях TLDW, когда окружение ломает команды — обход missing config key GIT_CONFIG_KEY_0 и dubious ownership одной обёрткой; применять перед любым git status/diff/log/add.
---

# Git в песочнице DSH

Паттерн повторялся в циклах 1–7 в каждой сессии — у developer, reviewer и
analyst. Две независимые ошибки окружения валят git ещё до команды; в цикле 3
разработчик потратил на диагностику ~10 вызовов, ревьюер — первые ~15 из 40.
В цикле 4 разработчик сразу применил обёртку и потратил 7 вызовов на git без
диагностики, а ревьюер снова начал с `git status` и потерял 5 из 48 вызовов.
В цикле 5 картина та же: developer — 7 вызовов с обёрткой и без диагностики,
reviewer — 5 вызовов на обход. В цикле 6 — снова 6/6: developer потратил 7
git-вызовов без диагностики, reviewer — 14 вызовов (обёртка + поиск
untracked + `git diff HEAD`), потому что начал с обычного git и `git diff`.
В цикле 7 обёртка применялась с первого вызова: developer — 7 git-вызовов,
reviewer — 10, ни одной ошибки `GIT_CONFIG_*`/`dubious ownership`. Это не
дефект проекта, а харнесс, поэтому лечится обёрткой, а не правкой
репозитория.

## Когда применять

- Любая git-команда в `/workspace` из DSH-сессии.
- Особенно перед `git status`, `git diff`, `git add -N`, `git log` — то есть
  в начале и в конце каждой задачи.

## Симптомы

```text
error: missing config key GIT_CONFIG_KEY_0
fatal: unable to parse command-line config
```

В окружении есть `GIT_CONFIG_COUNT=1` и `GIT_CONFIG_VALUE_0=/workspace`, но нет
`GIT_CONFIG_KEY_0`. Git падает при разборе своего конфига.

```text
fatal: detected dubious ownership in repository at '/workspace'
```

Репозиторий принадлежит другому uid, чем процесс агента.

## Обёртка

Одна функция на сессию снимает обе проблемы:

```bash
git_ws() {
  env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_KEY_0 \
    git -c safe.directory=/workspace "$@"
}

git_ws status --porcelain -uall
git_ws --no-pager diff --stat e82aef6
git_ws --no-pager log --oneline -5
git_ws add -N app/
```

- `env -u` убирает битые `GIT_CONFIG_*` только на время вызова.
- `git -c safe.directory=/workspace` — one-shot исключение для чужого uid.
- Не задавайте `GIT_CONFIG_KEY_0` вручную и не добавляйте `safe.directory` в
  глобальный конфиг: набор переменных приходит из харнесса и может меняться.
- Не правьте `.git/config` и не пытайтесь «починить» репозиторий — он общий.
- Проверяйте `exit code`: пустой `git status` — нормальный результат, а не
  ошибка.

## Если обёртка не сработала

1. `env | grep -i '^GIT'` — посмотрите, какие переменные пришли.
2. `git_ws rev-parse --show-toplevel` — убедитесь, что виден `/workspace`.
3. Только после этого диагностируйте дальше; не тратьте больше 2–3 шагов на
   инфраструктуру, не связанную с задачей.

Ревьюер в цикле 5 обошёл проблему иначе — `GIT_CONFIG_KEY_0=safe.directory
git ...` (задал недостающий ключ вместо снятия переменных). В этой сессии
сработало, но полагаться на это нельзя: набор `GIT_CONFIG_*` приходит из
харнесса и может измениться. Обёртка `git_ws` надёжнее.

Помните и про следствие `git add -N`: изменения попадают в индекс, поэтому
обычный `git diff` пуст. В рабочей копии, подготовленной к ревью, смотрите
`git diff HEAD <base>` или `git diff --cached <base>`.

## См. также

- `app/memory/facts/git-env-broken-in-dsh.md` — замеры и история.
- `app/skills/review-handoff/SKILL.md` — как эта обёртка используется при
  подготовке диффа к ревью.

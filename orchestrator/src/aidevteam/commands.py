"""Реализации подкоманд CLI."""
from __future__ import annotations

from pathlib import Path

from aidevteam import gitops
from aidevteam import tasks as taskmod
from aidevteam.state import State, read_failure, read_journal
from aidevteam.ui import UI


def find_root() -> Path | None:
    cur = Path.cwd()
    for p in (cur, *cur.parents):
        if (p / "specs").is_dir():
            return p
    return None


def dispatch(cmd: str, args: list[str]) -> int:
    root = find_root()
    if root is None:
        print("  ✘ Не найден корень проекта (нет каталога specs/)")
        return 1
    return dispatch_in(root, cmd, args)


def dispatch_in(root: Path, cmd: str, args: list[str]) -> int:
    ui = UI(verbose="--verbose" in args)
    handlers = {
        "status": cmd_status,
        "next": cmd_next,
        "list": cmd_list,
        "show": cmd_show,
        "log": cmd_log,
        "add": cmd_add,
        "doctor": cmd_doctor,
        "move": cmd_move,
        "plan": cmd_plan,
    }
    if cmd == "run":
        from aidevteam.cycle import cmd_run

        return cmd_run(root, args, ui)
    if cmd == "watch":
        from aidevteam.watch import cmd_watch

        return cmd_watch(root, args, ui)
    if cmd == "refine":
        from aidevteam.intake import cmd_refine

        return cmd_refine(root, args, ui)
    if cmd == "app":
        from aidevteam.appcmd import cmd_app

        return cmd_app(root, args, ui)
    if cmd == "init":
        from aidevteam.cycle import cmd_init

        return cmd_init(root, args, ui)
    handler = handlers.get(cmd)
    if handler is None:
        print(f"  ✘ Команда '{cmd}' ещё не реализована")
        return 1
    return handler(root, args, ui)


def _tool_version(cmd: list[str]) -> str | None:
    import subprocess

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return res.stdout.strip().splitlines()[0] if res.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None


def _has_dsh_key(root: Path, env_file: str) -> bool:
    import os

    from aidevteam import team

    if os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return True
    return bool(team.read_env_file(root / env_file).get("DEEPSEEK_API_KEY", "").strip())


def cmd_doctor(root: Path, args: list[str], ui: UI) -> int:
    """Проверка компонентов. Обязательные валят exit 1, опциональные — warn."""
    import os
    import sys

    from aidevteam import team
    from aidevteam.config import Config

    ok = True

    def req(cond: bool, good: str, bad: str) -> None:
        nonlocal ok
        ui.plain(f"  {'✔' if cond else '✘'} {good if cond else bad}")
        ok = ok and cond

    def opt(cond: bool, good: str, bad: str) -> None:
        ui.plain(f"  {'✔' if cond else '⚠'} {good if cond else bad}")

    try:
        cfg = Config.load(root)
        cfg.validate()
        cfg_error = ""
    except Exception as exc:  # битый toml/неизвестный backend — это провал doctor
        cfg = Config()
        cfg_error = str(exc)
    backend = team.agent_backend(cfg)
    req(not cfg_error, "aidevteam.toml читается", f"aidevteam.toml: {cfg_error}")

    ui.plain("Обязательные компоненты")
    req(sys.version_info >= (3, 9), f"python {sys.version.split()[0]}",
        f"python ≥3.9 требуется, найден {sys.version.split()[0]}")
    git_v = _tool_version(["git", "--version"])
    req(git_v is not None, str(git_v), "git не найден")
    # node/npm нужны только npm-приложению и DSH; Python-приложению — нет.
    node_v = _tool_version(["node", "--version"])
    opt(node_v is not None, f"node {node_v}",
        "node не найден — нужен только npm-приложению и DSH")
    npm_v = _tool_version(["npm", "--version"])
    opt(npm_v is not None, f"npm {npm_v}",
        "npm не найден — нужен только npm-приложению")
    if backend == "dsh":
        dsh_v = team.dsh_version(cfg, root)
        req(dsh_v is not None, f"dsh {dsh_v}",
            "dsh не найден — задайте AIDEVTEAM_DSH_BIN или agents.dsh.bin")
        if dsh_v and cfg.dsh_version:
            req(dsh_v == cfg.dsh_version, f"версия dsh {dsh_v}",
                f"dsh {dsh_v}, ожидается {cfg.dsh_version} (agents.dsh.version)")
    else:
        oc_v = _tool_version([team.opencode_bin(), "--version"])
        req(oc_v is not None, f"opencode {oc_v}",
            "opencode не найден — установка: https://opencode.ai, затем opencode auth login")

    ui.plain("\nПроект")
    req((root / "specs").is_dir(), "specs/ на месте", "нет каталога specs/")
    if backend == "dsh":
        for role in ("analyst", "planner", "developer", "reviewer"):
            f = root / cfg.dsh_roles_dir / f"{role}.yml"
            req(f.exists(), f"роль {role}", f"нет оверлея {f}")
        if dsh_v:
            for role in ("analyst", "planner", "developer", "reviewer"):
                problems = team.dsh_composition_problems(root, cfg, role)
                req(not problems, f"состав роли {role}",
                    "; ".join(problems[:2]))
    else:
        for role in ("analyst", "planner", "developer", "reviewer"):
            f = root / ".opencode" / "agent" / f"{role}.md"
            req(f.exists(), f"роль {role}", f"нет роли .opencode/agent/{role}.md")
    problems = taskmod.lint(root)
    req(not problems, "линт артефактов specs/ пройден",
        "линт артефактов: " + "; ".join(problems[:3]))

    if backend == "dsh" and cfg.agent_models:
        ui.plain("")
        ui.plain("  Модели ролей")
        for role in ("analyst", "planner", "developer", "reviewer"):
            spec = cfg.agent_models.get(role) or cfg.agent_models.get("default")
            if spec:
                ui.plain(f"    {role:<10} {spec}")

    ui.plain("\nОпциональные")
    if backend == "dsh":
        opt(_has_dsh_key(root, cfg.dsh_env_file),
            "ключ DEEPSEEK_API_KEY найден",
            f"ключ не найден — добавьте DEEPSEEK_API_KEY в {cfg.dsh_env_file}")
        home = Path(cfg.dsh_home).expanduser() if cfg.dsh_home else Path(
            os.environ.get("DSH_HOME", "~/.dsh")
        ).expanduser()
        anchor = home if home.exists() else home.parent
        opt(anchor.exists() and os.access(anchor, os.W_OK),
            f"DSH_HOME {home} доступен для записи",
            f"DSH_HOME {home} недоступен для записи — задайте agents.dsh.home")
        opt(cfg.dsh_reviewer_permission_mode == "read-only",
            "ревьюер: read-only песочница",
            f"ревьюер: permission_mode={cfg.dsh_reviewer_permission_mode!r} — запись возможна")
    docker_ok = _tool_version(["docker", "info", "--format", "ok"]) is not None
    if os.environ.get("AIDEVTEAM_IN_DOCKER"):
        opt(True, "запуск в контейнере (docker CLI внутри не нужен)", "")
    else:
        opt(docker_ok, "docker доступен (опционально: AIDEVTEAM_DOCKER=1 — запуск в изоляции)",
            "docker недоступен — штатный режим, запуск идёт в текущем шелле")

    ui.plain("")
    if ok:
        ui.plain("Все обязательные компоненты на месте. Аутентификацию LLM проверит"
                 " PREFLIGHT при первом run (пробный вызов).")
        return 0
    ui.plain("✘ Есть проблемы — исправьте отмеченные пункты и повторите aidevteam doctor")
    return 1


def cmd_status(root: Path, args: list[str], ui: UI) -> int:
    st = State.load(root)
    all_tasks = taskmod.load_tasks(root)
    drafts = taskmod.load_drafts(root)
    pending = [t for t in all_tasks if t.status == "pending"]
    ui.plain(f"Репозиторий        {root} (HEAD {gitops.head_sha(root)[:7]})")
    ui.plain(f"Циклов выполнено   {st.cycles}")
    ui.plain("")
    for spec in sorted({t.spec for t in all_tasks}):
        sp = [t for t in all_tasks if t.spec == spec]
        sp_done = sum(1 for t in sp if t.status == "done")
        ui.plain(f"  спека {spec}: {len(sp) - sp_done} pending / {sp_done} done")
    if drafts:
        ui.plain(f"  inbox: {len(drafts)} черновик(ов): " + ", ".join(d.id for d in drafts))
    if read_failure(root):
        ui.plain("  ⚠ есть отчёт о сбое (.aidevteam/last-failure.md)")
    nxt = "aidevteam run" if pending or drafts else "добавьте работу: aidevteam add"
    ui.plain(f"\nСледующий шаг: {nxt}")
    return 0


def cmd_next(root: Path, args: list[str], ui: UI) -> int:
    st = State.load(root)
    all_tasks = taskmod.load_tasks(root)
    inc = taskmod.pending_current(all_tasks)
    queued = [t for t in all_tasks if t.status == "pending" and t.bucket == "backlog"]
    drafts = taskmod.load_drafts(root)
    new = gitops.commits_since(root, st.last_processed_commit)
    ui.plain("Предпросмотр следующего цикла (без запуска, без LLM)\n")
    ui.plain(f"  Необработанные коммиты: {len(new) or 'нет'}")
    fail = read_failure(root)
    ui.plain(f"  Отчёт о сбое: {'есть — будет разобран' if fail else 'нет'}")
    if inc:
        ui.plain("\n  Текущий инкремент:")
        for t in inc:
            ui.plain(f"    → {t.id}  {t.type:<4} {t.title}")
    elif queued:
        ui.plain("\n  Текущий инкремент пуст — цикл возьмёт из бэклога:")
        ui.plain(f"    → {queued[0].id}  {queued[0].type:<4} {queued[0].title}")
    ready = [d for d in drafts if taskmod.draft_ready(d)]
    blocked = [d for d in drafts if d not in ready]
    for d in ready:
        ui.plain(f"    → {d.id}  разбор черновика «{d.title}»")
    for d in blocked:
        ui.plain(f"    ⚠ {d.id} «{d.title}» ждёт ответов: aidevteam refine {d.id}")
    if not inc and not ready and not fail and not new:
        ui.plain("\n  Работы нет — цикл завершится сразу.")
    return 0


def cmd_list(root: Path, args: list[str], ui: UI) -> int:
    what = args[0] if args else "tasks"
    if what == "tasks":
        show_all = "--all" in args
        all_tasks = taskmod.load_tasks(root)
        visible = all_tasks if show_all else [t for t in all_tasks if t.status == "pending"]
        headers = {
            "current": "Сейчас в работе (текущий инкремент)",
            "backlog": "Бэклог",
        }
        for bucket, header in headers.items():
            rows = [t for t in visible if t.bucket == bucket]
            if not rows:
                continue
            ui.plain(f"\n  {header}")
            for t in rows:
                status = f" {t.status:<8}" if show_all else ""
                ui.plain(f"    {t.id}  {t.type:<5}{status} {t.title}  [{t.spec}]")
        for d in taskmod.load_drafts(root):
            q = f" ({len(d.open_questions)} откр. вопрос.)" if d.open_questions else ""
            ui.plain(f"\n  {d.id}  draft  «{d.title}»{q}")
        done = sum(1 for t in all_tasks if t.status == "done")
        if done and not show_all:
            ui.plain(f"\n  выполнено: {done} — показать всё: aidevteam list tasks --all")
        return 0
    if what == "skills":
        skills = sorted((root / "app" / "skills").glob("*/SKILL.md"))
        ui.plain(f"  Скилов: {len(skills)}")
        for s in skills:
            ui.plain(f"    {s.parent.name}")
        return 0
    if what == "memory":
        facts = sorted((root / "app" / "memory" / "facts").glob("*.md"))
        adrs = sorted((root / "app" / "memory" / "decisions").glob("*.md"))
        ui.plain(f"  Фактов: {len(facts)}, ADR: {len(adrs)}")
        return 0
    if what == "specs":
        for f in sorted((root / "specs").glob("*/spec.md")):
            ui.plain(f"    {f.parent.name}")
        return 0
    print(f"  ✘ Неизвестная сущность: {what} (tasks|specs|skills|memory)")
    return 1


def cmd_show(root: Path, args: list[str], ui: UI) -> int:
    if len(args) >= 2 and args[0] == "task":
        tid = args[1]
        for t in taskmod.load_tasks(root):
            if t.id == tid:
                ui.plain(f"{t.id} — {t.title}\n")
                ui.plain(f"  Тип        {t.type}")
                ui.plain(f"  Статус     {t.status}")
                ui.plain(f"  Спека      {t.spec}")
                from aidevteam.tasks import BUCKET_TITLES

                ui.plain(f"  Секция     {BUCKET_TITLES[t.bucket]}")
                ui.plain(f"  Размер     {t.size}")
                ui.plain(f"  Зависит    {', '.join(t.depends) or '—'}")
                ui.plain(f"\n  Описание\n    {t.description.strip()}")
                ui.plain("\n  Критерии приёмки")
                for a in t.acceptance:
                    ui.plain(f"    [ ] {a}")
                return 0
        print(f"  ✘ Задача {tid} не найдена")
        return 1
    print("  Использование: aidevteam show task <ID>")
    return 1


def cmd_log(root: Path, args: list[str], ui: UI) -> int:
    n = int(args[0]) if args and args[0].isdigit() else 5
    entries = read_journal(root, n)
    if not entries:
        ui.plain("  Журнал пуст — циклов ещё не было.")
        return 0
    for e in entries:
        mark = "✔" if e.get("ok") else "✘"
        ui.plain(f"  {mark} цикл #{e['cycle']}  {e.get('summary', '')}")
    return 0


def cmd_move(root: Path, args: list[str], ui: UI) -> int:
    from aidevteam.tasks import BUCKET_TITLES

    positional = len(args) >= 3 and args[1] in ("before", "after")
    if not positional and (len(args) < 2 or args[1] not in BUCKET_TITLES):
        print("  Использование: aidevteam move <T-ID> current|backlog")
        print("                 aidevteam move <T-ID> before|after <T-ID>")
        return 1
    try:
        if positional:
            moved = [taskmod.reorder_task(root, args[0], args[1], args[2])]
        else:
            moved = taskmod.move_task(root, args[0], args[1])
    except ValueError as exc:
        print(f"  ✘ {exc}")
        return 1
    path = moved[0].path
    problems = taskmod.lint(root)
    if problems:
        gitops.run_git(root, "checkout", "--", str(path.relative_to(root)), check=False)
        print("  ✘ перенос нарушает зависимости — откачено:")
        for p in problems[:3]:
            print(f"    {p}")
        return 1
    ids = ", ".join(t.id for t in moved)
    dest = f"{args[1]} {args[2]}" if positional else f"→ {BUCKET_TITLES[args[1]]}"
    name, email = gitops.user_identity(root)
    gitops.commit_paths(
        root, [str(path.relative_to(root))],
        f"docs(specs): {ids} {dest}", name, email,
    )
    ui.plain(f"  ✔ {ids} {dest}")
    if len(moved) > 1:
        ui.plain("    (каскад: вместе с задачами, связанными порядком выполнения)")
    ui.plain(f"    закоммичено от вашего имени ({name})")
    return 0


def cmd_plan(root: Path, args: list[str], ui: UI) -> int:
    """Оформить готовые черновики в задачи — один вызов аналитика, без цикла."""
    from aidevteam import team
    from aidevteam.config import Config

    cfg = Config.load(root)
    drafts = taskmod.load_drafts(root)
    if args:
        drafts = [d for d in drafts if d.id == args[0]]
        if not drafts:
            print(f"  ✘ Черновик {args[0]} не найден (aidevteam list tasks)")
            return 1
    ready = [d for d in drafts if taskmod.draft_ready(d)]
    for d in drafts:
        if d not in ready:
            if d.open_questions:
                reason = "есть открытые вопросы"
            elif d.description in ("", "(пусто)"):
                reason = "пустое описание"
            else:
                reason = "story без критериев исполнения"
            ui.plain(f"  ⚠ {d.id} «{d.title}» не готов ({reason}) → aidevteam refine {d.id}")
    if not ready:
        if not drafts:
            ui.plain("  Черновиков нет — создать: aidevteam add task \"…\"")
        return 1 if args else 0

    # рабочая копия должна быть чистой, чтобы откат по линту был безопасным
    dirty = [p for p in gitops.dirty_paths(root) if not p.startswith(".aidevteam/")]
    inbox_dirty = [p for p in dirty if p.startswith("specs/inbox/")]
    other = [p for p in dirty if p not in inbox_dirty]
    if other:
        print(f"  ✘ незакоммиченные изменения вне specs/inbox: {other[:3]} — закоммитьте или уберите")
        return 1
    name, email = gitops.user_identity(root)
    if inbox_dirty:
        gitops.commit_paths(root, inbox_dirty, "docs(inbox): заявки пользователя", name, email)
        ui.plain(f"  ✔ inbox закоммичен от вашего имени ({name})")

    before_ids = {(t.spec, t.id) for t in taskmod.load_tasks(root)}
    specs_before = {p.parent.name for p in (root / "specs").glob("*/spec.md")}
    ctx = "\n\n".join(
        f"Черновик {d.id} «{d.title}»:\n{d.path.read_text(encoding='utf-8')}" for d in ready
    )
    with ui.wait("аналитик оформляет черновики в задачи…"):
        res = team.run_agent(
            root, "analyst",
            "Оформи готовые черновики в задачи: добавь их в секцию «Бэклог» tasks.md "
            "подходящей спеки (формат '### T-NNN: …' с типом, статусом pending, "
            "зависимостями, оценкой '- размер: S|M|L', описанием и критериями приёмки) "
            "или создай spec.md новой спеки specs/NNN-<slug>/. Задачу размера L разбивай "
            "на несколько S/M. Для user story (kind: story) её критерии исполнения "
            "становятся критериями приёмки задач. Разобранные черновики удали. "
            "Не трогай код и защищённые пути.\n\n" + ctx,
            timeout=cfg.stage_timeout_sec,
        )
    if not res.ok:
        print(f"  ✘ аналитик недоступен: {res.error}")
        return 2
    # новая спека без tasks.md → планировщик
    new_specs = [
        p.parent.name for p in (root / "specs").glob("*/spec.md")
        if p.parent.name not in specs_before and not (p.parent / "tasks.md").exists()
    ]
    for spec_name in new_specs:
        with ui.wait(f"планировщик делает plan.md и tasks.md для {spec_name}…"):
            team.run_agent(
                root, "planner",
                f"Для спеки specs/{spec_name}/spec.md создай plan.md и tasks.md строго "
                "в формате проекта (см. specs/001-mvp и AGENTS.md): секции "
                "'## Текущий инкремент' и '## Бэклог', задачи '### T-NNN: …' с критериями приёмки.",
                timeout=cfg.stage_timeout_sec,
            )
    problems = taskmod.lint(root)
    if problems:
        gitops.run_git(root, "reset", "--hard", check=False)
        print("  ✘ результат аналитика не прошёл линт — откачено:")
        for p in problems[:3]:
            print(f"    {p}")
        return 2
    new = [t for t in taskmod.load_tasks(root) if (t.spec, t.id) not in before_ids]
    left = {d.id for d in taskmod.load_drafts(root)}
    for d in ready:
        if d.id in left:
            ui.plain(f"  ⚠ {d.id} не разобран — остался в inbox")
    if not new:
        return 2 if all(d.id in left for d in ready) else 0
    cfg_ = cfg
    gitops.commit_all(
        root, "docs(specs): черновики оформлены в задачи (" + ", ".join(t.id for t in new) + ")",
        cfg_.agent_author_name, cfg_.agent_author_email,
    )
    ui.plain("  ✔ созданы задачи:")
    for t in new:
        from aidevteam.tasks import BUCKET_TITLES

        ui.plain(f"    {t.id} ({t.size})  [{t.spec} / {BUCKET_TITLES[t.bucket]}]  {t.title}")
    ui.plain("\n  Дальше: aidevteam move <ID> current — взять в работу; aidevteam run — выполнить")
    return 0


def cmd_add(root: Path, args: list[str], ui: UI) -> int:
    if args and args[0] == "story":
        title = args[1] if len(args) > 1 else ""
        if not title:
            print('  ✘ Укажите историю: aidevteam add story "Как <роль>, я хочу <что>, чтобы <зачем>"')
            return 1
        desc = ""
        if "--desc" in args:
            desc = args[args.index("--desc") + 1]
        d = taskmod.create_draft(root, title, desc, kind="story")
        ui.plain(f"  ✔ История создана: {d.id} «{title}»")
        ui.plain(f"    файл: {d.path.relative_to(root)} · kind: story")
        ui.plain("\n  История не готова без критериев исполнения (КИ).")
        ui.plain(f"    → aidevteam refine {d.id}   обсудить и собрать КИ с аналитиком")
        ui.plain(f"    → затем aidevteam plan {d.id}   оформить в задачи")
        return 0
    if args and args[0] == "task":
        title = args[1] if len(args) > 1 else ""
        if not title:
            print('  ✘ Укажите название: aidevteam add task "…"')
            return 1
        desc = ""
        if "--desc" in args:
            desc = args[args.index("--desc") + 1]
        d = taskmod.create_draft(root, title, desc)
        ui.plain(f"  ✔ Черновик создан: {d.id} «{title}»")
        ui.plain(f"    файл: {d.path.relative_to(root)} · статус: draft")
        if not desc:
            ui.plain("\n  ⚠ Без деталей аналитик отложит черновик и задаст вопросы.")
            ui.plain(f"    → aidevteam refine {d.id}   диалог с аналитиком (~1 мин)")
        ui.plain("\n  Черновик не закоммичен — его закоммитит от вашего имени run.")
        return 0
    from aidevteam.intake import cmd_add_interactive

    return cmd_add_interactive(root, args, ui)

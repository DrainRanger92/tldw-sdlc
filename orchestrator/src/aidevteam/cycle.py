"""Конвейер одного цикла SDLC."""
from __future__ import annotations

import re
import traceback
from datetime import datetime, timezone
from pathlib import Path

from aidevteam import gitops, team
from aidevteam import tasks as taskmod
from aidevteam.config import Config
from aidevteam.gates import (
    app_gates,
    app_smoke,
    check_learn_paths,
    check_protected,
    sdlc_gates,
)
from aidevteam.state import (
    State,
    append_journal,
    clear_failure,
    read_failure,
    write_failure,
)
from aidevteam.team import AgentResult, BudgetExceeded, CallBudget
from aidevteam.ui import UI


class AidevteamFailure(RuntimeError):
    def __init__(self, stage: str, msg: str):
        super().__init__(msg)
        self.stage = stage


def cmd_init(root: Path, args: list[str], ui: UI) -> int:
    try:
        cfg = Config.load(root)
        cfg.validate()
    except ValueError as exc:
        print(f"  ✘ {exc}")
        return 1
    backend = team.agent_backend(cfg)
    with ui.stage("INIT", "bootstrap окружения") as st:
        if team.probe(root, cfg):
            st.ok(f"{backend} отвечает")
        else:
            if backend == "dsh":
                st.fail("dsh не найден, не отвечает или версия не совпадает — проверьте aidevteam doctor")
            else:
                st.fail("opencode не найден или не отвечает — установите и выполните opencode auth login")
            return 1
        State.load(root).save(root)
        st.ok(".aidevteam/state.json готов")
        name, email = gitops.user_identity(root)
        st.ok(f"git identity пользователя: {name} <{email}>")
    return 0


def cmd_run(root: Path, args: list[str], ui: UI) -> int:
    if "--dry-run" in args:
        from aidevteam.commands import cmd_next

        return cmd_next(root, args, ui)
    from aidevteam.watch import acquire_lock, release_lock

    try:
        cfg = Config.load(root)
        cfg.validate()
    except ValueError as exc:
        print(f"  ✘ {exc}")
        return 1
    if not acquire_lock(root):
        print("  ✘ Уже идёт другой цикл или watch (файл .aidevteam/lock)")
        return 1
    try:
        return run_cycle(root, cfg, ui, agent=team.run_agent)
    finally:
        release_lock(root)


def run_cycle(root: Path, cfg: Config, ui: UI, agent=team.run_agent) -> int:
    state = State.load(root)
    cycle_no = state.cycles + 1
    started = datetime.now(timezone.utc).isoformat()
    budget = CallBudget(cfg.max_agent_calls)
    usage = {"tokens": 0, "cost": 0.0}
    current = {"stage": "PREFLIGHT"}
    ui.plain(f"\naidevteam — цикл #{cycle_no}   (HEAD {gitops.head_sha(root)[:7]})")

    def call(role: str, prompt: str) -> AgentResult:
        current["stage"] = _stage_of(role)
        budget.spend()
        res = agent(root, role, prompt, timeout=cfg.stage_timeout_sec)
        usage["tokens"] += getattr(res, "tokens", 0)
        usage["cost"] += getattr(res, "cost", 0.0)
        if ui.verbose and res.text:
            ui.plain(f"[dim]· {role}: {res.text[:2000]}[/dim]")
        if not res.ok:
            raise AidevteamFailure(_stage_of(role), f"агент {role}: {res.error}")
        return res

    try:
        # --- PREFLIGHT -----------------------------------------------------
        with ui.stage("PREFLIGHT") as st:
            backend = team.agent_backend(cfg)
            if not team.probe(root, cfg):
                if backend == "dsh":
                    st.fail("dsh не отвечает или версия не совпадает с agents.dsh.version")
                else:
                    st.fail("opencode не отвечает — opencode auth login / установка")
                return 1
            st.ok(f"{backend} отвечает")
            dirty = [p for p in gitops.dirty_paths(root) if not p.startswith(".aidevteam/")]
            inbox = [p for p in dirty if p.startswith("specs/inbox/")]
            other = [p for p in dirty if not p.startswith("specs/inbox/")]
            if other:
                st.fail(
                    f"незакоммиченные изменения вне specs/inbox: {other[:3]} — закоммитьте или уберите"
                )
                return 1
            if inbox:
                name, email = gitops.user_identity(root)
                gitops.commit_paths(root, inbox, "docs(inbox): заявки пользователя", name, email)
                st.ok(f"inbox закоммичен от вашего имени ({name})")
            st.ok("рабочая копия чистая")
            lint_problems = taskmod.lint(root)
            if lint_problems:
                for p in lint_problems[:5]:
                    st.fail(p)
                st.fail("артефакты specs/ не проходят линт — исправьте формат")
                return 1
            st.ok("линт артефактов specs/ пройден")
        cycle_start_sha = gitops.head_sha(root)

        # --- SYNC ----------------------------------------------------------
        with ui.stage("SYNC") as st:
            new_commits = gitops.commits_since(root, state.last_processed_commit)
            st.ok(f"новых коммитов: {len(new_commits) or 'нет'}")

        # --- Детерминированный выбор работы --------------------------------
        all_tasks = taskmod.load_tasks(root)
        inc = taskmod.pending_current(all_tasks)
        if not inc:
            if pulled := taskmod.pull_from_backlog(root):
                state.increment_no += 1
                ui.plain(f"  ↻ из бэклога взята {pulled.id} «{pulled.title}» (инкремент #{state.increment_no})")
            all_tasks = taskmod.load_tasks(root)
            inc = taskmod.pending_current(all_tasks)
        drafts = taskmod.load_drafts(root)
        ready = [d for d in drafts if taskmod.draft_ready(d)]
        blocked = [d for d in drafts if d not in ready]
        failure = read_failure(root)

        if not inc and not ready and not failure and not new_commits:
            ui.report(f"ЦИКЛ #{cycle_no}: РАБОТЫ НЕТ", [
                ("Бэклог", "все задачи выполнены"),
                ("Требует вас", "; ".join(
                    f"{d.id} «{d.title}» — aidevteam refine {d.id}" for d in blocked
                ) or "—"),
                ("Дальше", "aidevteam add — поставить новую работу"),
            ])
            return 0

        # --- ANALYZE -------------------------------------------------------
        if failure or new_commits or ready:
            with ui.stage("ANALYZE", "аналитик изучает проект") as st:
                specs_before = {
                    p.parent.name for p in (root / "specs").glob("*/spec.md")
                }
                ctx = []
                if failure:
                    ctx.append(f"Отчёт о сбое прошлого цикла:\n{failure}")
                if new_commits:
                    ctx.append("Новые коммиты:\n" + "\n".join(s for _, s in new_commits))
                for d in ready:
                    ctx.append(f"Черновик {d.id} «{d.title}»:\n{d.path.read_text(encoding='utf-8')}")
                call(
                    "analyst",
                    "Разбери контекст и приведи артефакты specs/ в актуальное состояние: "
                    "оформи готовые черновики в задачи существующей спеки или создай для них "
                    "spec.md новой спеки specs/NNN-<slug>/ (plan.md и tasks.md сделает "
                    "планировщик), для неясных черновиков допиши раздел '## Открытые вопросы' "
                    "с пунктами '- [ ] вопрос'. Каждой задаче ставь оценку "
                    "'- размер: S|M|L'; задачу размера L разбивай на несколько S/M. "
                    "Для user story (kind: story) критерии исполнения черновика становятся "
                    "критериями приёмки задач. Разобранные черновики удали. "
                    "Не трогай код и защищённые пути.\n\n"
                    + "\n\n".join(ctx),
                )
                if failure:
                    clear_failure(root)
                    st.ok("отчёт о сбое разобран")
                # пост-условие: неразобранные готовые черновики — предупреждение
                left = {d.id for d in taskmod.load_drafts(root)}
                for d in ready:
                    if d.id in left and not any(
                        x.open_questions for x in taskmod.load_drafts(root) if x.id == d.id
                    ):
                        st.warn(f"аналитик не разобрал {d.id} — черновик остался в inbox")
                st.ok("артефакты specs/ актуализированы")
        else:
            ui.skip("ANALYZE", "нет новых коммитов, черновиков и сбоев")
            specs_before = {p.parent.name for p in (root / "specs").glob("*/spec.md")}

        # --- SPECIFY / PLAN -------------------------------------------------
        ui.skip("SPECIFY", "делегировано аналитику (ANALYZE)")
        new_specs = [
            p.parent.name
            for p in (root / "specs").glob("*/spec.md")
            if p.parent.name not in specs_before and not (p.parent / "tasks.md").exists()
        ]
        if new_specs:
            with ui.stage("PLAN", "планировщик оформляет новые спеки") as st:
                for spec_name in new_specs:
                    call(
                        "planner",
                        f"Для спеки specs/{spec_name}/spec.md создай plan.md и tasks.md "
                        "строго в формате проекта (см. specs/001-mvp и AGENTS.md): "
                        "секции '## Текущий инкремент' и '## Бэклог', "
                        "задачи '### T-NNN: …' "
                        "с типом, статусом pending, зависимостями, описанием и "
                        "критериями приёмки.",
                    )
                    st.ok(f"specs/{spec_name}: plan.md + tasks.md")
        else:
            ui.skip("PLAN", "бэклог актуален")
        all_tasks = taskmod.load_tasks(root)
        inc = taskmod.pending_current(all_tasks)
        if not inc:
            if taskmod.pull_from_backlog(root):
                state.increment_no += 1
            inc = taskmod.pending_current(taskmod.load_tasks(root))

        # --- EXECUTE -------------------------------------------------------
        done_ids: list[str] = []
        if inc:
            with ui.stage("EXECUTE", f"разработчик выполняет текущий инкремент (#{state.increment_no})") as st:
                for t in inc:
                    prompt = (
                        f"Выполни задачу {t.id}: {t.title}\n\nОписание: {t.description}\n"
                        "Критерии приёмки:\n" + "\n".join(f"- {a}" for a in t.acceptance)
                        + "\n\nПеред работой прочитай app/AGENTS.md, app/memory/MEMORY.md и "
                        "просмотри app/skills/*/SKILL.md. Работай только в app/. "
                        "Не меняй статусы в tasks.md и защищённые пути (orchestrator/, dsh/, docker/)."
                    )
                    call("developer", prompt)
                    if not gitops.changed_since(root, cycle_start_sha):
                        raise AidevteamFailure(
                            "EXECUTE",
                            f"{t.id}: агент завершился успешно, но не изменил ни одного файла "
                            "(возможно, исчерпан лимит провайдера)",
                        )
                    done_ids.append(t.id)
                    st.ok(f"{t.id}  {t.title}")
        else:
            ui.skip("EXECUTE", "задач для выполнения нет")

        # --- REVIEW --------------------------------------------------------
        if done_ids:
            with ui.stage("REVIEW", "ревьюер проверяет изменения") as st:
                approved = False
                for i in range(1, cfg.max_review_iterations + 1):
                    diff_stat = gitops.run_git(root, "diff", "--stat", cycle_start_sha)
                    before = _worktree_snapshot(root, cycle_start_sha)
                    res = call(
                        "reviewer",
                        "Проанализируй изменения рабочей копии (git diff против "
                        f"{cycle_start_sha[:7]}) на соответствие критериям приёмки задач "
                        f"{', '.join(done_ids)} из specs/*/tasks.md и качеству кода. "
                        "Последней строкой выведи ровно 'VERDICT: APPROVE' или 'VERDICT: ISSUES', "
                        f"перед ней — список замечаний.\n\nСтатистика диффа:\n{diff_stat}",
                    )
                    if _worktree_snapshot(root, cycle_start_sha) != before:
                        raise AidevteamFailure(
                            "REVIEW",
                            "ревьюер изменил рабочую копию — ревью должно быть только чтением",
                        )
                    verdict = _parse_verdict(res.text)
                    if verdict == "APPROVE":
                        st.ok(f"итерация {i}: approve")
                        approved = True
                        break
                    st.warn(f"итерация {i}: есть замечания — разработчик исправляет")
                    call(
                        "developer",
                        "Исправь замечания ревьюера, не расширяя объём работ:\n" + res.text,
                    )
                if not approved:
                    d = taskmod.create_draft(
                        root, f"Замечания ревью цикла {cycle_no}",
                        "Не исправлены за лимит итераций — оформить задачами.",
                    )
                    st.warn(f"лимит итераций — остаток замечаний в {d.id}")
        else:
            ui.skip("REVIEW", "нечего ревьюить")

        # --- VERIFY --------------------------------------------------------
        current["stage"] = "VERIFY"
        with ui.stage("VERIFY", "детерминированные гейты") as st:
            changed = gitops.changed_since(root, cycle_start_sha)
            types = {t.type for t in inc} or {"app"}
            prot = check_protected(changed, types, cfg)
            if not prot.ok:
                raise AidevteamFailure("VERIFY", prot.detail)
            st.ok(prot.detail or prot.name)
            lint_problems = taskmod.lint(root)
            if lint_problems:
                raise AidevteamFailure(
                    "VERIFY", "артефакты после цикла не проходят линт: " + "; ".join(lint_problems[:3])
                )
            st.ok("линт артефактов specs/ пройден")
            gate_list = list(app_gates(root / "app", cfg.stage_timeout_sec))
            gate_list.append(app_smoke(root / "app"))
            if "sdlc" in types:
                gate_list.extend(sdlc_gates(root, cfg.stage_timeout_sec))
            for g in gate_list:
                if not g.ok:
                    raise AidevteamFailure("VERIFY", f"{g.name}: {g.detail}")
                st.ok(f"{g.name}: ok" if not g.detail else f"{g.name}: {g.detail[:60]}")

        for t in inc:
            taskmod.mark_done(t)

        # --- LEARN ---------------------------------------------------------
        if done_ids:
            with ui.stage("LEARN", "ретроспектива → память проекта") as st:
                before = set(gitops.dirty_paths(root))
                call(
                    "analyst",
                    "Ретроспектива цикла: зафиксируй уроки. Новые факты — отдельными .md в "
                    "app/memory/facts/ (одна тема = один файл), архитектурные решения — ADR в "
                    "app/memory/decisions/, повторившиеся паттерны — скилом app/skills/<имя>/SKILL.md "
                    "(frontmatter name+description). Обнови индекс app/memory/MEMORY.md. "
                    "ОБЯЗАТЕЛЬНО актуализируй секцию '## Контекст проекта' в "
                    "app/skills/intake/SKILL.md — 2-4 предложения: что приложение умеет "
                    "теперь, какие решения стоит знать при уточнении новых заявок. "
                    "Сравни фактический расход цикла с оценками размеров выполненных задач — "
                    "систематические промахи оценок фиксируй фактом в память. "
                    "Меняй ТОЛЬКО markdown в app/memory/ и app/skills/.",
                )
                learn_changed = [p for p in gitops.dirty_paths(root) if p not in before]
                if learn_changed:
                    gate = check_learn_paths(learn_changed)
                    if not gate.ok:
                        for p in learn_changed:
                            if not (
                                (p.startswith("app/memory/") or p.startswith("app/skills/"))
                                and p.endswith(".md")
                            ):
                                gitops.run_git(root, "checkout", "--", p, check=False)
                        st.warn("лишние изменения LEARN откачены")
                st.ok("память обновлена")
        else:
            ui.skip("LEARN", "не было выполненных задач")

        # --- VERSION -------------------------------------------------------
        new_version = None
        if done_ids:
            current["stage"] = "VERSION"
            with ui.stage("VERSION", "версия приложения и changelog") as st:
                titles = {t.id: t.title for t in all_tasks}
                new_version = bump_app_version(
                    root, cycle_no, [(i, titles.get(i, "")) for i in done_ids]
                )
                if new_version:
                    st.ok(f"app v{new_version} · запись в app/CHANGELOG.md")
                    # VERIFY уже прошёл до bump: проверяем состояние, которое
                    # реально уйдёт в коммит (версия в двух файлах, тесты)
                    after = app_gates(root / "app", cfg.stage_timeout_sec)
                    red = [g for g in after if not g.ok]
                    if red:
                        st.fail("гейты после bump: " + "; ".join(g.name for g in red))
                        raise AidevteamFailure(
                            "VERSION",
                            "после смены версии гейты красные: "
                            + "; ".join(f"{g.name}: {g.detail[:120]}" for g in red),
                        )
                else:
                    st.off("app/package.json | pyproject.toml ещё нет — версия не меняется")
        else:
            ui.skip("VERSION", "не было выполненных задач")

        # --- COMMIT --------------------------------------------------------
        current["stage"] = "COMMIT"
        with ui.stage("COMMIT") as st:
            msg = (
                f"feat(app): цикл {cycle_no} — {', '.join(done_ids)}"
                if done_ids
                else f"chore(sdlc): цикл {cycle_no} — актуализация артефактов"
            )
            sha = gitops.commit_all(root, msg, cfg.agent_author_name, cfg.agent_author_email)
            push = "skipped: отключён конфигом (git.push = never)" if cfg.push == "never" \
                else gitops.try_push(root)
            st.ok(f"main @ {sha[:7]}  {msg}")
            if push == "ok":
                st.ok("push: ok")
            else:
                st.off(f"push: {push}")

        # .aidevteam/ — локальное состояние (в .gitignore); прогресс проекта
        # переносится статусами в tasks.md, которые закоммичены выше.
        state.cycles = cycle_no
        state.last_processed_commit = gitops.head_sha(root)
        state.save(root)
        append_journal(root, {
            "cycle": cycle_no, "started": started,
            "finished": datetime.now(timezone.utc).isoformat(),
            "ok": True, "commit": state.last_processed_commit,
            "summary": f"{len(done_ids)} задач: {', '.join(done_ids) or '—'}",
            "usage": {"calls": budget.used, "tokens": usage["tokens"], "cost": round(usage["cost"], 4)},
            "version": new_version,
        })

        # --- REPORT --------------------------------------------------------
        usage_bits = [f"вызовов LLM: {budget.used}"]
        if usage["tokens"]:
            usage_bits.append(f"токенов: {usage['tokens']}")
        if usage["cost"]:
            usage_bits.append(f"${usage['cost']:.2f}")
        after = taskmod.load_tasks(root)
        remaining = taskmod.pending_current(after)
        queued = [t for t in after if t.status == "pending" and t.bucket == "backlog"]
        blocked = [d for d in taskmod.load_drafts(root) if d.open_questions]
        ui.report(f"ЦИКЛ #{cycle_no} ЗАВЕРШЁН", [
            ("Сделано", f"{len(done_ids)} задач ({', '.join(done_ids) or '—'}), "
                        ", ".join(usage_bits)),
            ("Версия", f"app v{new_version}" if new_version else "—"),
            ("Проверить", "aidevteam app dev  →  http://localhost:3000" if done_ids else "—"),
            ("Требует вас", "; ".join(
                f"{d.id} «{d.title}» — aidevteam refine {d.id}" for d in blocked
            ) or "—"),
            ("Дальше", "текущий инкремент: " + ", ".join(t.id for t in remaining)
             if remaining else (
                 "бэклог: " + ", ".join(t.id for t in queued)
                 if queued else "работы нет — aidevteam add"
             )),
        ])
        return 0

    except KeyboardInterrupt:
        stage = current["stage"]
        dirty = [p for p in gitops.dirty_paths(root) if not p.startswith(".aidevteam/")]
        write_failure(
            root, stage,
            "Цикл прерван пользователем (Ctrl+C).\n"
            f"Незакоммиченных файлов в рабочей копии: {len(dirty)}.",
        )
        append_journal(root, {
            "cycle": cycle_no, "started": started,
            "finished": datetime.now(timezone.utc).isoformat(),
            "ok": False, "commit": None, "summary": f"прерван пользователем на {stage}",
            "usage": {"calls": budget.used, "tokens": usage["tokens"], "cost": round(usage["cost"], 4)},
        })
        ui.report(f"ЦИКЛ #{cycle_no}: ПРЕРВАН ({stage})", [
            ("Состояние", "main не тронут; частичная работа агента осталась в рабочей копии"
             if dirty else "main не тронут; рабочая копия чистая"),
            ("Ваш выбор", "оставить: git add -A && git commit; выбросить: git checkout -- . && git clean -fd app"
             if dirty else "—"),
            ("Дальше", "следующий aidevteam run разберёт отчёт о прерывании"),
        ])
        return 130

    except (AidevteamFailure, BudgetExceeded) as exc:
        stage = getattr(exc, "stage", "CYCLE")
        write_failure(root, stage, f"{exc}\n\n{traceback.format_exc(limit=3)}")
        append_journal(root, {
            "cycle": cycle_no, "started": started,
            "finished": datetime.now(timezone.utc).isoformat(),
            "ok": False, "commit": None, "summary": f"сбой на {stage}: {exc}",
            "usage": {"calls": budget.used, "tokens": usage["tokens"], "cost": round(usage["cost"], 4)},
        })
        ui.report(f"ЦИКЛ #{cycle_no}: СБОЙ ({stage})", [
            ("Ошибка", str(exc)[:200]),
            ("Состояние", "main не тронут; изменения остались в рабочей копии"),
            ("Дальше", "следующий aidevteam run разберёт .aidevteam/last-failure.md"),
        ])
        return 2


def _worktree_snapshot(root: Path, base: str) -> tuple[set[str], str]:
    """Слепок worktree: ревьюер не должен менять ни файлы, ни diff."""
    return set(gitops.dirty_paths(root)), gitops.run_git(root, "diff", base)


def _parse_verdict(text: str) -> str:
    """Строгий разбор вердикта ревьюера; нет явного вердикта — считаем ISSUES."""
    verdict = "ISSUES"
    for line in text.splitlines():
        m = re.match(r"^\s*VERDICT:\s*(APPROVE|ISSUES)\s*$", line.strip())
        if m:
            verdict = m.group(1)
    return verdict


def _stage_of(role: str) -> str:
    return {"analyst": "ANALYZE", "developer": "EXECUTE", "reviewer": "REVIEW", "planner": "PLAN"}.get(role, "CYCLE")


def bump_app_version(root: Path, cycle_no: int, done: list[tuple[str, str]]) -> str | None:
    """Поднять minor-версию приложения (package.json или pyproject.toml) и дописать CHANGELOG."""
    import json
    import re as _re
    from datetime import date

    pkg_file = root / "app" / "package.json"
    pyproject_file = root / "app" / "pyproject.toml"
    if pkg_file.exists():
        pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
        new = _next_minor(pkg.get("version", "0.0.0"))
        pkg["version"] = new
        pkg_file.write_text(json.dumps(pkg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif pyproject_file.exists():
        text = pyproject_file.read_text(encoding="utf-8")
        m = _re.search(r'(?m)^(version\s*=\s*")(\d+(?:\.\d+)*)(")', text)
        if m is None:
            return None
        new = _next_minor(m.group(2))
        text = text[: m.start()] + m.group(1) + new + m.group(3) + text[m.end():]
        pyproject_file.write_text(text, encoding="utf-8")
    else:
        return None
    _sync_package_version(root, new)
    ch = root / "app" / "CHANGELOG.md"
    entry = (
        f"## {new} — цикл {cycle_no}, {date.today().isoformat()}\n\n"
        + "\n".join(f"- {tid} {title}".rstrip() for tid, title in done)
        + "\n"
    )
    if ch.exists():
        text = ch.read_text(encoding="utf-8")
        if text.startswith("# Changelog"):
            head, _, rest = text.partition("\n")
            text = head + "\n\n" + entry + "\n" + rest.lstrip("\n")
        else:
            text = entry + "\n" + text
    else:
        text = "# Changelog\n\n" + entry
    ch.write_text(text, encoding="utf-8")
    return new


def _sync_package_version(root: Path, version: str) -> None:
    """Держать `__version__` пакета приложения в ногу с метаданными.

    Иначе после bump версии `pyproject.toml` и `tldw/__init__.py` расходятся,
    и тест согласованности версий падает уже после коммита.
    """
    for init in sorted((root / "app").glob("*/__init__.py")):
        if any(part in (".venv", "site-packages", "build", "dist") for part in init.parts):
            continue
        text = init.read_text(encoding="utf-8")
        m = re.search(r'(?m)^(__version__\s*=\s*")([^"]*)(")', text)
        if m is None or m.group(2) == version:
            continue
        init.write_text(
            text[: m.start()] + m.group(1) + version + m.group(3) + text[m.end():],
            encoding="utf-8",
        )


def _next_minor(version: str) -> str:
    """0.1.4 → 0.2.0: версия приложения растёт по minor на каждый цикл."""
    parts = version.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except ValueError:
        return "0.1.0"
    return f"{major}.{minor + 1}.0"

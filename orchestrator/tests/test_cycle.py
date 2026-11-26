from pathlib import Path

from aidevteam import gitops
from aidevteam import tasks as taskmod
from aidevteam.config import Config
from aidevteam.cycle import run_cycle
from aidevteam.state import State, read_failure
from aidevteam.team import AgentResult
from aidevteam.ui import UI

FAKE_OPENCODE = str(Path(__file__).parent / "fake_opencode")


def make_agent(repo, verdict="VERDICT: APPROVE"):
    calls = []

    def agent(root, role, prompt, timeout=0, session=None):
        calls.append(role)
        if role == "developer":
            appsrc = repo / "app" / "src"
            appsrc.mkdir(parents=True, exist_ok=True)
            (appsrc / "index.ts").write_text("// код от разработчика\n")
        if role == "reviewer":
            return AgentResult(True, f"замечаний нет\n{verdict}")
        if role == "analyst":
            (repo / "app" / "memory" / "facts" / "urok.md").write_text("# Урок\n")
        return AgentResult(True, "ok")

    return agent, calls


def test_happy_cycle(repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)
    agent, calls = make_agent(repo)
    code = run_cycle(repo, Config(), UI(), agent=agent)
    assert code == 0
    assert "developer" in calls and "reviewer" in calls
    st = State.load(repo)
    assert st.cycles == 1 and st.last_processed_commit == gitops.head_sha(repo)
    tasks = taskmod.load_tasks(repo)
    assert tasks[0].status == "done" and tasks[1].status == "done"
    assert tasks[2].status == "pending"  # инкремент 2 не тронут
    author = gitops.run_git(repo, "log", "-1", "--format=%an")
    assert author.strip() == "aidevteam-agent"


def test_dirty_worktree_blocks(repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)
    (repo / "random.txt").write_text("dirt")
    agent, _ = make_agent(repo)
    assert run_cycle(repo, Config(), UI(), agent=agent) == 1


def test_inbox_committed_as_user(repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)
    (repo / "specs" / "inbox" / "D-001-x.md").write_text(
        "---\nid: D-001\ntitle: X\nstatus: draft\n---\n\n## Описание\nтекст\n"
    )
    agent, _ = make_agent(repo)
    assert run_cycle(repo, Config(), UI(), agent=agent) == 0
    log = gitops.run_git(repo, "log", "--format=%an %s")
    assert "Test User" in log  # черновик закоммичен от имени пользователя


def test_failed_developer_writes_failure(repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)

    def agent(root, role, prompt, timeout=0, session=None):
        return AgentResult(False, "", None, "LLM недоступен")

    assert run_cycle(repo, Config(), UI(), agent=agent) == 2
    assert "EXECUTE" in read_failure(repo)


def test_sloppy_verdict_treated_as_issues(repo, monkeypatch):
    """Вердикт без строгой строки 'VERDICT: APPROVE' не считается одобрением."""
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)
    agent, _ = make_agent(repo, verdict="я бы дал APPROVE, но не уверен")
    code = run_cycle(repo, Config(), UI(), agent=agent)
    assert code == 0
    drafts = taskmod.load_drafts(repo)
    assert any("Замечания ревью" in d.title for d in drafts)


def test_run_respects_lock(repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)
    from aidevteam.commands import dispatch_in
    from aidevteam.watch import acquire_lock, release_lock

    assert acquire_lock(repo)
    try:
        assert dispatch_in(repo, "run", []) == 1
    finally:
        release_lock(repo)


def test_preflight_lint_blocks(repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)
    f = repo / "specs" / "001-mvp" / "tasks.md"
    f.write_text(
        f.read_text(encoding="utf-8").replace("- зависит: T-001", "- зависит: T-099"),
        encoding="utf-8",
    )
    gitops.commit_all(repo, "ломаем зависимости", "x", "x@x")
    agent, calls = make_agent(repo)
    assert run_cycle(repo, Config(), UI(), agent=agent) == 1
    assert calls == []


def test_interrupt_graceful(repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)

    def agent(root, role, prompt, timeout=0, session=None):
        raise KeyboardInterrupt

    code = run_cycle(repo, Config(), UI(), agent=agent)
    assert code == 130
    assert "прерван" in read_failure(repo).lower()


def test_cli_interrupt(monkeypatch, capsys):
    from aidevteam import cli, commands

    def boom(cmd, args):
        raise KeyboardInterrupt

    monkeypatch.setattr(commands, "dispatch", boom)
    assert cli.main(["status"]) == 130
    assert "Прервано" in capsys.readouterr().out


def test_no_work_exits_zero(repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)
    for t in taskmod.load_tasks(repo):
        taskmod.mark_done(t)
    gitops.commit_all(repo, "все задачи done", "x", "x@x")
    st = State.load(repo)
    st.last_processed_commit = gitops.head_sha(repo)
    st.save(repo)
    agent, calls = make_agent(repo)
    assert run_cycle(repo, Config(), UI(), agent=agent) == 0
    assert calls == []  # ни одного вызова агентов


def test_version_stage_bumps_app(repo, monkeypatch):
    import json

    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)
    (repo / "app" / "package.json").write_text(
        json.dumps({"name": "notebook", "version": "0.1.0", "scripts": {}})
    )
    gitops.commit_all(repo, "seed app pkg", "x", "x@x")
    agent, _ = make_agent(repo)
    assert run_cycle(repo, Config(), UI(), agent=agent) == 0
    pkg = json.loads((repo / "app" / "package.json").read_text())
    assert pkg["version"] == "0.2.0"
    changelog = (repo / "app" / "CHANGELOG.md").read_text()
    assert "0.2.0" in changelog and "T-001" in changelog


def test_cycle_dsh_preflight(repo, monkeypatch):
    (repo / "aidevteam.toml").write_text(
        '[agents]\nbackend = "dsh"\n', encoding="utf-8"
    )
    roles = repo / "dsh" / "roles"
    roles.mkdir(parents=True)
    for role in ("analyst", "planner", "developer", "reviewer"):
        (roles / f"{role}.yml").write_text("- id: system-prompt\n", encoding="utf-8")
    gitops.commit_all(repo, "dsh roles", "x", "x@x")
    monkeypatch.setenv("AIDEVTEAM_DSH_BIN", str(Path(__file__).parent / "fake_dsh"))
    agent, calls = make_agent(repo)
    code = run_cycle(repo, Config.load(repo), UI(), agent=agent)
    assert code == 0
    assert "developer" in calls


def test_reviewer_cannot_modify_worktree(repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)

    def agent(root, role, prompt, timeout=0, session=None):
        if role == "developer":
            src = repo / "app" / "src"
            src.mkdir(parents=True, exist_ok=True)
            (src / "index.ts").write_text("// ok\n")
        if role == "reviewer":
            (repo / "hack.txt").write_text("reviewer changed the worktree")
            return AgentResult(True, "VERDICT: APPROVE")
        return AgentResult(True, "ok")

    assert run_cycle(repo, Config(), UI(), agent=agent) == 2
    assert "РЕВЬЮЕР" in read_failure(repo).upper()


def test_version_stage_bumps_app_pyproject(repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)
    (repo / "app" / "pyproject.toml").write_text(
        '[project]\nname = "tldw"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    gitops.commit_all(repo, "seed app pyproject", "x", "x@x")
    agent, _ = make_agent(repo)
    assert run_cycle(repo, Config(), UI(), agent=agent) == 0
    text = (repo / "app" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.2.0"' in text
    assert "0.2.0" in (repo / "app" / "CHANGELOG.md").read_text(encoding="utf-8")

import contextlib
import io
from pathlib import Path

from aidevteam.commands import dispatch_in

FAKE_OPENCODE = str(Path(__file__).parent / "fake_opencode")
FAKE_DSH = str(Path(__file__).parent / "fake_dsh")


def run(repo, *argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = dispatch_in(repo, argv[0], list(argv[1:]))
    return code, buf.getvalue()


def test_status(repo):
    code, out = run(repo, "status")
    assert code == 0
    assert "001-mvp" in out and "pending" in out


def test_next_shows_increment(repo):
    code, out = run(repo, "next")
    assert code == 0
    assert "T-001" in out and "T-002" in out and "T-003" not in out


def test_list_tasks_grouped(repo):
    code, out = run(repo, "list", "tasks")
    assert code == 0
    assert "Сейчас в работе" in out and "Бэклог" in out and "T-003" in out


def test_show_task(repo):
    code, out = run(repo, "show", "task", "T-001")
    assert code == 0
    assert "Критерии приёмки" in out and "GET /health" in out
    assert run(repo, "show", "task", "T-999")[0] == 1


def test_add_task_creates_draft(repo):
    code, out = run(repo, "add", "task", "Новая фича")
    assert code == 0 and "D-001" in out
    assert list((repo / "specs" / "inbox").glob("D-001-*.md"))


def test_log_empty(repo):
    code, out = run(repo, "log")
    assert code == 0 and "цикл" in out.lower()


def test_doctor_reports_missing_roles(repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)
    code, out = run(repo, "doctor")
    assert code == 1  # в фикстуре нет .opencode/agent/*
    assert "нет роли" in out


def test_doctor_ok_with_roles(repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)
    agents = repo / ".opencode" / "agent"
    agents.mkdir(parents=True)
    for role in ("analyst", "planner", "developer", "reviewer"):
        (agents / f"{role}.md").write_text("---\ndescription: x\n---\n")
    code, out = run(repo, "doctor")
    assert code == 0
    assert "Все обязательные компоненты на месте" in out


def test_add_story_creates_story_draft(repo):
    code, out = run(repo, "add", "story", "Как пользователь, я хочу экспорт")
    assert code == 0
    assert "kind: story" in out and "КИ" in out
    from aidevteam.tasks import load_drafts

    d = load_drafts(repo)[0]
    assert d.kind == "story"


def _dsh_project(repo: Path) -> None:
    (repo / "aidevteam.toml").write_text(
        '[agents]\nbackend = "dsh"\n\n'
        '[agents.dsh]\nversion = "0.2.2-rc.1"\n',
        encoding="utf-8",
    )
    (repo / ".env").write_text("DEEPSEEK_API_KEY=test-key\n", encoding="utf-8")
    roles = repo / "dsh" / "roles"
    roles.mkdir(parents=True)
    for role in ("analyst", "planner", "developer", "reviewer"):
        (roles / f"{role}.yml").write_text("- id: system-prompt\n", encoding="utf-8")


def test_doctor_dsh_ok(repo, monkeypatch):
    _dsh_project(repo)
    monkeypatch.setenv("AIDEVTEAM_DSH_BIN", FAKE_DSH)
    code, out = run(repo, "doctor")
    assert code == 0, out
    assert "состав роли analyst" in out
    assert "ключ DEEPSEEK_API_KEY найден" in out


def test_doctor_dsh_reports_missing_role(repo, monkeypatch):
    _dsh_project(repo)
    (repo / "dsh" / "roles" / "reviewer.yml").unlink()
    monkeypatch.setenv("AIDEVTEAM_DSH_BIN", FAKE_DSH)
    code, out = run(repo, "doctor")
    assert code == 1
    assert "нет оверлея" in out


def test_doctor_dsh_reports_version_mismatch(repo, monkeypatch):
    _dsh_project(repo)
    (repo / "aidevteam.toml").write_text(
        '[agents]\nbackend = "dsh"\n\n'
        '[agents.dsh]\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("AIDEVTEAM_DSH_BIN", FAKE_DSH)
    code, out = run(repo, "doctor")
    assert code == 1
    assert "ожидается 9.9.9" in out


def test_run_rejects_unknown_backend(repo):
    (repo / "aidevteam.toml").write_text(
        '[agents]\nbackend = "nope"\n', encoding="utf-8"
    )
    code, out = run(repo, "run")
    assert code == 1
    assert "opencode или dsh" in out

from pathlib import Path

import pytest

from aidevteam import team
from aidevteam.config import Config
from aidevteam.team import BudgetExceeded, CallBudget, probe, read_env_file, run_agent

FAKE_OPENCODE = str(Path(__file__).parent / "fake_opencode")
FAKE_DSH = str(Path(__file__).parent / "fake_dsh")


@pytest.fixture(autouse=True)
def fake_bin(monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_OPENCODE_BIN", FAKE_OPENCODE)
    monkeypatch.delenv("AIDEVTEAM_AGENTS_BACKEND", raising=False)
    monkeypatch.delenv("AIDEVTEAM_DSH_BIN", raising=False)


@pytest.fixture
def dsh_repo(repo, monkeypatch):
    roles = repo / "dsh" / "roles"
    roles.mkdir(parents=True)
    for role in ("analyst", "planner", "developer", "reviewer"):
        (roles / f"{role}.yml").write_text("- id: system-prompt\n", encoding="utf-8")
    monkeypatch.setenv("AIDEVTEAM_AGENTS_BACKEND", "dsh")
    monkeypatch.setenv("AIDEVTEAM_DSH_BIN", FAKE_DSH)
    return repo


def test_run_agent_ok(repo):
    res = run_agent(repo, "developer", "сделай T-001")
    assert res.ok
    assert "--agent developer" in res.text and "сделай T-001" in res.text


def test_run_agent_failure(repo, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail")
    res = run_agent(repo, "reviewer", "проверь")
    assert not res.ok and "boom" in res.error


def test_probe(repo):
    assert probe(repo) is True


def test_budget():
    b = CallBudget(2)
    b.spend()
    b.spend()
    with pytest.raises(BudgetExceeded):
        b.spend()


def test_run_agent_parses_json_events(repo, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "json")
    res = run_agent(repo, "analyst", "вопрос")
    assert res.ok
    assert res.text == "привет от агента"
    assert res.session_id == "ses_fake123"
    assert res.tokens == 1500 and res.cost == 0.05


def test_dsh_run_agent_ok(dsh_repo):
    res = run_agent(dsh_repo, "developer", "сделай T-001")
    assert res.ok, res.error
    assert "--profile headless" in res.text
    assert "dsh/roles/developer.yml" in res.text
    assert "--permission-mode danger-full-access" in res.text
    assert "FAKE_DSH_PROMPT: сделай T-001" in res.text
    assert res.session_id == "session-fake123"


def test_dsh_probe(dsh_repo):
    assert probe(dsh_repo) is True


def test_dsh_probe_missing_bin(dsh_repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_DSH_BIN", "/nonexistent/dsh")
    assert probe(dsh_repo) is False


def test_dsh_probe_rejects_version_mismatch(dsh_repo):
    (dsh_repo / "aidevteam.toml").write_text(
        '[agents]\nbackend = "dsh"\n\n[agents.dsh]\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    assert probe(dsh_repo) is False


def test_dsh_failure(dsh_repo, monkeypatch):
    monkeypatch.setenv("FAKE_DSH_MODE", "fail")
    res = run_agent(dsh_repo, "reviewer", "проверь")
    assert not res.ok and "AUTH" in res.error


def test_dsh_error_event_reason(dsh_repo, monkeypatch):
    monkeypatch.setenv("FAKE_DSH_MODE", "error_event")
    res = run_agent(dsh_repo, "analyst", "вопрос")
    assert not res.ok
    assert "PROVIDER" in res.error and "провайдер недоступен" in res.error


def test_dsh_timeout(dsh_repo, monkeypatch):
    monkeypatch.setenv("FAKE_DSH_MODE", "timeout")
    monkeypatch.setenv("FAKE_DSH_SLEEP", "10")
    res = run_agent(dsh_repo, "analyst", "вопрос", timeout=0.2)
    assert not res.ok and "таймаут" in res.error


def test_dsh_empty_answer_is_failure(dsh_repo, monkeypatch):
    monkeypatch.setenv("FAKE_DSH_MODE", "empty")
    res = run_agent(dsh_repo, "analyst", "вопрос")
    assert not res.ok and "пустой ответ" in res.error


def test_dsh_bad_reason_is_failure(dsh_repo, monkeypatch):
    monkeypatch.setenv("FAKE_DSH_MODE", "bad_reason")
    res = run_agent(dsh_repo, "analyst", "вопрос")
    assert not res.ok and "aborted" in res.error


def test_dsh_missing_role(dsh_repo):
    (dsh_repo / "dsh" / "roles" / "reviewer.yml").unlink()
    res = run_agent(dsh_repo, "reviewer", "проверь")
    assert not res.ok and "нет оверлея роли" in res.error


def test_dsh_reviewer_uses_read_only(dsh_repo):
    res = run_agent(dsh_repo, "reviewer", "проверь")
    assert res.ok, res.error
    assert "--permission-mode read-only" in res.text


def test_dsh_developer_uses_full_access(dsh_repo):
    res = run_agent(dsh_repo, "developer", "сделай")
    assert res.ok, res.error
    assert "--permission-mode danger-full-access" in res.text


def test_dsh_session_is_passed(dsh_repo):
    res = run_agent(dsh_repo, "analyst", "продолжай", session="session-existing")
    assert res.ok, res.error
    assert "--session-id session-existing" in res.text


def test_dsh_model_overlay(dsh_repo):
    (dsh_repo / "aidevteam.toml").write_text(
        '[agents]\nbackend = "dsh"\n\n'
        '[agents.models]\nreviewer = "deepseek-official/deepseek-reasoner"\n',
        encoding="utf-8",
    )
    res = run_agent(dsh_repo, "reviewer", "проверь")
    assert res.ok, res.error
    overlay = dsh_repo / ".aidevteam" / "dsh" / "model-reviewer.yml"
    assert "--patch" in res.text and str(overlay) in res.text
    text = overlay.read_text(encoding="utf-8")
    assert 'provider: "deepseek-official"' in text
    assert 'model: "deepseek-reasoner"' in text


def test_dsh_invalid_model_spec(dsh_repo):
    (dsh_repo / "aidevteam.toml").write_text(
        '[agents]\nbackend = "dsh"\n\n[agents.models]\nreviewer = "no-provider"\n',
        encoding="utf-8",
    )
    res = run_agent(dsh_repo, "reviewer", "проверь")
    assert not res.ok and "provider/model" in res.error


def test_dsh_composition_ok(dsh_repo):
    cfg = Config.load(dsh_repo)
    assert team.dsh_composition_problems(dsh_repo, cfg, "analyst") == []
    assert team.dsh_composition_problems(dsh_repo, cfg, "planner") == []
    assert team.dsh_composition_problems(dsh_repo, cfg, "developer") == []
    assert team.dsh_composition_problems(dsh_repo, cfg, "reviewer") == []


def test_dsh_composition_detects_wrong_write(dsh_repo, monkeypatch):
    dump = """- id: system-prompt
  config:
    personaPrefix: >-
      Ты — ревьюер.
- id: tool-fs
  config:
    write: true
    edit: true
- id: tool-subagent
  disabled: true
- id: tool-subagent-fork
  disabled: true
- id: tool-workflow
  disabled: true
"""
    monkeypatch.setattr(team, "dsh_composition", lambda root, cfg, role: (dump, ""))
    problems = team.dsh_composition_problems(dsh_repo, Config.load(dsh_repo), "reviewer")
    assert any("tool-fs.write" in p for p in problems)


def test_unknown_backend(repo, monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_AGENTS_BACKEND", "nope")
    res = run_agent(repo, "analyst", "вопрос")
    assert not res.ok and "неизвестный бэкенд" in res.error


def test_read_env_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "# comment\nexport A=1\nB = \"two words\"\nC='three'\nEMPTY=\nnoeq\n",
        encoding="utf-8",
    )
    assert read_env_file(p) == {"A": "1", "B": "two words", "C": "three", "EMPTY": ""}


def test_dsh_reads_project_env(dsh_repo, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    (dsh_repo / ".env").write_text("DEEPSEEK_API_KEY=from-file\n", encoding="utf-8")
    res = run_agent(dsh_repo, "analyst", "вопрос")
    assert res.ok, res.error
    assert "FAKE_DSH_KEY: from-file" in res.text


def test_dsh_keyboard_interrupt_kills_child(dsh_repo, tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time

    marker = tmp_path / "child-survived"
    env = os.environ.copy()
    env.update({
        "AIDEVTEAM_AGENTS_BACKEND": "dsh",
        "AIDEVTEAM_DSH_BIN": FAKE_DSH,
        "FAKE_DSH_MODE": "timeout",
        "FAKE_DSH_SLEEP": "2",
        "FAKE_DSH_MARKER": str(marker),
    })
    script = (
        "from pathlib import Path\n"
        "from aidevteam import team\n"
        "team.run_agent(Path('.'), 'analyst', 'x', timeout=30)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script], cwd=dsh_repo, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        time.sleep(0.8)
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    time.sleep(2.3)
    assert not marker.exists(), "DSH-потомок пережил Ctrl+C"

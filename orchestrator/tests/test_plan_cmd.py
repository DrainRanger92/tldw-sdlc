import contextlib
import io

from aidevteam import team
from aidevteam.commands import dispatch_in
from aidevteam.tasks import create_draft, load_drafts, load_tasks
from aidevteam.team import AgentResult


def run(repo, *argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = dispatch_in(repo, argv[0], list(argv[1:]))
    return code, buf.getvalue()


def digesting_agent(repo):
    """Фейковый аналитик: оформляет черновик в задачу и удаляет его."""

    def agent(root, role, prompt, timeout=0, session=None):
        f = repo / "specs" / "001-mvp" / "tasks.md"
        f.write_text(
            f.read_text(encoding="utf-8")
            + "\n### T-004: Экспорт заметок\n- тип: app\n- статус: pending\n"
              "- зависит: T-003\n\n**Описание**\nЭкспорт в markdown.\n\n"
              "**Критерии приёмки**\n- [ ] GET /api/export отдаёт markdown\n",
            encoding="utf-8",
        )
        for d in load_drafts(repo):
            d.path.unlink()
        return AgentResult(True, "оформлено")

    return agent


def test_plan_digests_ready_draft(repo, monkeypatch):
    create_draft(repo, "Экспорт заметок", "Кнопка экспорта всех заметок в markdown")
    monkeypatch.setattr(team, "run_agent", digesting_agent(repo))
    code, out = run(repo, "plan", "D-001")
    assert code == 0, out
    assert "T-004" in out
    assert load_drafts(repo) == []
    assert any(t.id == "T-004" for t in load_tasks(repo))


def test_plan_rejects_draft_with_questions(repo, monkeypatch):
    d = create_draft(repo, "Неясная фича", "что-то")
    d.path.write_text(
        d.path.read_text(encoding="utf-8") + "\n## Открытые вопросы\n- [ ] что именно?\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(team, "run_agent", digesting_agent(repo))
    code, out = run(repo, "plan", "D-001")
    assert code == 1
    assert "refine D-001" in out


def test_plan_reverts_on_lint_failure(repo, monkeypatch):
    create_draft(repo, "Фича", "описание есть")

    def bad_agent(root, role, prompt, timeout=0, session=None):
        f = repo / "specs" / "001-mvp" / "tasks.md"
        f.write_text(
            f.read_text(encoding="utf-8")
            + "\n### T-004: Кривая\n- тип: app\n- статус: pending\n- зависит: T-777\n\n"
              "**Описание**\nx\n\n**Критерии приёмки**\n- [ ] x\n",
            encoding="utf-8",
        )
        return AgentResult(True, "готово")

    monkeypatch.setattr(team, "run_agent", bad_agent)
    code, out = run(repo, "plan", "D-001")
    assert code == 2
    assert "не прошёл линт" in out
    assert not any(t.id == "T-004" for t in load_tasks(repo))  # откачено

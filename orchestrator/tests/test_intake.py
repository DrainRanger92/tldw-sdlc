from aidevteam.intake import dialog
from aidevteam.tasks import create_draft
from aidevteam.team import AgentResult
from aidevteam.ui import UI


def scripted_agent(replies):
    it = iter(replies)
    prompts = []

    def agent(root, role, prompt, timeout=0, session=None):
        prompts.append(prompt)
        return AgentResult(True, next(it), session_id="s1")

    agent.prompts = prompts
    return agent


def test_first_message_carries_project_context(repo):
    d = create_draft(repo, "Фича", "")
    agent = scripted_agent(["ок\nINTAKE: DONE"])
    dialog(repo, UI(), d, agent, input_fn=lambda _: "")
    first = agent.prompts[0]
    assert "Записная книжка" in first  # vision из specs/product.md
    assert "T-001" in first  # состояние бэклога
    assert "в контексте этого проекта" in first  # инструкция


def test_first_message_includes_intake_skill(repo):
    skill = repo / "app" / "skills" / "intake"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: intake\ndescription: x\n---\n\n"
        "## Контекст проекта\nСтек фиксирован: не спрашивай про технологии.\n",
        encoding="utf-8",
    )
    d = create_draft(repo, "Фича", "")
    agent = scripted_agent(["ок\nINTAKE: DONE"])
    dialog(repo, UI(), d, agent, input_fn=lambda _: "")
    first = agent.prompts[0]
    assert "Скил intake" in first
    assert "Стек фиксирован" in first
    assert "name: intake" not in first  # frontmatter отрезан


def test_dialog_until_done(repo, capsys):
    d = create_draft(repo, "Вход по паролю", "")
    agent = scripted_agent(["Один пользователь или несколько?", "Понял.\nINTAKE: DONE"])
    answers = iter(["несколько, с регистрацией"])
    code = dialog(repo, UI(), d, agent, input_fn=lambda _: next(answers))
    assert code == 0
    out = capsys.readouterr().out
    assert "Один пользователь" in out and "Черновик обновлён" in out


def test_dialog_interrupt_saves(repo, capsys):
    d = create_draft(repo, "Фича", "")
    agent = scripted_agent(["Что именно нужно?"])

    def raise_interrupt(_):
        raise KeyboardInterrupt

    code = dialog(repo, UI(), d, agent, input_fn=raise_interrupt)
    assert code == 0
    assert "прервана" in capsys.readouterr().out.lower()

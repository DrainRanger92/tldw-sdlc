"""Интерактивный intake: диалог с аналитиком по черновику."""
from __future__ import annotations

from pathlib import Path

from aidevteam import team
from aidevteam import tasks as taskmod
from aidevteam.ui import UI

_INTAKE_PROMPT = (
    "Ты уточняешь заявку пользователя (файл {path}). ПЕРЕД первым вопросом изучи "
    "контекст проекта (приложен ниже; при необходимости читай файлы репозитория сам: "
    "specs/, app/, app/memory/). Вопросы задавай ТОЛЬКО в контексте этого проекта "
    "и только о том, чего нет ни в заявке, ни в репозитории — не спрашивай то, что "
    "можно узнать из кода или спек. По одному короткому вопросу за раз. "
    "Ответы записывай в файл черновика (секция '## Описание', открытые вопросы — "
    "'## Открытые вопросы' пунктами '- [ ] …'; отвеченные помечай [x]). "
    "Пустой ответ пользователя = 'на усмотрение команды'. "
    "Если черновик — пользовательская история (kind: story): помоги сформулировать её "
    "в виде 'Как <роль>, я хочу <возможность>, чтобы <ценность>' и собери в секции "
    "'## Критерии исполнения' 2-5 проверяемых пунктов '- [ ] …' (КИ) — обсуждай их с "
    "пользователем по одному. История не готова без КИ. Когда информации достаточно, "
    "обнови файл и закончи реплику строкой 'INTAKE: DONE'."
)


def _intake_skill(root: Path) -> str:
    """Тело скила intake (app/skills/intake/SKILL.md) без frontmatter."""
    f = root / "app" / "skills" / "intake" / "SKILL.md"
    if not f.exists():
        return ""
    text = f.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        text = parts[2] if len(parts) == 3 else text
    return text.strip()


def _project_context(root: Path) -> str:
    """Детерминированная сводка проекта для первого сообщения аналитику."""
    from aidevteam import tasks as taskmod

    parts: list[str] = []
    product = root / "specs" / "product.md"
    if product.exists():
        parts.append("=== Vision (specs/product.md) ===\n" + product.read_text(encoding="utf-8")[:2000])
    tasks = taskmod.load_tasks(root)
    done = [t for t in tasks if t.status == "done"]
    pending = [t for t in tasks if t.status == "pending"]
    if done:
        parts.append("=== Уже сделано ===\n" + "\n".join(f"- {t.id} {t.title}" for t in done[-15:]))
    if pending:
        parts.append("=== В бэклоге ===\n" + "\n".join(f"- {t.id} {t.title}" for t in pending[:15]))
    mem = root / "app" / "memory" / "MEMORY.md"
    if mem.exists():
        parts.append("=== Память проекта (app/memory/MEMORY.md) ===\n" + mem.read_text(encoding="utf-8")[:1500])
    skills = sorted(p.parent.name for p in (root / "app" / "skills").glob("*/SKILL.md"))
    if skills:
        parts.append("=== Скилы ===\n" + ", ".join(skills))
    return "\n\n".join(parts)


def dialog(root: Path, ui: UI, draft, agent=team.run_agent, input_fn=input) -> int:
    ui.plain(f"Уточнение черновика {draft.id} «{draft.title}»")
    ui.plain("Отвечайте свободным текстом; пустой ответ = «на усмотрение команды»; Ctrl+C — прервать.\n")
    session = None
    skill = _intake_skill(root)
    message = (
        _INTAKE_PROMPT.format(path=draft.path.relative_to(root))
        + (f"\n\n=== Скил intake (правила уточнения в этом проекте) ===\n{skill}" if skill else "")
        + f"\n\nТекущий черновик:\n{draft.path.read_text(encoding='utf-8')}"
        + f"\n\n{_project_context(root)}"
    )
    while True:
        with ui.wait("аналитик думает… (обычно 10–60 секунд, Ctrl+C — прервать)"):
            res = agent(root, "analyst", message, session=session)
        if not res.ok:
            ui.plain(f"  ✘ аналитик недоступен: {res.error}")
            return 1
        session = res.session_id or session
        reply = res.text.strip()
        if "INTAKE: DONE" in reply:
            ui.plain("  Аналитик: " + reply.replace("INTAKE: DONE", "").strip())
            ui.plain(f"\n  ✔ Черновик обновлён: {draft.path.relative_to(root)}")
            ui.plain("  Ближайший aidevteam run возьмёт его в работу.")
            return 0
        ui.plain(f"  Аналитик: {reply}\n")
        try:
            answer = input_fn(f"{draft.id} > ")
        except (KeyboardInterrupt, EOFError):
            ui.plain("\n  Сессия прервана — прогресс сохранён в черновике.")
            ui.plain(f"  Продолжить: aidevteam refine {draft.id}")
            return 0
        message = answer.strip() or "на усмотрение команды"


def cmd_refine(root: Path, args: list[str], ui: UI) -> int:
    if not args:
        print("  Использование: aidevteam refine <D-ID>")
        return 1
    for d in taskmod.load_drafts(root):
        if d.id == args[0]:
            return dialog(root, ui, d)
    print(f"  ✘ Черновик {args[0]} не найден (aidevteam list tasks)")
    return 1


def cmd_add_interactive(root: Path, args: list[str], ui: UI) -> int:
    try:
        title = input("Коротко: что хотите добавить? > ").strip()
    except (KeyboardInterrupt, EOFError):
        return 0
    if not title:
        return 0
    draft = taskmod.create_draft(root, title, "")
    return dialog(root, ui, draft)

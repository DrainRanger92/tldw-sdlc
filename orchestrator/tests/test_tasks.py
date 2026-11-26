from aidevteam.tasks import (
    create_draft,
    lint,
    load_drafts,
    load_tasks,
    mark_done,
    pending_current,
)


def test_lint_clean_seed(repo):
    assert lint(repo) == []


def test_lint_unknown_dependency(repo):
    f = repo / "specs" / "001-mvp" / "tasks.md"
    f.write_text(
        f.read_text(encoding="utf-8").replace("- зависит: T-001", "- зависит: T-099"),
        encoding="utf-8",
    )
    problems = lint(repo)
    assert any("T-099 не найдена" in p for p in problems)


def test_lint_broken_format(repo):
    (repo / "specs" / "002-x").mkdir()
    (repo / "specs" / "002-x" / "tasks.md").write_text("просто текст\n", encoding="utf-8")
    problems = lint(repo)
    assert any("002-x" in p and "не распарсилась" in p for p in problems)


def test_move_to_current_cascades_earlier(repo):
    from aidevteam.tasks import move_task

    moved = move_task(repo, "T-003", "current")
    assert [t.id for t in moved] == ["T-003"]
    tasks = load_tasks(repo)
    assert [t.id for t in pending_current(tasks)] == ["T-001", "T-002", "T-003"]
    assert lint(repo) == []


def test_move_to_backlog_cascades_later(repo):
    from aidevteam.tasks import move_task

    moved = move_task(repo, "T-001", "backlog")
    assert [t.id for t in moved] == ["T-001", "T-002"]
    tasks = load_tasks(repo)
    assert pending_current(tasks) == []
    # порядок сохранён: T-001, T-002 встали в начало свободных, перед T-003
    backlog = [t.id for t in tasks if t.bucket == "backlog"]
    assert backlog == ["T-001", "T-002", "T-003"]
    assert lint(repo) == []


def test_move_task_unknown_bucket(repo):
    import pytest

    from aidevteam.tasks import move_task

    with pytest.raises(ValueError, match="неизвестна"):
        move_task(repo, "T-001", "sprint9")


def test_move_task_same_bucket_is_noop(repo):
    import pytest

    from aidevteam.tasks import move_task

    before = (repo / "specs" / "001-mvp" / "tasks.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="уже в секции"):
        move_task(repo, "T-003", "backlog")
    assert (repo / "specs" / "001-mvp" / "tasks.md").read_text(encoding="utf-8") == before


def test_pull_first_when_all_in_backlog(repo):
    from aidevteam.tasks import move_task, pull_from_backlog

    move_task(repo, "T-001", "backlog")  # каскад: всё в backlog
    assert pending_current(load_tasks(repo)) == []
    pulled = pull_from_backlog(repo)
    assert pulled is not None and pulled.id == "T-001" and pulled.bucket == "current"
    assert lint(repo) == []


def test_pull_from_backlog(repo):
    from aidevteam.tasks import pull_from_backlog

    f = repo / "specs" / "001-mvp" / "tasks.md"
    f.write_text(
        f.read_text(encoding="utf-8")
        + "\n### T-004: Из пула\n- тип: app\n"
          "- статус: pending\n- зависит: T-001\n\n**Описание**\nx\n\n"
          "**Критерии приёмки**\n- [ ] x\n",
        encoding="utf-8",
    )
    # пока есть pending в current — из пула не берём
    assert pull_from_backlog(repo) is None
    for t in [t for t in load_tasks(repo) if t.bucket == "current"]:
        mark_done(t)
    # T-003 первым в пуле, зависит от T-002 (done) → возьмётся раньше T-004?
    # нет: T-003 тоже done ниже? — помечаем и его, чтобы проверить именно пул
    for t in [t for t in load_tasks(repo) if t.id == "T-003"]:
        mark_done(t)
    pulled = pull_from_backlog(repo)
    assert pulled is not None and pulled.id == "T-004" and pulled.bucket == "current"
    assert lint(repo) == []


def test_pull_skips_blocked_dependencies(repo):
    from aidevteam.tasks import pull_from_backlog

    f = repo / "specs" / "001-mvp" / "tasks.md"
    text = f.read_text(encoding="utf-8") + (
        "\n### T-004: Заблокирована\n- тип: app\n"
        "- статус: pending\n- зависит: T-009\n\n**Описание**\nx\n\n"
        "**Критерии приёмки**\n- [ ] x\n"
    )
    # T-003 зависит от невыполненной T-004, T-004 — от несуществующей T-009:
    # обе задачи пула заблокированы, взять нечего
    text = text.replace("- зависит: T-002", "- зависит: T-004")
    f.write_text(text, encoding="utf-8")
    for t in [t for t in load_tasks(repo) if t.bucket == "current"]:
        mark_done(t)
    assert pull_from_backlog(repo) is None


def test_reorder_within_bucket(repo):
    from aidevteam.tasks import move_task, reorder_task

    move_task(repo, "T-003", "current")
    moved = reorder_task(repo, "T-003", "after", "T-001")
    assert moved.bucket == "current"
    ids = [t.id for t in load_tasks(repo)]
    assert ids == ["T-001", "T-003", "T-002"]
    # зависимость T-003 → T-002 теперь нарушена по порядку — линт это видит
    assert any("стоит позже" in p for p in lint(repo))


def test_reorder_adopts_anchor_bucket(repo):
    from aidevteam.tasks import reorder_task

    moved = reorder_task(repo, "T-002", "after", "T-003")
    assert moved.bucket == "backlog"
    ids = [(t.id, t.bucket) for t in load_tasks(repo)]
    assert ids == [("T-001", "current"), ("T-003", "backlog"), ("T-002", "backlog")]


def test_lint_forward_dependency(repo):
    f = repo / "specs" / "001-mvp" / "tasks.md"
    f.write_text(
        f.read_text(encoding="utf-8").replace("- зависит: T-001", "- зависит: T-003"),
        encoding="utf-8",
    )
    problems = lint(repo)
    assert any("более поздней секции" in p or "стоит позже" in p for p in problems)


def test_parse_tasks(repo):
    tasks = load_tasks(repo)
    assert [t.id for t in tasks] == ["T-001", "T-002", "T-003"]
    t1 = tasks[0]
    assert t1.type == "app" and t1.status == "pending" and t1.bucket == "current"
    assert t1.depends == []
    assert tasks[1].depends == ["T-001"]
    assert "GET /health отвечает 200" in t1.acceptance[0]
    assert t1.spec == "001-mvp"


def test_pending_current(repo):
    tasks = load_tasks(repo)
    inc = pending_current(tasks)
    assert [t.id for t in inc] == ["T-001", "T-002"]


def test_mark_done(repo):
    tasks = load_tasks(repo)
    mark_done(tasks[0])
    tasks = load_tasks(repo)
    assert tasks[0].status == "done"
    assert [t.id for t in pending_current(tasks)] == ["T-002"]


def test_create_and_load_draft(repo):
    d = create_draft(repo, "Вход по паролю", "Нужен login/password")
    assert d.id == "D-001" and d.path.exists()
    drafts = load_drafts(repo)
    assert drafts[0].title == "Вход по паролю"
    assert drafts[0].open_questions == []


def test_draft_open_questions(repo):
    d = create_draft(repo, "Фича", "")
    d.path.write_text(
        d.path.read_text(encoding="utf-8")
        + "\n## Открытые вопросы\n- [ ] один или много пользователей?\n",
        encoding="utf-8",
    )
    assert load_drafts(repo)[0].open_questions == ["один или много пользователей?"]


def test_story_draft_lifecycle(repo):
    from aidevteam.tasks import draft_ready

    d = create_draft(repo, "Как пользователь, я хочу экспорт", "чтобы делать бэкапы", kind="story")
    loaded = load_drafts(repo)[0]
    assert loaded.kind == "story"
    assert loaded.ki == []
    assert not draft_ready(loaded)  # story без КИ не готова
    d.path.write_text(
        d.path.read_text(encoding="utf-8").replace(
            "(пока пусто — соберите через aidevteam refine)",
            "- [ ] кнопка «Экспорт» в UI\n- [x] GET /api/export отдаёт markdown",
        ),
        encoding="utf-8",
    )
    loaded = load_drafts(repo)[0]
    assert len(loaded.ki) == 2
    assert draft_ready(loaded)  # КИ есть, вопросов нет → готова


def test_ki_checkboxes_are_not_open_questions(repo):
    d = create_draft(repo, "История", "описание", kind="story")
    d.path.write_text(
        d.path.read_text(encoding="utf-8")
        + "\n## Открытые вопросы\n- [ ] какой формат экспорта?\n",
        encoding="utf-8",
    )
    loaded = load_drafts(repo)[0]
    assert loaded.open_questions == ["какой формат экспорта?"]


def test_task_size_parsed_and_linted(repo):
    f = repo / "specs" / "001-mvp" / "tasks.md"
    f.write_text(
        f.read_text(encoding="utf-8").replace(
            "### T-001: Каркас приложения\n- тип: app",
            "### T-001: Каркас приложения\n- тип: app\n- размер: XL",
        ),
        encoding="utf-8",
    )
    # XL — неизвестный размер
    assert any("XL" in p for p in lint(repo))


def test_lint_scaffold_without_tasks_is_valid(repo):
    d = repo / "specs" / "003-scaffold"
    d.mkdir()
    (d / "tasks.md").write_text(
        "# Задачи — каркас\n\n## Текущий инкремент\n\n## Бэклог\n", encoding="utf-8"
    )
    assert lint(repo) == []


def test_lint_task_like_heading_without_format(repo):
    d = repo / "specs" / "004-bad"
    d.mkdir()
    (d / "tasks.md").write_text(
        "# Задачи\n\n## Текущий инкремент\n\n### Каркас приложения\n- тип: app\n", encoding="utf-8"
    )
    problems = lint(repo)
    assert any("004-bad" in p and "не распарсились" in p for p in problems)


def test_lint_ignores_commented_task_example(repo):
    f = repo / "specs" / "001-mvp" / "tasks.md"
    f.write_text(
        "# Задачи\n\n<!--\n### T-009: пример формата\n- тип: app\n-->\n\n"
        "## Текущий инкремент\n\n## Бэклог\n",
        encoding="utf-8",
    )
    assert lint(repo) == []
    assert load_tasks(repo) == []

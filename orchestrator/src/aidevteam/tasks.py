"""Контракты Spec Kit: tasks.md (Текущий инкремент / Свободные задания) и inbox.

Модель — «ватерлиния»: задачи спеки образуют один упорядоченный список;
секция «Текущий инкремент» — его верхняя часть (в работе), «Бэклог» —
остальное. `move` двигает границу каскадно: перенос T-NNN в
backlog утаскивает и все pending-задачи после неё; перенос в current —
все pending-задачи до неё. Выполненные задачи не перемещаются.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_TRANSLIT = str.maketrans(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя ",
    "abvgdeejziyklmnoprstufhccss_y_eua-",
)

# заголовок секции → корзина; «Следующий инкремент» — legacy-синоним backlog
BUCKETS = {
    "Текущий инкремент": "current",
    "Бэклог": "backlog",
    # legacy-синонимы старых форматов
    "Свободные задания": "backlog",
    "Следующий инкремент": "backlog",
}
BUCKET_TITLES = {"current": "Текущий инкремент", "backlog": "Бэклог"}
_BUCKET_ORDER = {"current": 0, "backlog": 1}

_SECTION_RE = re.compile(
    r"^## (Текущий инкремент|Бэклог|Свободные задания|Следующий инкремент)\s*$"
)
_TASK_RE = re.compile(r"^### (T-\d+): (.+)$")
_META_RE = re.compile(r"^- (тип|статус|зависит|размер): (.+)$")
_ACC_RE = re.compile(r"^- \[[ x]\] (.+)$")


@dataclass
class Task:
    id: str
    title: str
    type: str
    status: str
    bucket: str  # current | backlog
    depends: list[str]
    description: str
    acceptance: list[str]
    spec: str
    path: Path
    size: str = "M"  # S | M | L — оценка планировщика


@dataclass
class Draft:
    id: str
    title: str
    status: str
    path: Path
    description: str
    open_questions: list[str]
    kind: str = "task"  # task | story
    ki: list[str] = None  # критерии исполнения (для story)


def load_tasks(root: Path) -> list[Task]:
    tasks: list[Task] = []
    for f in sorted((root / "specs").glob("*/tasks.md")):
        spec = f.parent.name
        bucket = "backlog"
        cur: Task | None = None
        section = ""
        # комментарии описывают формат — это не задачи
        text = re.sub(r"<!--.*?-->", "", f.read_text(encoding="utf-8"), flags=re.S)
        for line in text.splitlines():
            if m := _SECTION_RE.match(line):
                bucket = BUCKETS[m.group(1)]
            elif m := _TASK_RE.match(line):
                cur = Task(m.group(1), m.group(2).strip(), "app", "pending",
                           bucket, [], "", [], spec, f)
                tasks.append(cur)
                section = ""
            elif cur is not None:
                if m := _META_RE.match(line):
                    key, val = m.group(1), m.group(2).strip()
                    if key == "тип":
                        cur.type = val
                    elif key == "статус":
                        cur.status = val
                    elif key == "зависит" and val != "—":
                        cur.depends = [x.strip() for x in val.split(",")]
                    elif key == "размер":
                        cur.size = val
                elif line.startswith("**Описание**"):
                    section = "desc"
                elif line.startswith("**Критерии приёмки**"):
                    section = "acc"
                elif section == "acc" and (m := _ACC_RE.match(line)):
                    cur.acceptance.append(m.group(1).strip())
                elif section == "desc" and line.strip():
                    cur.description += line.strip() + " "
    return tasks


def lint(root: Path) -> list[str]:
    """Линт артефактов: формат tasks.md, дубли id, зависимости, порядок."""
    problems: list[str] = []
    tasks = load_tasks(root)
    by_spec: dict[str, list[Task]] = {}
    for t in tasks:
        by_spec.setdefault(t.spec, []).append(t)
    for f in sorted((root / "specs").glob("*/tasks.md")):
        text = f.read_text(encoding="utf-8")
        # HTML-комментарии — документация формата, а не артефакты
        lines = re.sub(r"<!--.*?-->", "", text, flags=re.S).splitlines()
        has_sections = any(_SECTION_RE.match(line) for line in lines)
        rel = f.relative_to(root)
        if f.parent.name in by_spec:
            if not has_sections:
                problems.append(f"{rel}: нет секций «Текущий инкремент» / «Бэклог»")
            continue
        if not has_sections:
            problems.append(f"{rel}: не распарсилась ни одна задача (формат '### T-NNN: …'?)")
        elif any(line.startswith("### ") for line in lines):
            problems.append(f"{rel}: задачи не распарсились (формат '### T-NNN: …'?)")
        # каркас без задач (секции есть, заголовков задач нет) — валиден:
        # задачи появятся после ANALYZE/PLAN первого цикла.
    for spec, ts in by_spec.items():
        seen: set[str] = set()
        order = {t.id: i for i, t in enumerate(ts)}
        by_id = {t.id: t for t in ts}
        for i, t in enumerate(ts):
            if t.id in seen:
                problems.append(f"{spec}/{t.id}: дублирующийся id")
            seen.add(t.id)
            if t.status not in ("pending", "done"):
                problems.append(f"{spec}/{t.id}: неизвестный статус '{t.status}'")
            if t.type not in ("app", "sdlc"):
                problems.append(f"{spec}/{t.id}: неизвестный тип '{t.type}'")
            if t.size not in ("S", "M", "L"):
                problems.append(f"{spec}/{t.id}: неизвестный размер '{t.size}' (S|M|L)")
            for dep in t.depends:
                if dep not in order:
                    problems.append(f"{spec}/{t.id}: зависимость {dep} не найдена")
                    continue
                d = by_id[dep]
                if d.status == "done":
                    continue
                if _BUCKET_ORDER[d.bucket] > _BUCKET_ORDER[t.bucket]:
                    problems.append(
                        f"{spec}/{t.id} ({t.bucket}): зависит от {dep} из более поздней секции ({d.bucket})"
                    )
                elif d.bucket == t.bucket and order[dep] > i:
                    problems.append(
                        f"{spec}/{t.id}: зависит от {dep}, который стоит позже в файле"
                    )
    return problems


def pending_current(tasks: list[Task]) -> list[Task]:
    """Невыполненные задачи текущего инкремента (первой спеки, где они есть)."""
    pending = [t for t in tasks if t.status == "pending" and t.bucket == "current"]
    if not pending:
        return []
    first_spec = pending[0].spec
    return [t for t in pending if t.spec == first_spec]


# ---------------------------------------------------------------------------
# структурная работа с файлом задач

@dataclass
class _Section:
    bucket: str
    heading: str
    free: list[str] = field(default_factory=list)
    blocks: list[tuple[str, list[str]]] = field(default_factory=list)  # (task_id, lines)


def _parse_file(lines: list[str]) -> tuple[list[str], list[_Section]]:
    prelude: list[str] = []
    sections: list[_Section] = []
    cur_block: list[str] | None = None

    def close_block() -> None:
        nonlocal cur_block
        if cur_block is not None and sections:
            while cur_block and not cur_block[-1].strip():
                cur_block.pop()
            m = _TASK_RE.match(cur_block[0])
            sections[-1].blocks.append((m.group(1), cur_block))
        cur_block = None

    for line in lines:
        if m := _SECTION_RE.match(line):
            close_block()
            sections.append(_Section(BUCKETS[m.group(1)], line))
        elif _TASK_RE.match(line):
            close_block()
            if sections:
                cur_block = [line]
            else:
                prelude.append(line)
        elif cur_block is not None:
            cur_block.append(line)
        elif sections:
            if line.strip():
                sections[-1].free.append(line)
        else:
            prelude.append(line)
    close_block()
    return prelude, sections


def _serialize(prelude: list[str], sections: list[_Section]) -> str:
    out: list[str] = [l for l in prelude]
    while out and not out[-1].strip():
        out.pop()
    for s in sections:
        out += ["", s.heading]
        if s.free:
            out += [""] + s.free
        for _, block in s.blocks:
            out += [""] + block
    return "\n".join(out).rstrip() + "\n"


def move_task(root: Path, task_id: str, bucket: str) -> list[Task]:
    """Каскадный перенос: двигает «ватерлинию» между current и backlog.

    → backlog: задача и все pending-задачи ПОСЛЕ неё в current уходят в
      начало backlog. → current: задача и все pending-задачи ДО неё в
      backlog уходят в конец current. Выполненные задачи не перемещаются.
    Возвращает список перенесённых задач.
    """
    if bucket not in BUCKET_TITLES:
        raise ValueError(f"секция '{bucket}' неизвестна (current|backlog)")
    tasks = load_tasks(root)
    task = next((t for t in tasks if t.id == task_id), None)
    if task is None:
        raise ValueError(f"задача {task_id} не найдена")
    if task.bucket == bucket:
        raise ValueError(
            f"{task_id} уже в секции «{BUCKET_TITLES[bucket]}» — переносить нечего"
        )
    status = {t.id: t.status for t in tasks if t.spec == task.spec}
    lines = task.path.read_text(encoding="utf-8").splitlines()
    prelude, sections = _parse_file(lines)
    src_name = "current" if bucket == "backlog" else "backlog"
    src = next((s for s in sections if s.bucket == src_name), None)
    dst = next((s for s in sections if s.bucket == bucket), None)
    if src is None or dst is None:
        missing = BUCKET_TITLES[bucket if dst is None else src_name]
        raise ValueError(f"в {task.spec}/tasks.md нет секции «{missing}»")
    ids = [bid for bid, _ in src.blocks]
    pos = ids.index(task_id)
    if bucket == "backlog":  # задача и pending-задачи после неё
        chosen = [bid for bid in ids[pos:] if status.get(bid) == "pending"]
    else:  # pending-задачи до неё включительно
        chosen = [bid for bid in ids[: pos + 1] if status.get(bid) == "pending"]
    moved_blocks = [b for b in src.blocks if b[0] in chosen]
    src.blocks = [b for b in src.blocks if b[0] not in chosen]
    if bucket == "backlog":
        dst.blocks = moved_blocks + dst.blocks
    else:
        dst.blocks = dst.blocks + moved_blocks
    task.path.write_text(_serialize(prelude, sections), encoding="utf-8")
    after = {t.id: t for t in load_tasks(root)}
    return [after[bid] for bid in chosen]


def reorder_task(root: Path, task_id: str, where: str, anchor_id: str) -> Task:
    """Поставить задачу before/after другой. Корзина наследуется от якоря."""
    if where not in ("before", "after"):
        raise ValueError("позиция должна быть before или after")
    if task_id == anchor_id:
        raise ValueError("задача и якорь совпадают")
    tasks = load_tasks(root)
    task = next((t for t in tasks if t.id == task_id), None)
    anchor = next((t for t in tasks if t.id == anchor_id), None)
    if task is None:
        raise ValueError(f"задача {task_id} не найдена")
    if anchor is None:
        raise ValueError(f"якорь {anchor_id} не найден")
    if anchor.spec != task.spec:
        raise ValueError("переупорядочивание возможно только внутри одной спеки")
    lines = task.path.read_text(encoding="utf-8").splitlines()
    prelude, sections = _parse_file(lines)
    block = None
    for s in sections:
        for b in s.blocks:
            if b[0] == task_id:
                block = b
        s.blocks = [b for b in s.blocks if b[0] != task_id]
    for s in sections:
        ids = [bid for bid, _ in s.blocks]
        if anchor_id in ids:
            pos = ids.index(anchor_id) + (1 if where == "after" else 0)
            s.blocks.insert(pos, block)
            break
    task.path.write_text(_serialize(prelude, sections), encoding="utf-8")
    return next(t for t in load_tasks(root) if t.id == task_id)


def pull_from_backlog(root: Path) -> Task | None:
    """Взять из свободных заданий первую задачу с выполненными зависимостями."""
    tasks = load_tasks(root)
    if pending_current(tasks):
        return None
    by_id = {(t.spec, t.id): t for t in tasks}
    for t in tasks:
        if t.status != "pending" or t.bucket != "backlog":
            continue
        deps_done = all(
            (dep := by_id.get((t.spec, d))) is not None and dep.status == "done"
            for d in t.depends
        )
        if deps_done:
            return move_task(root, t.id, "current")[0]
    return None


def mark_done(task: Task) -> None:
    lines = task.path.read_text(encoding="utf-8").splitlines()
    inside = False
    for i, line in enumerate(lines):
        if _TASK_RE.match(line):
            inside = line.startswith(f"### {task.id}:")
        elif inside and line.startswith("- статус:"):
            lines[i] = "- статус: done"
            break
    task.path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _inbox(root: Path) -> Path:
    d = root / "specs" / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def next_draft_id(root: Path) -> str:
    nums = [
        int(m.group(1))
        for f in _inbox(root).glob("D-*.md")
        if (m := re.match(r"D-(\d+)", f.name))
    ]
    return f"D-{max(nums, default=0) + 1:03d}"


def create_draft(root: Path, title: str, desc: str = "", kind: str = "task") -> Draft:
    did = next_draft_id(root)
    slug = title.lower().translate(_TRANSLIT)
    slug = re.sub(r"[^a-z0-9-]", "", slug)[:40] or "draft"
    path = _inbox(root) / f"{did}-{slug}.md"
    body = (
        f"---\nid: {did}\ntitle: {title}\nstatus: draft\nkind: {kind}\n---\n\n"
        f"## Описание\n{desc or '(пусто)'}\n"
    )
    if kind == "story":
        body += "\n## Критерии исполнения\n(пока пусто — соберите через aidevteam refine)\n"
    path.write_text(body, encoding="utf-8")
    return Draft(did, title, "draft", path, desc, [], kind, [])


def _section_body(text: str, title: str) -> str:
    m = re.search(rf"## {title}\n(.*?)(?=\n## |\Z)", text, re.S)
    return m.group(1) if m else ""


def load_drafts(root: Path) -> list[Draft]:
    drafts = []
    for f in sorted(_inbox(root).glob("D-*.md")):
        text = f.read_text(encoding="utf-8")
        meta = dict(re.findall(r"^(id|title|status|kind): (.+)$", text, re.M))
        desc = _section_body(text, "Описание").strip()
        # открытые вопросы — ТОЛЬКО из своей секции (КИ тоже используют чекбоксы)
        questions = [
            m.group(1).strip()
            for m in re.finditer(r"^- \[ \] (.+)$", _section_body(text, "Открытые вопросы"), re.M)
        ]
        ki = [
            m.group(1).strip()
            for m in re.finditer(r"^- \[[ x]\] (.+)$", _section_body(text, "Критерии исполнения"), re.M)
        ]
        drafts.append(
            Draft(
                meta.get("id", f.stem), meta.get("title", f.stem),
                meta.get("status", "draft"), f, desc, questions,
                meta.get("kind", "task"), ki,
            )
        )
    return drafts


def draft_ready(d: Draft) -> bool:
    """Готов ли черновик к оформлению в задачи (plan/ANALYZE)."""
    if d.open_questions or d.description in ("", "(пусто)"):
        return False
    if d.kind == "story" and not d.ki:
        return False
    return True

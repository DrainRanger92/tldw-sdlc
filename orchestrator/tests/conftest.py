import subprocess
from pathlib import Path

import pytest


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


TASKS_MD = """# Задачи — Записная книжка, MVP

## Текущий инкремент

### T-001: Каркас приложения
- тип: app
- статус: pending
- зависит: —

**Описание**
Каркас Express + TypeScript, эндпоинт GET /health.

**Критерии приёмки**
- [ ] GET /health отвечает 200

### T-002: Модель заметки и хранилище
- тип: app
- статус: pending
- зависит: T-001

**Описание**
JSON-хранилище заметок, слой repository.

**Критерии приёмки**
- [ ] CRUD функций хранилища покрыт тестами

## Бэклог

### T-003: Поиск по заметкам
- тип: app
- статус: pending
- зависит: T-002

**Описание**
Поиск по тексту и тегам.

**Критерии приёмки**
- [ ] GET /api/notes?q= фильтрует результат
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "sdlc"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.com")
    (root / "specs" / "inbox").mkdir(parents=True)
    (root / "specs" / "001-mvp").mkdir()
    (root / "app" / "memory" / "facts").mkdir(parents=True)
    (root / "app" / "memory" / "decisions").mkdir()
    (root / "app" / "skills").mkdir()
    (root / "specs" / "001-mvp" / "tasks.md").write_text(TASKS_MD, encoding="utf-8")
    (root / "specs" / "product.md").write_text("# Записная книжка\n", encoding="utf-8")
    (root / "app" / "memory" / "MEMORY.md").write_text("# Память\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-m", "seed")
    return root

from __future__ import annotations

import os
import time
from pathlib import Path

from aidevteam import gitops
from aidevteam import tasks as taskmod
from aidevteam.config import Config
from aidevteam.state import State, read_failure
from aidevteam.ui import UI


def _lock_file(root: Path) -> Path:
    d = root / ".aidevteam"
    d.mkdir(exist_ok=True)
    return d / "lock"


def acquire_lock(root: Path) -> bool:
    f = _lock_file(root)
    if f.exists():
        try:
            pid = int(f.read_text())
            os.kill(pid, 0)
            return False  # процесс жив
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # протухший lock
    f.write_text(str(os.getpid()))
    return True


def release_lock(root: Path) -> None:
    _lock_file(root).unlink(missing_ok=True)


def _pause_seconds(worked: bool, interval: int) -> float:
    """Пауза между итерациями: пока есть работа, циклы идут подряд."""
    return 0.0 if worked else float(interval)


def has_work(root: Path) -> bool:
    st = State.load(root)
    tasks = taskmod.load_tasks(root)
    if any(t.status == "pending" for t in tasks):
        return True
    if [d for d in taskmod.load_drafts(root) if taskmod.draft_ready(d)]:
        return True
    if read_failure(root) or gitops.commits_since(root, st.last_processed_commit):
        return True
    return False


def cmd_watch(root: Path, args: list[str], ui: UI) -> int:
    from aidevteam.cycle import run_cycle

    try:
        cfg = Config.load(root)
        cfg.validate()
    except ValueError as exc:
        print(f"  ✘ {exc}")
        return 1
    if not acquire_lock(root):
        print("  ✘ Уже запущен другой цикл/наблюдение (файл .aidevteam/lock)")
        return 1
    try:
        while True:
            gitops.run_git(root, "fetch", "--quiet", check=False)
            worked = False
            if has_work(root):
                worked = True
                code = run_cycle(root, cfg, ui)
                if code == 2:
                    worked = False
                    ui.plain("  ⚠ цикл упал — наблюдение продолжается, отчёт в .aidevteam/last-failure.md")
            else:
                ui.plain(
                    f"  жду изменений… следующая проверка через {cfg.watch_interval_sec // 60} мин (Ctrl+C — выход)"
                )
            if "--once" in args:
                return 0
            time.sleep(_pause_seconds(worked, cfg.watch_interval_sec))
    except KeyboardInterrupt:
        ui.plain("\n  Наблюдение остановлено.")
        return 0
    finally:
        release_lock(root)

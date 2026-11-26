from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from aidevteam.config import Config
from aidevteam.gates import app_python, is_python_app
from aidevteam.ui import UI

PY_ENTRYPOINTS = ("main.py", "__main__.py", "bot.py")


def _py_entrypoint(app: Path) -> Path | None:
    for name in PY_ENTRYPOINTS:
        candidate = app / name
        if candidate.exists():
            return candidate
    return None


def cmd_app(root: Path, args: list[str], ui: UI) -> int:
    sub = args[0] if args else "dev"
    app = root / "app"
    cfg = Config.load(root)
    pidfile = root / ".aidevteam" / "app.pid"
    is_npm = (app / "package.json").exists()
    is_py = is_python_app(app)
    if not is_npm and not is_py:
        print("  ✘ Приложения ещё нет — выполните aidevteam run")
        return 1
    if sub == "dev":
        if is_npm:
            proc = subprocess.Popen(
                ["npm", "run", "dev"], cwd=app,
                env={**os.environ, "APP_PORT": str(cfg.app_port)},
            )
            where = f"→ http://localhost:{cfg.app_port}"
        else:
            entry = _py_entrypoint(app)
            if entry is None:
                print(f"  ✘ У Python-приложения нет точки входа ({', '.join(PY_ENTRYPOINTS)})")
                return 1
            proc = subprocess.Popen(
                [app_python(app), str(entry)], cwd=app,
                env={**os.environ, "APP_PORT": str(cfg.app_port)},
            )
            where = f"({entry.name})"
        pidfile.parent.mkdir(exist_ok=True)
        pidfile.write_text(str(proc.pid))
        ui.plain(f"  ✔ приложение запущено (pid {proc.pid}) {where}")
        ui.plain("    остановить: aidevteam app stop")
        proc.wait()
        return proc.returncode or 0
    if sub == "test":
        if is_npm:
            return subprocess.run(["npm", "test"], cwd=app).returncode
        tests = sorted(app.rglob("test_*.py"))
        if not tests:
            print("  ─ тестов в app/ ещё нет")
            return 0
        return subprocess.run(
            [app_python(app), "-m", "pytest", "-q",
             *[str(t.relative_to(app)) for t in tests]],
            cwd=app,
        ).returncode
    if sub == "stop":
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGTERM)
                ui.plain("  ✔ приложение остановлено")
            except (ValueError, ProcessLookupError):
                ui.plain("  ─ процесс уже не работает")
            pidfile.unlink()
            return 0
        print("  ─ приложение не запущено")
        return 0
    print("  Использование: aidevteam app dev|test|stop")
    return 1

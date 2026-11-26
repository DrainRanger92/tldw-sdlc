from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from aidevteam.config import Config


@dataclass
class GateResult:
    name: str
    ok: bool
    detail: str = ""


def check_protected(changed: list[str], task_types: set[str], cfg: Config) -> GateResult:
    if "sdlc" in task_types:
        return GateResult("защита путей", True, "sdlc-задача: изменения SDLC разрешены")
    hits = [
        p for p in changed
        if any(p == prot.rstrip("/") or p.startswith(prot) for prot in cfg.protected_paths)
    ]
    if hits:
        return GateResult("защита путей", False, f"задеты защищённые пути: {', '.join(hits[:5])}")
    return GateResult("защита путей", True, "дифф не трогает защищённые пути")


def check_learn_paths(changed: list[str]) -> GateResult:
    bad = [
        p for p in changed
        if not (
            (p.startswith("app/memory/") or p.startswith("app/skills/"))
            and p.endswith(".md")
        )
    ]
    if bad:
        return GateResult(
            "LEARN-пути", False,
            f"LEARN может писать только markdown в app/memory|skills: {bad[:3]}",
        )
    return GateResult("LEARN-пути", True)


def run_command(name: str, cmd: list[str], cwd: Path, timeout: int) -> GateResult:
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GateResult(name, False, str(exc)[:200])
    detail = (res.stdout + res.stderr).strip()[-300:]
    return GateResult(name, res.returncode == 0, detail)


def npm_gates(app_dir: Path, timeout: int) -> list[GateResult]:
    pkg = app_dir / "package.json"
    if not pkg.exists():
        return [GateResult("гейты приложения", True, "приложения ещё нет")]
    scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
    results = []
    if "test" in scripts:
        results.append(run_command("npm test", ["npm", "test", "--silent"], app_dir, timeout))
    if "lint" in scripts:
        results.append(run_command("npm run lint", ["npm", "run", "lint", "--silent"], app_dir, timeout))
    return results or [GateResult("гейты приложения", True, "нет скриптов test/lint")]


PYTHON_MARKERS = ("pyproject.toml", "setup.py", "requirements.txt")


def is_python_app(app_dir: Path) -> bool:
    if not app_dir.is_dir():
        return False
    if any((app_dir / marker).exists() for marker in PYTHON_MARKERS):
        return True
    return any(
        p.suffix == ".py" and ".venv" not in p.parts and "site-packages" not in p.parts
        for p in app_dir.rglob("*.py")
    )


def app_python(app_dir: Path) -> str:
    """Интерпретатор приложения: его собственный venv, если он здесь запускается."""
    for rel in (".venv/bin/python", "venv/bin/python"):
        candidate = app_dir / rel
        if candidate.exists() and _interpreter_runs(candidate):
            return str(candidate)
    return sys.executable


def _interpreter_runs(python: Path) -> bool:
    """venv, собранный на другой ОС (macOS-venv в Linux-контейнере), не запускается."""
    try:
        res = subprocess.run(
            [str(python), "-c", "import sys"], capture_output=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def _python_tests(app_dir: Path) -> list[str]:
    found = sorted(
        p for p in app_dir.rglob("test_*.py")
        if ".venv" not in p.parts and "site-packages" not in p.parts
    )
    return [str(p.relative_to(app_dir)) for p in found]


def _ruff_configured(app_dir: Path) -> bool:
    if (app_dir / "ruff.toml").exists() or (app_dir / ".ruff.toml").exists():
        return True
    pyproject = app_dir / "pyproject.toml"
    return pyproject.exists() and "[tool.ruff" in pyproject.read_text(encoding="utf-8")


def python_gates(app_dir: Path, timeout: int) -> list[GateResult]:
    """pytest по найденным тестам и ruff, если он настроен в приложении."""
    python = app_python(app_dir)
    results: list[GateResult] = []
    tests = _python_tests(app_dir)
    if tests:
        results.append(
            run_command("pytest", [python, "-m", "pytest", "-q", *tests], app_dir, timeout)
        )
    if _ruff_configured(app_dir):
        results.append(
            run_command("ruff check", [python, "-m", "ruff", "check", "."], app_dir, timeout)
        )
    return results or [GateResult("гейты приложения", True, "тестов и линтера нет")]


def app_gates(app_dir: Path, timeout: int) -> list[GateResult]:
    """Гейты приложения по его стеку: npm (package.json) или Python."""
    if (app_dir / "package.json").exists():
        return npm_gates(app_dir, timeout)
    if is_python_app(app_dir):
        return python_gates(app_dir, timeout)
    return [GateResult("гейты приложения", True, "приложения ещё нет")]


def smoke_python(app_dir: Path, timeout: int = 60) -> GateResult:
    """Smoke без токенов и сети: синтаксическая сборка всех .py приложения."""
    return run_command(
        "smoke: compileall",
        [app_python(app_dir), "-m", "compileall", "-q", "."],
        app_dir, timeout,
    )


def app_smoke(app_dir: Path, timeout: int = 60) -> GateResult:
    """Smoke по стеку: HTTP /health у npm-приложения или compileall у Python."""
    if (app_dir / "package.json").exists():
        return smoke_http(app_dir, timeout)
    if is_python_app(app_dir):
        return smoke_python(app_dir, timeout)
    return GateResult("smoke", True, "приложения ещё нет")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def smoke_http(app_dir: Path, timeout: int = 60) -> GateResult:
    """Поднять `npm run dev` на свободном порту и дождаться GET /health → 200."""
    pkg = app_dir / "package.json"
    if not pkg.exists():
        return GateResult("smoke", True, "приложения ещё нет")
    scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
    if "dev" not in scripts:
        return GateResult("smoke", True, "нет скрипта dev")
    port = _free_port()
    proc = subprocess.Popen(
        ["npm", "run", "dev"], cwd=app_dir,
        env={**os.environ, "APP_PORT": str(port)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return GateResult("smoke", False, "процесс dev завершился до готовности")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=2
                ) as r:
                    if r.status == 200:
                        return GateResult("smoke", True, f"/health → 200 (порт {port})")
            except (urllib.error.URLError, OSError):
                time.sleep(1)
        return GateResult("smoke", False, f"/health не ответил 200 за {timeout}s")
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


def sdlc_gates(root: Path, timeout: int) -> list[GateResult]:
    """Усиленные гейты для sdlc:-задач — оркестратор должен остаться живым."""
    return [
        run_command(
            "тесты оркестратора",
            [sys.executable, "-m", "pytest", "-q", "tests"],
            root / "orchestrator", timeout,
        ),
        run_command(
            "smoke aidevteam --version",
            [sys.executable, "-m", "aidevteam", "--version"],
            root, 60,
        ),
    ]

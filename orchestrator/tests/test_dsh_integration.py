"""Интеграция с реальным DSH: только композиция профиля, без вызова модели."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from aidevteam import team
from aidevteam.config import Config

ROOT = Path(__file__).resolve().parents[2]
ROLES = ("analyst", "planner", "developer", "reviewer")


def _dsh_bin() -> str | None:
    env = os.environ.get("AIDEVTEAM_DSH_BIN")
    if env and Path(env).exists():
        return env
    found = shutil.which("dsh")
    if found:
        return found
    app = Path("/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh")
    return str(app) if app.exists() else None


DSH = _dsh_bin()
pytestmark = pytest.mark.skipif(
    DSH is None or os.environ.get("AIDEVTEAM_SKIP_REAL_DSH") == "1",
    reason="реальный dsh не установлен (AIDEVTEAM_SKIP_REAL_DSH=1 отключает тест)",
)


def test_real_dsh_roles_compose(monkeypatch):
    monkeypatch.setenv("AIDEVTEAM_DSH_BIN", DSH)
    cfg = Config.load(ROOT)
    if cfg.agents_backend != "dsh":
        pytest.skip("в aidevteam.toml выбран другой бэкенд")
    version = team.dsh_version(cfg, ROOT)
    assert version is not None
    if cfg.dsh_version:
        assert version == cfg.dsh_version
    for role in ROLES:
        assert team.dsh_composition_problems(ROOT, cfg, role) == [], role

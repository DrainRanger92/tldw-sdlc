from pathlib import Path

import pytest

from aidevteam.config import Config, parse_model_spec


def test_defaults_without_file(tmp_path: Path):
    cfg = Config.load(tmp_path)
    assert cfg.max_review_iterations == 2
    assert "orchestrator/" in cfg.protected_paths
    assert "dsh/" in cfg.protected_paths
    assert cfg.agents_backend == "opencode"


def test_load_overrides(tmp_path: Path):
    (tmp_path / "aidevteam.toml").write_text(
        "[cycle]\nmax_review_iterations = 5\n", encoding="utf-8"
    )
    assert Config.load(tmp_path).max_review_iterations == 5


def test_agents_section(tmp_path: Path):
    (tmp_path / "aidevteam.toml").write_text(
        """
[agents]
backend = "dsh"

[agents.models]
reviewer = "deepseek-official/deepseek-reasoner"

[agents.dsh]
bin = "/opt/dsh"
version = "0.2.2-rc.1"
profile = "headless"
permission_mode = "danger-full-access"
reviewer_permission_mode = "read-only"
approval_policy = "never"
roles_dir = "dsh/roles"
env_file = ".env.custom"
home = "/tmp/dsh-home"
""",
        encoding="utf-8",
    )
    cfg = Config.load(tmp_path)
    assert cfg.agents_backend == "dsh"
    assert cfg.agent_models["reviewer"] == "deepseek-official/deepseek-reasoner"
    assert cfg.dsh_bin == "/opt/dsh"
    assert cfg.dsh_version == "0.2.2-rc.1"
    assert cfg.dsh_reviewer_permission_mode == "read-only"
    assert cfg.dsh_roles_dir == "dsh/roles"
    assert cfg.dsh_env_file == ".env.custom"
    assert cfg.dsh_home == "/tmp/dsh-home"


def test_old_cycle_key_is_read(tmp_path: Path):
    (tmp_path / "aidevteam.toml").write_text(
        "[cycle]\nmax_opencode_calls = 7\n", encoding="utf-8"
    )
    cfg = Config.load(tmp_path)
    assert cfg.max_agent_calls == 7
    assert cfg.max_opencode_calls == 7


def test_new_cycle_key_wins(tmp_path: Path):
    (tmp_path / "aidevteam.toml").write_text(
        "[cycle]\nmax_agent_calls = 9\nmax_opencode_calls = 7\n", encoding="utf-8"
    )
    assert Config.load(tmp_path).max_agent_calls == 9


def test_env_backend_override(tmp_path: Path, monkeypatch):
    (tmp_path / "aidevteam.toml").write_text(
        '[agents]\nbackend = "opencode"\n', encoding="utf-8"
    )
    monkeypatch.setenv("AIDEVTEAM_AGENTS_BACKEND", "dsh")
    assert Config.load(tmp_path).agents_backend == "dsh"


def test_validate_rejects_unknown_backend(tmp_path: Path):
    (tmp_path / "aidevteam.toml").write_text(
        '[agents]\nbackend = "nope"\n', encoding="utf-8"
    )
    cfg = Config.load(tmp_path)
    try:
        cfg.validate()
    except ValueError as exc:
        assert "opencode или dsh" in str(exc)
    else:
        raise AssertionError("ожидалась ошибка валидации")


def test_validate_rejects_bad_model_spec(tmp_path: Path):
    (tmp_path / "aidevteam.toml").write_text(
        '[agents]\nbackend = "dsh"\n\n[agents.models]\nreviewer = "no-provider"\n',
        encoding="utf-8",
    )
    cfg = Config.load(tmp_path)
    try:
        cfg.validate()
    except ValueError as exc:
        assert "provider/model" in str(exc)
    else:
        raise AssertionError("ожидалась ошибка валидации модели")


def test_model_spec_with_reasoning_effort():
    assert parse_model_spec("deepseek-official/deepseek-flash@high") == (
        "deepseek-official", "deepseek-flash", "high",
    )
    assert parse_model_spec("deepseek-official/deepseek-flash") == (
        "deepseek-official", "deepseek-flash", None,
    )
    assert parse_model_spec(" deepseek-official / deepseek-flash @ high ") == (
        "deepseek-official", "deepseek-flash", "high",
    )


def test_model_spec_rejects_broken_values():
    for bad in ("no-provider", "provider/", "/model", "p/m@"):
        with pytest.raises(ValueError):
            parse_model_spec(bad)


def test_validate_checks_model_spec(tmp_path):
    (tmp_path / "aidevteam.toml").write_text(
        '[agents.models]\nanalyst = "p/m@"\n', encoding="utf-8"
    )
    cfg = Config.load(tmp_path)
    with pytest.raises(ValueError, match="agents.models.analyst"):
        cfg.validate()

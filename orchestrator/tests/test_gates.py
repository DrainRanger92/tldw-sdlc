import json

from aidevteam.config import Config
from aidevteam.gates import (
    app_gates,
    app_smoke,
    check_learn_paths,
    check_protected,
    npm_gates,
    run_command,
    smoke_http,
)


def test_smoke_no_app(tmp_path):
    assert smoke_http(tmp_path).ok


def test_smoke_ok(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {"dev": (
            "node -e \"require('http').createServer((q,s)=>s.end('ok'))"
            ".listen(process.env.APP_PORT)\""
        )}
    }))
    res = smoke_http(tmp_path, timeout=30)
    assert res.ok, res.detail


def test_smoke_dev_dies(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"dev": "false"}}))
    res = smoke_http(tmp_path, timeout=15)
    assert not res.ok


def test_protected_violation():
    cfg = Config()
    bad = check_protected(["orchestrator/src/aidevteam/cli.py"], {"app"}, cfg)
    assert not bad.ok and "orchestrator/" in bad.detail
    ok = check_protected(["orchestrator/src/aidevteam/cli.py"], {"sdlc"}, cfg)
    assert ok.ok
    ok2 = check_protected(["app/src/index.ts"], {"app"}, cfg)
    assert ok2.ok


def test_learn_paths():
    assert check_learn_paths(["app/memory/facts/x.md", "app/skills/s/SKILL.md"]).ok
    assert not check_learn_paths(["app/src/index.ts"]).ok


def test_run_command_ok(tmp_path):
    res = run_command("true", ["true"], tmp_path, 10)
    assert res.ok


def test_npm_gates_no_app(tmp_path):
    results = npm_gates(tmp_path, 60)
    assert len(results) == 1 and results[0].ok


def test_npm_gates_with_scripts(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "true", "lint": "false"}})
    )
    results = npm_gates(tmp_path, 60)
    names = {r.name: r.ok for r in results}
    assert names["npm test"] is True and names["npm run lint"] is False


def test_app_gates_python_passing(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\nversion = '0.1.0'\n")
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    results = app_gates(tmp_path, 120)
    assert [r.name for r in results] == ["pytest"]
    assert results[0].ok, results[0].detail


def test_app_gates_python_failing(tmp_path):
    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    results = app_gates(tmp_path, 120)
    assert [r.name for r in results] == ["pytest"]
    assert not results[0].ok


def test_app_gates_python_without_tests_or_lint(tmp_path):
    (tmp_path / "main.py").write_text("print('hi')\n")
    results = app_gates(tmp_path, 60)
    assert len(results) == 1 and results[0].ok


def test_app_gates_no_app(tmp_path):
    results = app_gates(tmp_path, 60)
    assert len(results) == 1 and results[0].ok


def test_app_smoke_python_ok(tmp_path):
    (tmp_path / "main.py").write_text("x = 1\n")
    assert app_smoke(tmp_path).ok


def test_app_smoke_python_syntax_error(tmp_path):
    (tmp_path / "main.py").write_text("def broken(:\n")
    res = app_smoke(tmp_path)
    assert not res.ok and "smoke" in res.name

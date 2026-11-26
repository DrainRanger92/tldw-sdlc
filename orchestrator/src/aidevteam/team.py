"""Единственная точка вызова агентов: opencode (legacy) или dsh."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from aidevteam.config import Config, parse_model_spec


class BudgetExceeded(RuntimeError):
    pass


class CallBudget:
    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0

    def spend(self) -> None:
        self.used += 1
        if self.used > self.limit:
            raise BudgetExceeded(
                f"превышен бюджет вызовов агентов ({self.limit} на цикл)"
            )


@dataclass
class AgentResult:
    ok: bool
    text: str
    session_id: str | None = None
    error: str = ""
    tokens: int = 0
    cost: float = 0.0


def opencode_bin() -> str:
    return os.environ.get("AIDEVTEAM_OPENCODE_BIN", "opencode")


def read_env_file(path: Path) -> dict[str, str]:
    """Простой .env: KEY=VALUE, кавычки, комментарии; без подстановок."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def agent_backend(cfg: Config) -> str:
    """Активный бэкенд: env важнее aidevteam.toml, чтобы тесты не правили файл."""
    return (os.environ.get("AIDEVTEAM_AGENTS_BACKEND") or cfg.agents_backend or "opencode").strip().lower()


def dsh_bin(cfg: Config, root: Path | None = None) -> str | None:
    """Резолв DSH без абсолютных путей в коде: env → конфиг → PATH."""
    candidate = os.environ.get("AIDEVTEAM_DSH_BIN") or cfg.dsh_bin or "dsh"
    candidate = os.path.expanduser(candidate)
    if os.path.dirname(candidate):
        p = Path(candidate)
        if not p.is_absolute() and root is not None:
            p = root / p
        return str(p) if p.exists() and os.access(p, os.X_OK) else None
    return shutil.which(candidate)


def opencode_probe(root: Path) -> bool:
    try:
        res = subprocess.run(
            [opencode_bin(), "--version"], cwd=root,
            capture_output=True, text=True, timeout=30,
        )
        return res.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def dsh_version(cfg: Config, root: Path | None = None) -> str | None:
    binary = dsh_bin(cfg, root)
    if not binary:
        return None
    try:
        res = subprocess.run(
            [binary, "--version"], cwd=root,
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    return res.stdout.strip().splitlines()[0] if res.stdout.strip() else None


def probe(root: Path, cfg: Config | None = None) -> bool:
    cfg = cfg or Config.load(root)
    backend = agent_backend(cfg)
    if backend == "dsh":
        version = dsh_version(cfg, root)
        if version is None:
            return False
        # Пин версии — часть контракта: PREFLIGHT не должен пускать чужую версию.
        return not cfg.dsh_version or version == cfg.dsh_version
    if backend == "opencode":
        return opencode_probe(root)
    return False


def _parse_events(stdout: str) -> tuple[str, str | None, int, float]:
    """Разбор JSON-событий opencode: текст, session_id, токены, стоимость."""
    texts: list[str] = []
    session_id: str | None = None
    tokens = 0
    cost = 0.0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_id = ev.get("sessionID") or session_id
        part = ev.get("part") or {}
        if ev.get("type") == "text" and part.get("text"):
            texts.append(part["text"])
        elif ev.get("type") == "step_finish":
            tk = part.get("tokens") or {}
            tokens += int(tk.get("total") or 0)
            cost += float(part.get("cost") or 0.0)
    # событий не было (например, фейковый бинарь в тестах) — берём сырой stdout
    text = "\n".join(texts).strip() if texts else stdout.strip()
    return text, session_id, tokens, cost


def run_agent(
    root: Path, role: str, prompt: str,
    timeout: int = 1800, session: str | None = None,
) -> AgentResult:
    cfg = Config.load(root)
    backend = agent_backend(cfg)
    if backend == "dsh":
        return _run_dsh(root, cfg, role, prompt, timeout, session)
    if backend == "opencode":
        return _run_opencode(root, role, prompt, timeout, session)
    return AgentResult(
        False, "", None,
        f"неизвестный бэкенд агентов: {backend!r} (opencode|dsh)",
    )


def _run_opencode(
    root: Path, role: str, prompt: str,
    timeout: int, session: str | None,
) -> AgentResult:
    cmd = [
        opencode_bin(), "run", "--agent", role, "--auto",
        "--format", "json", "--dir", str(root),
    ]
    if session:
        cmd += ["-s", session]
    cmd += ["--", prompt]
    try:
        res = subprocess.run(
            cmd, cwd=root, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return AgentResult(False, "", None, f"таймаут этапа ({timeout}s)")
    except OSError as exc:
        return AgentResult(False, "", None, str(exc))
    text, session_id, tokens, cost = _parse_events(res.stdout)
    if res.returncode != 0:
        return AgentResult(False, text, session_id, res.stderr.strip()[:500])
    return AgentResult(True, text, session_id, "", tokens=tokens, cost=cost)


def _parse_dsh_events(stdout: str) -> tuple[str, str | None, str, object]:
    """Разбор NDJSON headless DSH: final.text, session, error, reason."""
    texts: list[str] = []
    final_text = ""
    saw_final = False
    session_id: str | None = None
    error = ""
    reason: object = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "session":
            session_id = ev.get("sessionId") or session_id
        elif kind == "final":
            final_text = ev.get("text") or ""
            saw_final = True
            session_id = ev.get("sessionId") or session_id
            reason = ev.get("reason")
        elif kind == "text":
            texts.append(ev.get("text") or "")
        elif kind == "error":
            error = ev.get("message") or ev.get("error") or "ошибка DSH"
    text = final_text if saw_final else "\n".join(t for t in texts if t).strip()
    return text, session_id, error, reason


def _model_overlay(root: Path, cfg: Config, role: str) -> tuple[Path | None, str]:
    """Оверлей модели на вызов: модель и reasoning effort роли из toml."""
    spec = cfg.agent_models.get(role) or cfg.agent_models.get("default")
    if not spec:
        return None, ""
    try:
        provider, model, effort = parse_model_spec(spec)
    except ValueError as exc:
        return None, f"модель {role}: {exc}"
    path = root / ".aidevteam" / "dsh" / f"model-{role}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "- id: agent-default-model\n"
        "  config:\n"
        f"    provider: {json.dumps(provider, ensure_ascii=False)}\n"
        f"    model: {json.dumps(model, ensure_ascii=False)}\n"
    )
    if effort:
        body += f"    reasoningEffort: {json.dumps(effort, ensure_ascii=False)}\n"
    path.write_text(body, encoding="utf-8")
    return path, ""


ROLE_WRITES = {"analyst": True, "planner": True, "developer": True, "reviewer": False}
ROLE_MARKS = {
    "analyst": "аналитик",
    "planner": "планировщик",
    "developer": "разработчик",
    "reviewer": "ревьюер",
}


def _dump_row(text: str, row_id: str) -> str:
    """Вырезать блок строки профиля из `dsh --dump-config` по её id."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == f"- id: {row_id}":
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("- id:") or lines[j].startswith("# =="):
            end = j
            break
    return "\n".join(lines[start:end])


def dsh_composition(root: Path, cfg: Config, role: str) -> tuple[str, str]:
    """Собрать профиль с оверлеем роли; вернуть (dump, ошибка)."""
    binary = dsh_bin(cfg, root)
    if not binary:
        return "", "dsh не найден"
    role_file = root / cfg.dsh_roles_dir / f"{role}.yml"
    if not role_file.exists():
        return "", f"нет оверлея роли: {role_file}"
    temp_home: tempfile.TemporaryDirectory | None = None
    env = os.environ.copy()
    if not cfg.dsh_home:
        temp_home = tempfile.TemporaryDirectory(prefix="aidevteam-dsh-home-")
        env["DSH_HOME"] = temp_home.name
    else:
        env["DSH_HOME"] = str(Path(cfg.dsh_home).expanduser())
    try:
        res = subprocess.run(
            [binary, "--profile", cfg.dsh_profile, "--patch", str(role_file),
             "--dump-config"],
            cwd=root, capture_output=True, text=True, timeout=120, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", str(exc)[:300]
    finally:
        if temp_home is not None:
            temp_home.cleanup()
    if res.returncode != 0:
        return "", (res.stderr or res.stdout).strip()[:500]
    return res.stdout, ""


def dsh_composition_problems(root: Path, cfg: Config, role: str) -> list[str]:
    """Проверить, что оверлей роли собрался и права соответствуют роли."""
    dump, error = dsh_composition(root, cfg, role)
    if error:
        return [f"{role}: {error}"]
    problems: list[str] = []
    persona = _dump_row(dump, "system-prompt")
    if "personaPrefix:" not in persona:
        problems.append(f"{role}: в профиле нет system-prompt.personaPrefix")
    elif "personaPrefix: ''" in persona or 'personaPrefix: ""' in persona:
        problems.append(f"{role}: пустая персона")
    elif ROLE_MARKS[role] not in persona:
        problems.append(f"{role}: персона не похожа на роль ({ROLE_MARKS[role]})")
    fs_row = _dump_row(dump, "tool-fs")
    expected_write = ROLE_WRITES[role]
    for field in ("write", "edit"):
        expected = f"{field}: {'true' if expected_write else 'false'}"
        if expected not in fs_row:
            problems.append(f"{role}: tool-fs.{field} не равен {expected_write}")
    for row_id in ("tool-subagent", "tool-subagent-fork", "tool-workflow"):
        row = _dump_row(dump, row_id)
        if not row:
            problems.append(f"{role}: строка {row_id} исчезла из профиля DSH")
        elif "disabled: true" not in row:
            problems.append(f"{role}: {row_id} не отключён")
    if role == "reviewer" and "read-only:" not in dump:
        problems.append("reviewer: в профиле DSH нет permission preset read-only")
    return problems


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _reap(proc: subprocess.Popen) -> None:
    """После killpg не ждать потомков бесконечно: закрыть пайпы и вернуть код."""
    try:
        proc.communicate(timeout=5)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass


def _reason_detail(reason: object) -> str:
    """Человекочитаемая причина незавершённого хода DSH."""
    if not isinstance(reason, dict):
        return ""
    kind = reason.get("kind")
    error = reason.get("error")
    message = ""
    code = ""
    if isinstance(error, dict):
        message = str(error.get("message") or "")
        code = str(error.get("code") or "")
    elif isinstance(error, str):
        message = error
    if kind and kind != "completed":
        parts = ["dsh:", str(kind)]
        if code:
            parts.append(code)
        text = " ".join(parts)
        return text + (f": {message}" if message else "")
    if message:
        return f"dsh: {message}"
    return ""


def _run_dsh(
    root: Path, cfg: Config, role: str, prompt: str,
    timeout: int, session: str | None,
) -> AgentResult:
    binary = dsh_bin(cfg, root)
    if not binary:
        return AgentResult(
            False, "", None,
            "dsh не найден — задайте AIDEVTEAM_DSH_BIN или agents.dsh.bin",
        )
    role_file = root / cfg.dsh_roles_dir / f"{role}.yml"
    if not role_file.exists():
        return AgentResult(
            False, "", None,
            f"нет оверлея роли: {role_file} (ожидается в {cfg.dsh_roles_dir}/)",
        )
    model_file, model_error = _model_overlay(root, cfg, role)
    if model_error:
        return AgentResult(False, "", None, model_error)

    permission_mode = cfg.dsh_permission_mode
    if role == "reviewer" and cfg.dsh_reviewer_permission_mode:
        # Ревьюеру нужен git diff, но не нужны записи: read-only песочница
        # сильнее, чем просто отсутствие write/edit в tool-fs.
        permission_mode = cfg.dsh_reviewer_permission_mode

    cmd = [binary, "--profile", cfg.dsh_profile, "--patch", str(role_file)]
    if model_file is not None:
        cmd += ["--patch", str(model_file)]
    if session:
        cmd += ["--session-id", session]
    cmd += [
        "--permission-mode", permission_mode,
        "--approval-policy", cfg.dsh_approval_policy,
        "--json", "-",
    ]

    env = os.environ.copy()
    if cfg.dsh_home:
        env["DSH_HOME"] = str(Path(cfg.dsh_home).expanduser())
    for key, value in read_env_file(root / cfg.dsh_env_file).items():
        # Пустая переменная в shell не должна перекрывать значение из .env.
        if not env.get(key):
            env[key] = value
    try:
        proc = subprocess.Popen(
            cmd, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env, start_new_session=True,
        )
    except OSError as exc:
        return AgentResult(False, "", None, str(exc))
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        _reap(proc)
        return AgentResult(False, "", None, f"таймаут этапа ({timeout}s)")
    except BaseException:
        # Ctrl+C и любые другие прерывания: не оставляем DSH-потомков жить
        # после того, как оркестратор уже отпустил lock.
        _kill_group(proc)
        _reap(proc)
        raise

    text, session_id, event_error, reason = _parse_dsh_events(stdout)
    reason_detail = _reason_detail(reason)
    if proc.returncode != 0:
        detail = (
            reason_detail
            or (stderr or "").strip()
            or event_error
            or f"dsh: exit {proc.returncode}"
        )
        return AgentResult(False, text, session_id, detail[:500])
    if reason_detail:
        return AgentResult(False, text, session_id, reason_detail[:500])
    if event_error:
        return AgentResult(False, text, session_id, event_error[:500])
    if not text.strip():
        return AgentResult(False, "", session_id, "dsh: пустой ответ агента")
    return AgentResult(True, text, session_id, "")

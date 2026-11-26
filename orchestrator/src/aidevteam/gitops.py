from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(root: Path, *args: str, check: bool = True) -> str:
    res = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True
    )
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {res.stderr.strip()}")
    return res.stdout


def head_sha(root: Path) -> str:
    return run_git(root, "rev-parse", "HEAD").strip()


def commits_since(root: Path, sha: str | None) -> list[tuple[str, str]]:
    if sha is None:
        return []
    out = run_git(root, "log", "--format=%H\t%s", f"{sha}..HEAD")
    return [tuple(line.split("\t", 1)) for line in out.splitlines()]


def dirty_paths(root: Path) -> list[str]:
    out = run_git(root, "status", "--porcelain", "-uall")
    return [line[3:].strip() for line in out.splitlines()]


def changed_since(root: Path, sha: str) -> list[str]:
    committed = run_git(root, "diff", "--name-only", sha, "HEAD").splitlines()
    return sorted(set(committed) | set(dirty_paths(root)))


def _commit(root: Path, message: str, name: str, email: str) -> str:
    run_git(
        root, "-c", f"user.name={name}", "-c", f"user.email={email}",
        "commit", "-m", message,
    )
    return head_sha(root)


def commit_all(root: Path, message: str, name: str, email: str) -> str:
    run_git(root, "add", "-A")
    return _commit(root, message, name, email)


def commit_paths(root: Path, paths: list[str], message: str, name: str, email: str) -> str:
    run_git(root, "add", "--", *paths)
    return _commit(root, message, name, email)


def user_identity(root: Path) -> tuple[str, str]:
    name = run_git(root, "config", "user.name", check=False).strip() or "user"
    email = run_git(root, "config", "user.email", check=False).strip() or "user@local"
    return name, email


def try_push(root: Path) -> str:
    remotes = run_git(root, "remote", check=False).split()
    if "origin" not in remotes:
        return "skipped: нет origin"
    res = subprocess.run(
        ["git", "push", "origin", "HEAD"], cwd=root, capture_output=True, text=True
    )
    return "ok" if res.returncode == 0 else f"skipped: {res.stderr.strip()[:80]}"

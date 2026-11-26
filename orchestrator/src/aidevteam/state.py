from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


def _dir(root: Path) -> Path:
    d = root / ".aidevteam"
    d.mkdir(exist_ok=True)
    return d


@dataclass
class State:
    last_processed_commit: str | None = None
    cycles: int = 0
    # номер текущего инкремента — чисто для статистики и логов
    increment_no: int = 1

    @classmethod
    def load(cls, root: Path) -> "State":
        f = _dir(root) / "state.json"
        if not f.exists():
            return cls()
        data = json.loads(f.read_text(encoding="utf-8"))
        return cls(
            last_processed_commit=data.get("last_processed_commit"),
            cycles=int(data.get("cycles", 0)),
            increment_no=int(data.get("increment_no", 1)),
        )

    def save(self, root: Path) -> None:
        f = _dir(root) / "state.json"
        f.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "last_processed_commit": self.last_processed_commit,
                    "cycles": self.cycles,
                    "increment_no": self.increment_no,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def append_journal(root: Path, entry: dict) -> None:
    with (_dir(root) / "journal.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_journal(root: Path, n: int) -> list[dict]:
    f = _dir(root) / "journal.jsonl"
    if not f.exists():
        return []
    lines = f.read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in reversed(lines[-n:])]


def write_failure(root: Path, stage: str, error: str) -> Path:
    f = _dir(root) / "last-failure.md"
    f.write_text(
        f"# Сбой цикла — этап {stage}\n\n```\n{error}\n```\n\n"
        "Следующий запуск проанализирует этот отчёт (этап ANALYZE).\n",
        encoding="utf-8",
    )
    return f


def read_failure(root: Path) -> str | None:
    f = root / ".aidevteam" / "last-failure.md"
    return f.read_text(encoding="utf-8") if f.exists() else None


def clear_failure(root: Path) -> None:
    f = root / ".aidevteam" / "last-failure.md"
    if f.exists():
        f.unlink()

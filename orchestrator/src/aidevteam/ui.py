from __future__ import annotations

import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


class StageCtx:
    def __init__(self, console: Console, name: str):
        self.console = console
        self.name = name
        self.start = time.monotonic()

    def __enter__(self) -> "StageCtx":
        return self

    def __exit__(self, *exc) -> None:
        dur = time.monotonic() - self.start
        m, s = divmod(int(dur), 60)
        self.console.print(f"  [dim]— {m}m{s:02d}s[/dim]" if m else f"  [dim]— {s}s[/dim]")

    def _line(self, sign: str, style: str, text: str) -> None:
        self.console.print(f"  [{style}]{sign}[/{style}] {text}")

    def ok(self, text: str) -> None:
        self._line("✔", "green", text)

    def warn(self, text: str) -> None:
        self._line("⚠", "yellow", text)

    def fail(self, text: str) -> None:
        self._line("✘", "red", text)

    def off(self, text: str) -> None:
        self._line("─", "dim", text)


class UI:
    def __init__(self, verbose: bool = False):
        self.console = Console(highlight=False)
        self.verbose = verbose

    def stage(self, name: str, note: str = "") -> StageCtx:
        self.console.print(f"\n[bold]⏵ {name}[/bold]   [dim]{note}[/dim]")
        return StageCtx(self.console, name)

    def skip(self, name: str, reason: str) -> None:
        self.console.print(f"\n[bold]⏵ {name}[/bold]   [dim]\\[skip] {reason}[/dim]")

    def plain(self, text: str) -> None:
        # строки уже сверстаны вызывающим кодом: не даём rich ломать их по ширине
        # терминала (иначе команды вида «aidevteam refine D-001» рвутся пополам)
        self.console.print(text, soft_wrap=True)

    def wait(self, text: str):
        """Спиннер на время долгой операции (вызов LLM и т.п.)."""
        return self.console.status(f"[dim]{text}[/dim]")

    def report(self, title: str, rows: list[tuple[str, str]]) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold", no_wrap=True)
        table.add_column()
        for key, val in rows:
            table.add_row(key, val)
        self.console.print(Panel(table, title=title, border_style="green"))

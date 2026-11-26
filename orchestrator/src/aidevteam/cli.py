"""CLI: разбор команд, диспетчеризация, коды выхода."""
from __future__ import annotations

import argparse
import difflib
import os
import sys

from aidevteam import __version__

COMMANDS: dict[str, str] = {
    "run": "выполнить один цикл SDLC",
    "next": "предпросмотр: что сделает следующий цикл (без LLM)",
    "status": "диагностика: окружение, проект, последний цикл",
    "doctor": "проверка компонентов: git, python, бэкенд агентов, роли, артефакты",
    "init": "явный bootstrap окружения",
    "list": "просмотр артефактов: tasks | specs | skills | memory",
    "show": "карточка артефакта: show task <ID>",
    "move": "move T-007 current|backlog | move T-007 before|after T-006",
    "add": 'intake: add | add task "…" | add story "Как <роль>, я хочу…"',
    "refine": "диалог с аналитиком по черновику: refine <ID>",
    "plan": "оформить черновики в задачи: plan [D-ID] (без цикла)",
    "watch": "режим наблюдения: цикл при появлении работы",
    "log": "журнал последних циклов",
    "app": "приложение: app dev | test | stop",
    "help": "эта справка",
}


def _force_utf8_stdio(streams=None) -> None:
    """UTF-8 для stdin/stdout/stderr, битые байты заменяются, а не валят ввод.

    PYTHONIOENCODING=utf-8 без обработчика делал stdin строгим: неполный
    UTF-8-байт с терминала (оборванная вставка, IME) валил ответ в диалоге с
    `UnicodeDecodeError`. В UTF-8-режиме по умолчанию ошибки — surrogateescape,
    их восстанавливает `text.clean_text`; здесь дополнительно гарантируем, что
    ни один поток не поднимет исключение на битом байте.
    """
    for stream in streams if streams is not None else (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aidevteam", add_help=False)
    p.add_argument("--version", action="store_true")
    p.add_argument("command", nargs="?")
    p.add_argument("args", nargs=argparse.REMAINDER)
    return p


def print_help() -> None:
    print(f"aidevteam v{__version__} — AI-native SDLC orchestrator\n")
    print("Использование: aidevteam <команда> [опции]\n")
    for name, desc in COMMANDS.items():
        print(f"  {name:<10} {desc}")
    print("\nКоды выхода: 0 — успех, 1 — preflight/гейт, 2 — авария цикла")


def suggest(cmd: str) -> None:
    print(f"  ✘ Неизвестная команда: {cmd}\n")
    close = difflib.get_close_matches(cmd, COMMANDS, n=3, cutoff=0.4) or ["help"]
    print("    Похожие команды:")
    for name in close:
        print(f"      aidevteam {name:<10} {COMMANDS[name]}")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ns = build_parser().parse_args(argv)
    if ns.version:
        print(f"aidevteam {__version__}")
        return 0
    cmd = ns.command or "help"
    if cmd == "help":
        print_help()
        return 0
    if cmd not in COMMANDS:
        suggest(cmd)
        return 1
    from aidevteam import commands as _commands

    try:
        return _commands.dispatch(cmd, ns.args)
    except KeyboardInterrupt:
        print("\n  Прервано пользователем.")
        return 130
    except Exception as exc:
        print(f"  ✘ авария: {exc}", file=sys.stderr)
        if os.environ.get("AIDEVTEAM_TRACEBACK"):
            raise
        print("  Подробности: AIDEVTEAM_TRACEBACK=1 aidevteam " + cmd, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

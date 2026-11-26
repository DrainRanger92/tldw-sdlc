"""CLI TLDW: проверка конфигурации, сборка и запуск бота.

Точка входа — ``app/main.py``. Логика вынесена сюда, чтобы тесты могли
собирать приложение и проверять сообщения об ошибках без запуска polling.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from tldw.access import describe_allowlist
from tldw.bootstrap import Application, build_application
from tldw.config import (
    CONFIG_HINT,
    DEFAULT_ENV_FILE,
    ConfigError,
    Settings,
    load_env_file,
    load_settings,
)
from tldw.logging_setup import configure_logging

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CONFIG_ERROR = 2

Runner = Callable[[Application], None]


def build_parser() -> argparse.ArgumentParser:
    """Собрать парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        prog="tldw",
        description="TLDW — Telegram-бот: ссылка на YouTube превращается в заметку Obsidian.",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="проверить env, собрать приложение и выйти без запуска бота",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help=f"путь к файлу .env (по умолчанию {DEFAULT_ENV_FILE})",
    )
    return parser


def _collect_environ(
    base: Mapping[str, str] | None,
    env_file: Path,
    *,
    explicit: bool,
) -> dict[str, str]:
    """Склеить реальный env и значения из .env (env приоритетнее)."""
    env = dict(os.environ if base is None else base)
    if explicit and not env_file.is_file():
        raise ConfigError(f"Файл конфигурации {env_file} не найден. {CONFIG_HINT}")
    load_env_file(env_file, env)
    return env


def _run_application(application: Application) -> None:
    """Запустить polling; вызов блокирующий."""
    application.run()


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    run_application: Runner | None = None,
) -> int:
    """Точка входа приложения.

    :param argv: аргументы CLI (по умолчанию ``sys.argv[1:]``).
    :param environ: источник переменных окружения (для тестов).
    :param run_application: подмена блокирующего запуска (для тестов).
    :return: код возврата процесса.
    """
    args = build_parser().parse_args(argv)
    env_file = args.env_file if args.env_file is not None else DEFAULT_ENV_FILE
    try:
        env = _collect_environ(environ, env_file, explicit=args.env_file is not None)
        settings: Settings = load_settings(env)
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        configure_logging(settings.log_level)
        application = build_application(settings)
    except Exception as exc:  # noqa: BLE001 - на старте любая ошибка должна быть понятной
        log.exception("Не удалось собрать приложение")
        print(
            f"Не удалось запустить TLDW: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print(CONFIG_HINT, file=sys.stderr)
        return EXIT_FAILURE

    if args.check_config:
        asyncio.run(application.close())
        print(
            "Конфигурация в порядке: приложение собрано "
            f"(модель LLM: {settings.llm_model}, "
            f"порог видео: {settings.max_video_duration_seconds} с, "
            f"доступ: {describe_allowlist(settings.allowed_user_ids)})."
        )
        return EXIT_OK

    runner = _run_application if run_application is None else run_application
    log.info(
        "TLDW запускается: модель %s, порог видео %s с, доступ: %s",
        settings.llm_model,
        settings.max_video_duration_seconds,
        describe_allowlist(settings.allowed_user_ids),
    )
    try:
        runner(application)
    except KeyboardInterrupt:
        log.info("Остановка по Ctrl+C")
    except Exception:  # noqa: BLE001 - не роняем процесс трейсбеком
        log.exception("Приложение остановлено из-за ошибки")
        return EXIT_FAILURE
    finally:
        if run_application is not None:
            asyncio.run(application.close())
    return EXIT_OK


__all__ = ["EXIT_CONFIG_ERROR", "EXIT_FAILURE", "EXIT_OK", "build_parser", "main"]

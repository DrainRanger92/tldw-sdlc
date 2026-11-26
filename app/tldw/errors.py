"""Понятные ошибки пользователю и безопасный лог сбоев (T-008).

Модуль — единственное место, где типизированные исключения адаптеров и
сервисов превращаются в ответ пользователю и запись в лог. Он нейтральный:
импортирует только стандартную библиотеку, поэтому адаптеры и сервисы могут
наследовать :class:`UserFacingError`, не нарушая границы слоёв (ADR-002).

Контракт:

- ошибки, унаследованные от :class:`UserFacingError`, считаются «известными»:
  их сообщение уже написано для пользователя и уходит в Telegram как есть, а в
  лог пишется причина (тип и безопасный текст) на уровне ``WARNING`` без
  стектрейса;
- остальные исключения считаются неизвестным сбоем: пользователь видит
  :data:`MESSAGE_UNEXPECTED`, а в лог на уровне ``ERROR`` пишется стектрейс;
- любая запись в лог проходит через :func:`sanitize_log_text`: вырезаются
  токены и ключи API (включая ``Authorization: Bearer`` и значения переменных
  вида ``LLM_API_KEY=…``), а длинные фрагменты ограничиваются, поэтому полный
  транскрипт в лог не попадает.

Почему стектрейс форматируется вручную, а не через ``log.exception``:
стандартный ``exc_info`` обошёл бы очистку, а сообщение исключения или URL
запроса может содержать токен бота и ключ LLM (например, адрес
``api.telegram.org/bot<token>/…``).
"""

from __future__ import annotations

import logging
import re
import traceback
from dataclasses import dataclass
from typing import Final

log = logging.getLogger(__name__)

#: Общее сообщение для неизвестного сбоя: без деталей и стектрейса.
MESSAGE_UNEXPECTED: Final = "Не удалось обработать видео. Попробуйте позже."
#: Чем заменяются секреты в логе.
REDACTED: Final = "[скрыто]"
#: Предел длины одного фрагмента причины в логе (символов).
MAX_LOG_FIELD_CHARS: Final = 500
#: Предел длины строки стектрейса: защита от транскрипта в тексте исключения.
MAX_TRACEBACK_LINE_CHARS: Final = 300
#: Предел длины всего стектрейса; обычный стек укладывается с запасом.
MAX_TRACEBACK_CHARS: Final = 8000
#: Предел длины сообщения известной ошибки: длинный текст — уже не сообщение.
MAX_USER_MESSAGE_CHARS: Final = 1000
#: Начало блока кадров стектрейса.
_TRACEBACK_HEADER: Final = "Traceback (most recent call last)"
#: Начало строки кадра стека (единственный признак настоящего кадра).
_FRAME_PREFIX: Final = '  File "'
#: Отступ строк-исходников кадра; у сообщения исключения бывает такой же.
_FRAME_BODY_INDENT: Final = "    "
#: Сколько строк после кадра относятся к нему: исходник и (3.11+) каретка.
_MAX_FRAME_BODY_LINES: Final = 2
#: Начало разделителя цепочки исключений (``raise … from …``, «во время обработки»).
_EXCEPTION_CHAIN_PREFIXES: Final = (
    "During handling of the above exception",
    "The above exception was the direct cause",
)

#: Шаблоны секретов: (регулярное выражение, замена).
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Значения переменных окружения и заголовков: LLM_API_KEY=…, Authorization: ….
    # Схема авторизации входит в значение: иначе ``\S+`` съел бы только слово
    # «Bearer», а сам токен остался бы в тексте (формат ``adapters/llm.py``).
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:TOKEN|API_KEY|APIKEY|SECRET|PASSWORD|AUTHORIZATION)[A-Z0-9_]*)"
            r"\s*[=:]\s*(?:(?:bearer|basic|token)\s+)?\S+"
        ),
        rf"\1={REDACTED}",
    ),
    # «bearer <token>» без имени заголовка — свободный текст ошибки.
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+"), rf"\1 {REDACTED}"),
    # Учётные данные в URL: scheme://user:password@host.
    (re.compile(r"(?i)(://)[^/\s:@]+:[^/\s@]+@"), rf"\1{REDACTED}@"),
    # Токен Telegram-бота: <bot_id>:<secret>, в том числе в URL «/bot<token>/».
    (re.compile(r"\d{6,}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"), REDACTED),
    # Ключи в стиле OpenAI/DeepSeek.
    (re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"), REDACTED),
)


class UserFacingError(Exception):
    """Базовая ошибка, сообщение которой безопасно показывать пользователю.

    Наследники задают текст в конструкторе (обычно из константы модуля).
    Обработчик отличает такие ошибки от неизвестных сбоев и не показывает
    стектрейс.
    """


@dataclass(frozen=True, slots=True)
class ErrorReport:
    """Готовый разбор сбоя: что ответить пользователю и что записать в лог.

    :param user_message: текст ответа в Telegram; без стектрейса и секретов.
    :param reason: причина для лога — тип исключения и безопасный текст.
    :param level: уровень логирования: ``WARNING`` для известных ошибок,
        ``ERROR`` для неизвестного сбоя.
    :param traceback_text: очищенный стектрейс; только для неизвестных сбоев.
    """

    user_message: str
    reason: str
    level: int = logging.WARNING
    traceback_text: str | None = None


def describe_error(exc: BaseException) -> ErrorReport:
    """Свести исключение к ответу пользователю и безопасной записи в лог.

    Известные ошибки (:class:`UserFacingError`) отдают своё сообщение и
    причину на уровне ``WARNING``. Всё остальное — общее сообщение и
    ``ERROR`` со стектрейсом, очищенным от секретов и длинных фрагментов.
    """
    reason = format_reason(exc)
    if isinstance(exc, UserFacingError):
        return ErrorReport(
            user_message=safe_user_message(exc),
            reason=reason,
            level=logging.WARNING,
        )
    return ErrorReport(
        user_message=MESSAGE_UNEXPECTED,
        reason=reason,
        level=logging.ERROR,
        traceback_text=format_traceback(exc),
    )


def log_failure(exc: BaseException, *, logger: logging.Logger | None = None) -> ErrorReport:
    """Записать сбой в лог и вернуть разбор для ответа пользователю.

    Известная ошибка логируется одним сообщением с причиной, неизвестная —
    на уровне ``ERROR`` с очищенным стектрейсом. Ни сообщение, ни лог не
    содержат токенов, ключей API и полного транскрипта.
    """
    report = describe_error(exc)
    target = log if logger is None else logger
    if report.traceback_text is None:
        target.log(report.level, "Сбой обработки видео: %s", report.reason)
    else:
        target.log(
            report.level,
            "Неизвестный сбой обработки видео: %s\n%s",
            report.reason,
            report.traceback_text,
        )
    return report


def safe_user_message(exc: UserFacingError) -> str:
    """Безопасный текст известной ошибки для Telegram.

    Сообщения задаются константами модулей, но функция всё равно вырезает
    секреты и отвергает слишком длинный текст: если в сообщение случайно
    попал транскрипт, пользователь увидит общее сообщение, а не его.
    """
    message = _condense(str(exc))
    if not message or len(message) > MAX_USER_MESSAGE_CHARS:
        return MESSAGE_UNEXPECTED
    return message


def format_reason(exc: BaseException) -> str:
    """Причина сбоя для лога: тип исключения и очищенный текст сообщения."""
    message = sanitize_log_text(str(exc))
    name = type(exc).__name__
    return f"{name}: {message}" if message else name


def format_traceback(exc: BaseException) -> str:
    """Отформатировать стектрейс, вырезав секреты и длинные фрагменты.

    Кадры стека и их строки-исходники сохраняются, а всё остальное — текст
    исключения (в том числе многострочный) — схлопывается в одну очищенную
    строку. Кадром считается только строка ``  File "…"`` внутри блока
    ``Traceback …`` и следующие за ней строки-исходники: отступ в четыре
    пробела сам по себе признаком кадра не является, поэтому транскрипт с
    отступами не уходит в лог целиком, а причина остаётся видимой.
    """
    lines: list[str] = []
    message_parts: list[str] = []

    def flush_message() -> None:
        if message_parts:
            lines.append(sanitize_log_text(" ".join(message_parts)))
            message_parts.clear()

    in_frames = False
    frame_body_left = 0
    for line in "".join(traceback.format_exception(exc)).splitlines():
        if line.startswith(_TRACEBACK_HEADER):
            flush_message()
            in_frames, frame_body_left = True, 0
            lines.append(_sanitize_traceback_line(line))
        elif in_frames and line.startswith(_FRAME_PREFIX):
            flush_message()
            frame_body_left = _MAX_FRAME_BODY_LINES
            lines.append(_sanitize_traceback_line(line))
        elif frame_body_left > 0 and line.startswith(_FRAME_BODY_INDENT):
            flush_message()
            frame_body_left -= 1
            lines.append(_sanitize_traceback_line(line))
        elif line.startswith(_EXCEPTION_CHAIN_PREFIXES):
            flush_message()
            in_frames, frame_body_left = False, 0
            lines.append(_sanitize_traceback_line(line))
        else:
            in_frames, frame_body_left = False, 0
            message_parts.append(line)
    flush_message()

    text = "\n".join(lines).strip()
    if len(text) > MAX_TRACEBACK_CHARS:
        text = f"{text[:MAX_TRACEBACK_CHARS].rstrip()}\n… [стектрейс обрезан]"
    return text


def sanitize_log_text(value: object, *, max_length: int = MAX_LOG_FIELD_CHARS) -> str:
    """Очистить текст для лога: убрать секреты и ограничить длину.

    Пробельные последовательности схлопываются: причина должна занимать одну
    строку. Длинный текст (например, транскрипт, попавший в сообщение
    исключения) обрезается с явным маркером.
    """
    text = _condense(str(value))
    if max_length > 0 and len(text) > max_length:
        text = f"{text[:max_length].rstrip()}… [фрагмент обрезан]"
    return text


def _condense(value: str) -> str:
    """Схлопнуть пробелы в одну строку и вырезать секреты."""
    return _redact(" ".join(value.split()))


def _sanitize_traceback_line(line: str) -> str:
    """Очистить одну строку стектрейса, сохранив отступы и структуру."""
    text = _redact(line.rstrip())
    if len(text) > MAX_TRACEBACK_LINE_CHARS:
        text = f"{text[:MAX_TRACEBACK_LINE_CHARS].rstrip()}… [фрагмент обрезан]"
    return text


def _redact(text: str) -> str:
    """Заменить секреты в тексте на :data:`REDACTED`."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


__all__ = [
    "ErrorReport",
    "MAX_LOG_FIELD_CHARS",
    "MAX_TRACEBACK_CHARS",
    "MAX_TRACEBACK_LINE_CHARS",
    "MAX_USER_MESSAGE_CHARS",
    "MESSAGE_UNEXPECTED",
    "REDACTED",
    "UserFacingError",
    "describe_error",
    "format_reason",
    "format_traceback",
    "log_failure",
    "safe_user_message",
    "sanitize_log_text",
]

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
  вида ``LLM_API_KEY=…``), URL сводится к схеме и host без пути и query, а
  длинные фрагменты ограничиваются, поэтому полный транскрипт и полный URL в
  лог не попадают;
- у записи есть необязательный **контекст**: по умолчанию это «обработки
  видео» (прежний текст цикла T-008), а сценарий шаблона передаёт «разбора
  шаблона (пользовательский, разделов: 2)», поэтому сбой говорит о шаблоне, а
  не о видео, и не раскрывает текст шаблона;
- здесь же живут нейтральные ошибки шаблона заметки
  (:class:`TemplateParseError`, :class:`TemplateValidationError`): их
  поднимает и домен шаблона, и LLM-адаптер, который по ADR-002 не импортирует
  ``services``;
- общее сообщение неизвестного сбоя можно переопределить
  (:data:`MESSAGE_UNEXPECTED_TEMPLATE`): сценарий шаблона говорит о шаблоне,
  а не о видео, и передаёт свой текст в ``unknown_message``;
- пользовательский текст лимита голосового сообщения
  (:data:`MESSAGE_AUDIO_TOO_LONG`) живёт здесь же — это общий ресурс
  обработчика и адаптера речи, а не константа слоя.

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
#: То же для сценария шаблона: сбой не про видео, а про шаблон (T-015).
MESSAGE_UNEXPECTED_TEMPLATE: Final = "Не удалось обработать шаблон. Попробуйте позже."
#: Единственный текст лимита голосового сообщения (FR-11): его используют и
#: обработчик (проверка до скачивания), и адаптер речи (защита внутри).
#: ``{limit}`` подставляется из :data:`tldw.config.MAX_AUDIO_DURATION_SECONDS`.
MESSAGE_AUDIO_TOO_LONG: Final = (
    "Голосовое сообщение длиннее {limit} секунд. "
    "Запишите описание короче или отправьте его текстом."
)
#: Чем заменяются секреты в логе.
REDACTED: Final = "[скрыто]"
#: Контекст сбоя по умолчанию: прежний текст записи для старых вызовов.
DEFAULT_FAILURE_CONTEXT: Final = "обработки видео"
#: Предел длины контекста в логе (символов): контекст — короткая пометка.
MAX_CONTEXT_CHARS: Final = 120
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
#: Начало разделителя цепочки исключений (``raise … from …``, «во время обработки»).
_EXCEPTION_CHAIN_PREFIXES: Final = (
    "During handling of the above exception",
    "The above exception was the direct cause",
)
#: Предел вложенности ``ExceptionGroup``: защита от рекурсивного обхода.
MAX_EXCEPTION_GROUP_DEPTH: Final = 8

#: Шаблоны секретов: (регулярное выражение, замена).
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Переменные окружения: секретное слово стоит **внутри** имени, а не только
    # в конце. Хвост ``[A-Za-z0-9_]*`` после ключевого слова возвращает имена с
    # суффиксом (``SECRET_KEY``, ``AWS_SECRET_ACCESS_KEY``, ``PASSWORD_HASH``,
    # ``ACCESS_TOKEN_HASH``) — без него сужение шаблона открыло регресс
    # (REVIEW-2 T-016). Регистр не важен, иначе ``llm_api_key=…``/
    # ``db_password=…`` оставались в логе. Исключения — не-секреты, которые
    # начинаются с ключевого слова: счётчики ``*_TOKENS`` (``TOKEN(?!S)``) и
    # токенизатор ``tokenizer``/``TOKENIZERS_PARALLELISM`` (``IZER``):
    # ``max_tokens``/``MAX_OUTPUT_TOKENS`` — лимит ответа, а не секрет (P3
    # T-008). Токенизатор открылся уже после хвоста имени из FIX-2: до него
    # имя должно было заканчиваться ключевым словом, поэтому ``tokenizer=…``
    # не скрывался; проверено T-019.
    (
        re.compile(
            r"\b((?:[A-Za-z_][A-Za-z0-9_]*?)?(?:TOKEN(?!S|IZER)|API_?KEY|SECRET|PASSWORD|"
            r"AUTHORIZATION)[A-Za-z0-9_]*)\s*[=:]\s*(?:(?:bearer|basic|token)\s+)?\S+",
            re.IGNORECASE,
        ),
        rf"\1={REDACTED}",
    ),
    # Заголовки и ключи в любом регистре: ``x-api-key``, ``access_token``.
    # Граница слова не даёт совпасть с ``max_tokens``: перед «token» в нём
    # стоит подчёркивание, а после — буква.
    (
        re.compile(
            r"(?i)\b(authorization|api[-_]?key|access[-_]?token|refresh[-_]?token|"
            r"client[-_]?secret|secret|password|token)\s*[=:]\s*"
            r"(?:(?:bearer|basic|token)\s+)?\S+"
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
    # URL в тексте исключения: остаётся только схема и host, путь/query/фрагмент
    # вырезаются. Иначе полный URL с приватным ``si=`` или токеном в query
    # попадал в лог через ``str(exc)`` (P3 T-006/T-016).
    (
        re.compile(r"(?i)\b(https?://[^\s<>\"'/?#]+)(?:[/?#][^\s<>\"']*)?"),
        r"\1",
    ),
)


class UserFacingError(Exception):
    """Базовая ошибка, сообщение которой безопасно показывать пользователю.

    Наследники задают текст в конструкторе (обычно из константы модуля).
    Обработчик отличает такие ошибки от неизвестных сбоев и не показывает
    стектрейс.
    """


class TemplateParseError(UserFacingError):
    """Из описания или правки не удалось собрать изменения шаблона.

    Живёт в нейтральном ``errors.py``, а не в ``services/template.py``:
    LLM-адаптер разбирает описание шаблона и поднимает эту ошибку, но по
    ADR-002 не импортирует сервисный слой. Домен шаблона реэкспортирует её,
    поэтому ``tldw.services.template.TemplateParseError`` — тот же класс.
    """


class TemplateValidationError(UserFacingError):
    """Шаблон не прошёл валидацию; текст сообщения безопасен для пользователя.

    Определена здесь по тем же причинам, что и :class:`TemplateParseError`:
    ошибка нужна и домену шаблона, и слоям, которые его не импортируют.
    """


@dataclass(frozen=True, slots=True)
class ErrorReport:
    """Готовый разбор сбоя: что ответить пользователю и что записать в лог.

    :param user_message: текст ответа в Telegram; без стектрейса и секретов.
    :param reason: причина для лога — тип исключения и безопасный текст.
    :param level: уровень логирования: ``WARNING`` для известных ошибок,
        ``ERROR`` для неизвестного сбоя.
    :param traceback_text: очищенный стектрейс; только для неизвестных сбоев.
    :param context: о чём запись в логе — «обработки видео» по умолчанию или
        «разбора шаблона (…, разделов: 2)» у шаблонного сценария.
    """

    user_message: str
    reason: str
    level: int = logging.WARNING
    traceback_text: str | None = None
    context: str = DEFAULT_FAILURE_CONTEXT


def describe_error(
    exc: BaseException,
    *,
    context: str | None = None,
    unknown_message: str = MESSAGE_UNEXPECTED,
) -> ErrorReport:
    """Свести исключение к ответу пользователю и безопасной записи в лог.

    Известные ошибки (:class:`UserFacingError`) отдают своё сообщение и
    причину на уровне ``WARNING``. Всё остальное — общее сообщение и
    ``ERROR`` со стектрейсом, очищенным от секретов и длинных фрагментов.

    :param context: необязательная пометка, о чём сбой («разбора шаблона»).
        ``None`` и пустая строка дают прежнее «обработки видео», поэтому старые
        вызовы и тексты не меняются. Пометка очищается как любой текст лога.
    :param unknown_message: текст ответа для **неизвестного** сбоя. По
        умолчанию :data:`MESSAGE_UNEXPECTED` (про видео); сценарий шаблона
        передаёт :data:`MESSAGE_UNEXPECTED_TEMPLATE`. Известные ошибки
        по-прежнему отдают своё сообщение.
    """
    reason = format_reason(exc)
    failure_context = format_context(context)
    if isinstance(exc, UserFacingError):
        return ErrorReport(
            user_message=safe_user_message(exc),
            reason=reason,
            level=logging.WARNING,
            context=failure_context,
        )
    return ErrorReport(
        user_message=unknown_message,
        reason=reason,
        level=logging.ERROR,
        traceback_text=format_traceback(exc),
        context=failure_context,
    )


def log_failure(
    exc: BaseException,
    *,
    logger: logging.Logger | None = None,
    context: str | None = None,
    unknown_message: str = MESSAGE_UNEXPECTED,
) -> ErrorReport:
    """Записать сбой в лог и вернуть разбор для ответа пользователю.

    Известная ошибка логируется одним сообщением с причиной, неизвестная —
    на уровне ``ERROR`` с очищенным стектрейсом. Ни сообщение, ни лог не
    содержат токенов, ключей API и полного транскрипта.

    :param logger: логгер вызывающего слоя; по умолчанию — ``tldw.errors``.
    :param context: о чём сбой; ``None`` сохраняет прежнее «Сбой обработки
        видео», сценарий шаблона передаёт «разбора шаблона (…, разделов: 2)».
    :param unknown_message: текст ответа для неизвестного сбоя; ``None`` не
        принимается, поэтому общий текст всегда осознан (см.
        :func:`describe_error`).
    """
    report = describe_error(exc, context=context, unknown_message=unknown_message)
    target = log if logger is None else logger
    # Известные ошибки пишутся на WARNING, но при LOG_LEVEL=ERROR этот уровень
    # был бы отброшен и причина исчезла бы из лога. Поэтому уровень поднимается
    # до действующего порога, если он выше: запись остаётся видимой (T-016).
    level = _visible_level(target, report.level)
    if report.traceback_text is None:
        target.log(level, "Сбой %s: %s", report.context, report.reason)
    else:
        target.log(
            level,
            "Неизвестный сбой %s: %s\n%s",
            report.context,
            report.reason,
            report.traceback_text,
        )
    return report


def _visible_level(logger: logging.Logger, level: int) -> int:
    """Поднять уровень записи до порога логгера, чтобы она не потерялась.

    Принятый компромисс (REVIEW-2 T-016): известная пользовательская ошибка при
    ``LOG_LEVEL=ERROR`` пишется как ``ERROR``/``CRITICAL`` и поэтому неотличима
    от настоящего инцидента по уровню — алерты и метрики увидят её наравне с
    неизвестным сбоем. Альтернатива хуже: без подъёма уровня причина известной
    ошибки исчезала из лога совсем, и разбор обращения становился невозможен.
    Отличить её от инцидента можно по тексту записи: у известной ошибки нет
    стектрейса и есть «Сбой …», у неизвестной — «Неизвестный сбой …» со стеком.
    """
    effective = logger.getEffectiveLevel()
    if effective <= level:
        return level
    return min(effective, logging.CRITICAL)


def format_context(context: str | None) -> str:
    """Нормализовать контекст сбоя для записи в лог.

    Пустой или отсутствующий контекст даёт :data:`DEFAULT_FAILURE_CONTEXT`,
    поэтому старые вызовы пишут прежний текст. Остальное очищается как любой
    текст лога (секреты, одна строка, предел длины) и не может добавить
    перевод строки в запись.
    """
    if context is None:
        return DEFAULT_FAILURE_CONTEXT
    text = sanitize_log_text(context, max_length=MAX_CONTEXT_CHARS).strip(" :")
    return text or DEFAULT_FAILURE_CONTEXT


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

    Кадры стека и их строки-исходники сохраняются (включая длинные кадры
    Python 3.11+ с кареткой и многострочным исходником), а всё остальное —
    текст исключения (в том числе многострочный) — схлопывается в одну
    очищенную строку. Кадром считается только строка ``  File "…"`` внутри
    блока ``Traceback …``; отступ в четыре пробела сам по себе признаком кадра
    не является, поэтому транскрипт с отступами не уходит в лог целиком, а
    причина остаётся видимой.

    ``ExceptionGroup`` (Python 3.11+) раньше схлопывался в одну строку и терял
    вложенные исключения. Теперь группа сериализуется деревом: заголовок
    группы и каждый элемент со своим стектрейсом и причиной. Это сохраняет
    структуру и не смешивает разные сбои в одну запись (T-016).
    """
    if isinstance(exc, BaseExceptionGroup):
        text = _format_exception_group(exc, depth=0)
    else:
        text = _format_single_traceback(exc)
    return _truncate_traceback(text)


def _format_single_traceback(exc: BaseException) -> str:
    """Стектрейс одного (не группового) исключения."""
    lines: list[str] = []
    message_parts: list[str] = []

    def flush_message() -> None:
        if message_parts:
            lines.append(sanitize_log_text(" ".join(message_parts)))
            message_parts.clear()

    in_frames = False
    in_frame_body = False
    for line in "".join(traceback.format_exception(exc)).splitlines():
        if line.startswith(_TRACEBACK_HEADER):
            flush_message()
            in_frames, in_frame_body = True, False
            lines.append(_sanitize_traceback_line(line))
        elif in_frames and line.startswith(_FRAME_PREFIX):
            flush_message()
            in_frame_body = True
            lines.append(_sanitize_traceback_line(line))
        elif in_frame_body and line.startswith(_FRAME_BODY_INDENT):
            # Длинный кадр: сохраняем все строки исходника и каретку, а не
            # только первые две — иначе многострочное выражение терялось.
            flush_message()
            lines.append(_sanitize_traceback_line(line))
        elif line.startswith(_EXCEPTION_CHAIN_PREFIXES):
            flush_message()
            in_frames, in_frame_body = False, False
            lines.append(_sanitize_traceback_line(line))
        else:
            in_frames, in_frame_body = False, False
            message_parts.append(line)
    flush_message()

    return "\n".join(lines).strip()


def _format_exception_group(
    group: BaseExceptionGroup,
    *,
    depth: int,
) -> str:
    """Сериализовать ``ExceptionGroup`` деревом, не теряя вложенные ошибки."""
    indent = "  " * depth
    lines = [f"{indent}Exception Group Traceback (most recent call last):"]
    # Внешние кадры места, где группа была поднята: их не видно в тексте
    # вложенных исключений, но без них инцидент не сопоставить с кодом.
    for chunk in traceback.format_tb(group.__traceback__):
        lines.extend(
            f"{indent}{_sanitize_traceback_line(line)}"
            for line in chunk.splitlines()
        )
    lines.append(f"{indent}ExceptionGroup: {sanitize_log_text(str(group))}")
    if depth >= MAX_EXCEPTION_GROUP_DEPTH:
        lines.append(f"{indent}… [вложенность групп обрезана]")
        return "\n".join(lines)
    for index, sub_error in enumerate(group.exceptions, start=1):
        lines.append(f"{indent}+- {index} ----------------")
        if isinstance(sub_error, BaseExceptionGroup):
            lines.append(_format_exception_group(sub_error, depth=depth + 1))
        else:
            body = _format_single_traceback(sub_error)
            prefix = f"{indent}  | "
            lines.extend(
                f"{prefix}{line}" if line else prefix.rstrip()
                for line in body.splitlines()
            )
    return "\n".join(lines)


def _truncate_traceback(text: str) -> str:
    """Ограничить длину итогового стектрейса явным маркером."""
    text = text.strip()
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


def sanitize_log_block(
    value: object,
    *,
    max_length: int = MAX_TRACEBACK_CHARS,
    max_line_length: int = MAX_TRACEBACK_LINE_CHARS,
) -> str:
    """Очистить многострочный блок лога, сохранив строки и структуру.

    Используется централизованным фильтром логирования: стектрейс или
    многострочная запись остаются построчными (иначе нечитаемы), но каждый
    секрет вырезается, длина строки и всего блока ограничены. Поэтому даже
    сторонний логгер с полным транскриптом не запишет его целиком.
    """
    lines: list[str] = []
    for raw_line in str(value).splitlines():
        line = _redact(raw_line.rstrip())
        if len(line) > max_line_length:
            line = f"{line[:max_line_length].rstrip()}… [фрагмент обрезан]"
        lines.append(line)
    text = "\n".join(lines).strip()
    if max_length > 0 and len(text) > max_length:
        text = f"{text[:max_length].rstrip()}\n… [блок обрезан]"
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
    "DEFAULT_FAILURE_CONTEXT",
    "ErrorReport",
    "MAX_CONTEXT_CHARS",
    "MAX_EXCEPTION_GROUP_DEPTH",
    "MAX_LOG_FIELD_CHARS",
    "MAX_TRACEBACK_CHARS",
    "MAX_TRACEBACK_LINE_CHARS",
    "MAX_USER_MESSAGE_CHARS",
    "MESSAGE_AUDIO_TOO_LONG",
    "MESSAGE_UNEXPECTED",
    "MESSAGE_UNEXPECTED_TEMPLATE",
    "REDACTED",
    "TemplateParseError",
    "TemplateValidationError",
    "UserFacingError",
    "describe_error",
    "format_context",
    "format_reason",
    "format_traceback",
    "log_failure",
    "safe_user_message",
    "sanitize_log_block",
    "sanitize_log_text",
]

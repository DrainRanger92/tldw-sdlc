"""Нейтральные текстовые помощники TLDW.

Модуль ничего не импортирует из ``services``/``adapters``/``handlers``
(ADR-002), поэтому его могут использовать все слои и он не создаёт инверсию
зависимостей. Здесь живёт единственная реализация схлопывания пробелов:
до появления модуля ``_one_line`` был скопирован в ``services/note.py``,
``adapters/youtube.py`` и ``adapters/llm.py`` — правило трёх копий
(``app/skills/shared-helpers/SKILL.md``) требует одного источника.

T-016: здесь же разбор ссылки YouTube для лога и адаптера. Обработчик ссылки
не должен импортировать ``adapters`` (ADR-002), но ему нужен безопасный
``video_id``/host для записи об ошибке — без query и полного URL. Один разбор
используют и :func:`tldw.adapters.youtube.parse_video_id`, и обработчик,
поэтому правило «какой id считается video id» не дублируется.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

__all__ = [
    "SHORT_YOUTUBE_HOSTS",
    "VIDEO_ID_PATTERN",
    "YOUTUBE_HOSTS",
    "one_line",
    "safe_url_host",
    "video_id_from_url",
]

#: Хосты, у которых id видео — первый сегмент пути (``youtu.be/<id>``).
SHORT_YOUTUBE_HOSTS: frozenset[str] = frozenset({"youtu.be", "www.youtu.be"})
#: Хосты YouTube с путями ``/watch?v=``, ``/shorts/<id>``, ``/embed/<id>``, ``/live/<id>``.
YOUTUBE_HOSTS: frozenset[str] = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)
#: Идентификатор видео YouTube: ровно 11 символов URL-safe base64.
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_VIDEO_PATH_PATTERN = re.compile(r"^/(?:shorts|embed|live)/([^/]+)/?$")
_SHORT_PATH_PATTERN = re.compile(r"^/([^/]+)/?$")


def one_line(value: str) -> str:
    """Схлопнуть все пробельные последовательности в один пробел.

    Переводы строк и табуляции становятся пробелами, поэтому результат всегда
    занимает одну строку и годится для заголовков, полей YAML и записей в лог.
    """
    return " ".join(value.split())


def video_id_from_url(url: str, *, strict: bool = False) -> str | None:
    """Безопасно извлечь video id из ссылки YouTube (``None`` — не вышло).

    По умолчанию функция не бросает исключение: она нужна для записи в лог,
    где сбой разбора не должен подменять исходную ошибку. Поддерживаются те же
    формы, что у ``YouTubeAdapter``: ``youtube.com/watch?v=…`` (включая поддомены),
    ``youtu.be/…``, ``/shorts/``, ``/embed/`` и ``/live/``.

    Возвращается только 11-символьный id; query-строка и полный URL наружу не
    попадают.

    :param strict: пробросить ``ValueError`` разбора URL вместо ``None``. Так
        адаптер сохраняет цепочку ``raise … from exc`` для битой ссылки; для
        лога (по умолчанию) ошибка по-прежнему гасится.
    """
    if not isinstance(url, str):
        return None
    raw = url.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw.lstrip('/')}"
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
    except ValueError:
        if strict:
            raise
        return None
    if parts.scheme.lower() not in {"http", "https"}:
        return None

    candidate = ""
    if host in SHORT_YOUTUBE_HOSTS:
        match = _SHORT_PATH_PATTERN.fullmatch(parts.path)
        candidate = match.group(1) if match else ""
    elif host in YOUTUBE_HOSTS:
        if parts.path.rstrip("/") == "/watch":
            candidate = (parse_qs(parts.query).get("v") or [""])[0]
        else:
            match = _VIDEO_PATH_PATTERN.fullmatch(parts.path)
            candidate = match.group(1) if match else ""
    if not VIDEO_ID_PATTERN.fullmatch(candidate):
        return None
    return candidate


def safe_url_host(url: str) -> str | None:
    """Вернуть только host ссылки для лога: без scheme, query и пути."""
    if not isinstance(url, str):
        return None
    raw = url.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw.lstrip('/')}"
    try:
        host = urlsplit(raw).hostname
    except ValueError:
        return None
    return host.lower() if host else None

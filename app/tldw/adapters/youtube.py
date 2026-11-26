"""Адаптер YouTube: разбор ссылки, метаданные и транскрипт.

Модуль — единственная граница внешних вызовов YouTube (ADR-002): сервисы и
обработчики не импортируют ``youtube_transcript_api`` и ``yt_dlp`` напрямую.
Порядок получения данных:

1. ссылка проверяется и превращается в video id (:func:`parse_video_id`);
2. транскрипт берётся через ``youtube-transcript-api`` (основной источник);
3. если основной источник не дал субтитров или упал по сети/блокировке —
   включается fallback через ``yt-dlp``;
4. метаданные (title, channel, duration) читаются через ``yt-dlp`` и считаются
   необязательными: без них видео всё равно можно законспектировать.

Все внешние библиотеки спрятаны за узкими протоколами :class:`TranscriptClient`
и :class:`FallbackClient`, поэтому тесты подменяют их фейками и не ходят в
сеть. Адаптер синхронный: вызывающий слой уводит его в отдельный поток, чтобы
не блокировать event loop Telegram-бота.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol
from urllib.parse import parse_qs, urlsplit

from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)
from yt_dlp import YoutubeDL

from tldw.errors import UserFacingError

log = logging.getLogger(__name__)

#: Языки субтитров в порядке предпочтения: сначала оригинал, затем английский.
DEFAULT_LANGUAGES: Final[tuple[str, ...]] = ("ru", "en")
#: Основной источник субтитров — библиотека youtube-transcript-api.
SOURCE_TRANSCRIPT_API: Final = "youtube-transcript-api"
#: Резервный источник — субтитры, скачанные yt-dlp.
SOURCE_YTDLP: Final = "yt-dlp"
#: Шаблон ссылки для yt-dlp: библиотека работает с URL, а не с голым id.
WATCH_URL_TEMPLATE: Final = "https://www.youtube.com/watch?v={video_id}"
#: Единственный формат субтитров, который разбирает fallback (его отдаёт YouTube).
CAPTION_FORMAT: Final = "json3"

MESSAGE_NOT_YOUTUBE: Final = (
    "Это не ссылка на видео YouTube. Пришлите ссылку на ролик YouTube."
)
MESSAGE_NO_TRANSCRIPT: Final = (
    "У этого видео нет субтитров, поэтому конспект сделать не получится."
)
MESSAGE_NETWORK: Final = "Не удалось получить данные с YouTube. Попробуйте позже."
MESSAGE_VIDEO_UNAVAILABLE: Final = "Видео недоступно: возможно, оно удалено или закрыто."

_YOUTUBE_HOSTS: Final[frozenset[str]] = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)
_SHORT_HOSTS: Final[frozenset[str]] = frozenset({"youtu.be", "www.youtu.be"})
#: Идентификатор видео YouTube: ровно 11 символов URL-safe base64.
_VIDEO_ID_RE: Final = re.compile(r"^[A-Za-z0-9_-]{11}$")
#: Пути вида /shorts/<id>, /embed/<id>, /live/<id>.
_VIDEO_PATH_RE: Final = re.compile(r"^/(?:shorts|embed|live)/([^/]+)/?$")
_SHORT_PATH_RE: Final = re.compile(r"^/([^/]+)/?$")
_WATCH_PATH: Final = "/watch"


class YouTubeAdapterError(UserFacingError):
    """Базовая ошибка адаптера YouTube.

    Наследуется от :class:`tldw.errors.UserFacingError`: сообщение каждого
    подкласса написано для пользователя, а обработчик отличает такую ошибку
    от неизвестного сбоя и не показывает стектрейс.
    """


class NotYouTubeURLError(YouTubeAdapterError):
    """Ссылка ведёт не на видео YouTube или id в ней некорректен."""

    def __init__(self, message: str = MESSAGE_NOT_YOUTUBE) -> None:
        super().__init__(message)


class NoTranscriptError(YouTubeAdapterError):
    """У видео нет субтитров — ни в основном источнике, ни в fallback."""

    def __init__(self, message: str = MESSAGE_NO_TRANSCRIPT) -> None:
        super().__init__(message)


class YouTubeNetworkError(YouTubeAdapterError):
    """Сбой запроса к YouTube: сеть, блокировка, таймаут или битый ответ."""

    def __init__(self, message: str = MESSAGE_NETWORK) -> None:
        super().__init__(message)


class VideoUnavailableError(YouTubeNetworkError):
    """Видео удалено, приватное или недоступно для просмотра."""

    def __init__(self, message: str = MESSAGE_VIDEO_UNAVAILABLE) -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """Фрагмент транскрипта: текст и таймкод начала в секундах."""

    text: str
    start_seconds: float
    duration_seconds: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _one_line(_text(self.text)))
        object.__setattr__(self, "start_seconds", _seconds(self.start_seconds, "start_seconds"))
        object.__setattr__(
            self, "duration_seconds", _seconds(self.duration_seconds, "duration_seconds")
        )


@dataclass(frozen=True, slots=True)
class Transcript:
    """Транскрипт видео: сегменты с таймкодами и источник, из которого он получен."""

    segments: tuple[TranscriptSegment, ...] = ()
    language: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "segments", _normalize_segments(self.segments))
        object.__setattr__(self, "language", _text(self.language))
        object.__setattr__(self, "source", _text(self.source))

    @property
    def text(self) -> str:
        """Склеенный текст транскрипта без таймкодов."""
        return " ".join(segment.text for segment in self.segments)

    @property
    def is_empty(self) -> bool:
        """Нет ни одного непустого сегмента."""
        return not self.segments


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    """Метаданные видео; недоступные поля остаются пустыми/``None``."""

    video_id: str
    title: str = ""
    channel: str = ""
    duration_seconds: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "video_id", _text(self.video_id))
        object.__setattr__(self, "title", _one_line(_text(self.title)))
        object.__setattr__(self, "channel", _one_line(_text(self.channel)))
        duration = self.duration_seconds
        if duration is None:
            return
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise TypeError("duration_seconds должен быть целым числом секунд или None")
        if duration < 0:
            raise ValueError("duration_seconds не может быть отрицательным")


@dataclass(frozen=True, slots=True)
class Video:
    """Видео YouTube: исходная ссылка, метаданные и транскрипт."""

    url: str
    metadata: VideoMetadata
    transcript: Transcript

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", _text(self.url))
        if not isinstance(self.metadata, VideoMetadata):
            raise TypeError("metadata должен быть VideoMetadata")
        if not isinstance(self.transcript, Transcript):
            raise TypeError("transcript должен быть Transcript")

    @property
    def video_id(self) -> str:
        """Идентификатор видео, извлечённый из ссылки."""
        return self.metadata.video_id


class TranscriptClient(Protocol):
    """Основной источник транскрипта (``youtube-transcript-api``)."""

    def fetch(self, video_id: str) -> Transcript:
        """Вернуть транскрипт или поднять :class:`YouTubeAdapterError`."""


class FallbackClient(Protocol):
    """Резервный источник (``yt-dlp``): метаданные и субтитры."""

    def fetch_metadata(self, video_id: str) -> VideoMetadata:
        """Вернуть метаданные видео или поднять :class:`YouTubeAdapterError`."""

    def fetch_transcript(self, video_id: str) -> Transcript:
        """Вернуть транскрипт или поднять :class:`YouTubeAdapterError`."""


def parse_video_id(url: str) -> str:
    """Извлечь video id из поддерживаемой ссылки YouTube.

    Поддерживаются ``youtube.com/watch?v=…`` (включая ``m.``/``music.``
    поддомены), ``youtu.be/…``, ``youtube.com/shorts/…``, а также ``/embed/``
    и ``/live/``. Ссылку можно присылать без схемы — она дополнится ``https``.

    :raises NotYouTubeURLError: если ссылка ведёт не на YouTube или id в ней
        не похож на идентификатор видео.
    """
    if not isinstance(url, str):
        raise NotYouTubeURLError(MESSAGE_NOT_YOUTUBE)

    raw = url.strip()
    if not raw:
        raise NotYouTubeURLError(MESSAGE_NOT_YOUTUBE)
    if "://" not in raw:
        raw = f"https://{raw.lstrip('/')}"

    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
    except ValueError as exc:
        raise NotYouTubeURLError(MESSAGE_NOT_YOUTUBE) from exc

    if parts.scheme.lower() not in {"http", "https"}:
        raise NotYouTubeURLError(MESSAGE_NOT_YOUTUBE)

    candidate = ""
    if host in _SHORT_HOSTS:
        match = _SHORT_PATH_RE.fullmatch(parts.path)
        candidate = match.group(1) if match else ""
    elif host in _YOUTUBE_HOSTS:
        if parts.path.rstrip("/") == _WATCH_PATH:
            candidate = (parse_qs(parts.query).get("v") or [""])[0]
        else:
            match = _VIDEO_PATH_RE.fullmatch(parts.path)
            candidate = match.group(1) if match else ""

    if not _VIDEO_ID_RE.fullmatch(candidate):
        raise NotYouTubeURLError(MESSAGE_NOT_YOUTUBE)
    return candidate


class TranscriptApiClient:
    """Клиент ``youtube-transcript-api`` — основной источник субтитров."""

    def __init__(
        self,
        *,
        languages: Sequence[str] = DEFAULT_LANGUAGES,
        api: object | None = None,
    ) -> None:
        self.languages = tuple(languages)
        self._api = api

    def fetch(self, video_id: str) -> Transcript:
        """Получить транскрипт и перевести ошибки библиотеки в типизированные."""
        api = self._api if self._api is not None else YouTubeTranscriptApi()
        try:
            fetched = api.fetch(video_id, languages=list(self.languages))
        except (TranscriptsDisabled, NoTranscriptFound) as exc:
            raise NoTranscriptError(MESSAGE_NO_TRANSCRIPT) from exc
        except VideoUnavailable as exc:
            raise VideoUnavailableError(MESSAGE_VIDEO_UNAVAILABLE) from exc
        except Exception as exc:  # сеть, блокировка IP, битый ответ YouTube
            raise YouTubeNetworkError(MESSAGE_NETWORK) from exc

        segments = tuple(
            TranscriptSegment(
                text=snippet.text,
                start_seconds=snippet.start,
                duration_seconds=snippet.duration,
            )
            for snippet in fetched
        )
        transcript = Transcript(
            segments=segments,
            language=_text(getattr(fetched, "language_code", "")),
            source=SOURCE_TRANSCRIPT_API,
        )
        if transcript.is_empty:
            raise NoTranscriptError(MESSAGE_NO_TRANSCRIPT)
        return transcript


class YtDlpClient:
    """Клиент ``yt-dlp``: метаданные и fallback-транскрипт из субтитров."""

    def __init__(
        self,
        *,
        languages: Sequence[str] = DEFAULT_LANGUAGES,
        socket_timeout: float = 15.0,
    ) -> None:
        self.languages = tuple(languages)
        self.socket_timeout = socket_timeout

    def fetch_metadata(self, video_id: str) -> VideoMetadata:
        """Прочитать title, channel и duration (в секундах), если они есть."""
        info = self._extract_info(video_id)
        return VideoMetadata(
            video_id=video_id,
            title=_text(info.get("title")),
            channel=_text(info.get("channel") or info.get("uploader")),
            duration_seconds=_coerce_duration(info.get("duration")),
        )

    def fetch_transcript(self, video_id: str) -> Transcript:
        """Скачать субтитры через yt-dlp и разобрать их в сегменты."""
        info = self._extract_info(video_id)
        tracks = _caption_tracks(info)
        if not tracks:
            raise NoTranscriptError(MESSAGE_NO_TRANSCRIPT)

        language, formats = _pick_caption_track(tracks, self.languages)
        caption = _pick_caption_format(formats)
        if caption is None:
            raise NoTranscriptError(MESSAGE_NO_TRANSCRIPT)

        payload = self._read_caption(_text(caption.get("url")))
        segments = _parse_json3(payload)
        if not segments:
            raise NoTranscriptError(MESSAGE_NO_TRANSCRIPT)
        return Transcript(segments=segments, language=language, source=SOURCE_YTDLP)

    def _extract_info(self, video_id: str) -> dict[str, Any]:
        try:
            with YoutubeDL(self._options()) as ydl:
                info = ydl.extract_info(
                    WATCH_URL_TEMPLATE.format(video_id=video_id), download=False
                )
        except Exception as exc:  # DownloadError, сеть, блокировка, таймаут
            raise YouTubeNetworkError(MESSAGE_NETWORK) from exc

        if not isinstance(info, dict):
            raise YouTubeNetworkError(MESSAGE_NETWORK)
        entries = info.get("entries")
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            return entries[0]  # noplaylist не должен пропускать плейлисты, но подстрахуемся
        return info

    def _read_caption(self, url: str) -> bytes | str:
        if not url:
            raise NoTranscriptError(MESSAGE_NO_TRANSCRIPT)
        try:
            with YoutubeDL(self._options()) as ydl:
                return ydl.urlopen(url).read()
        except Exception as exc:
            raise YouTubeNetworkError(MESSAGE_NETWORK) from exc

    def _options(self) -> dict[str, Any]:
        return {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "socket_timeout": self.socket_timeout,
        }


class YouTubeAdapter:
    """Фасад адаптера: ссылка → видео с метаданными и транскриптом.

    Основной источник транскрипта — :class:`TranscriptApiClient`, резервный
    (yt-dlp) — :class:`YtDlpClient`. Оба клиента можно подменить фейками.
    Если основной источник не сработал, fallback пробуется при любом
    :class:`YouTubeAdapterError`; когда оба сообщают «нет субтитров», наружу
    выходит :class:`NoTranscriptError`, иначе — сетевая ошибка.
    """

    def __init__(
        self,
        transcript_client: TranscriptClient | None = None,
        fallback_client: FallbackClient | None = None,
        *,
        languages: Sequence[str] = DEFAULT_LANGUAGES,
    ) -> None:
        self._transcript_client = (
            transcript_client
            if transcript_client is not None
            else TranscriptApiClient(languages=languages)
        )
        self._fallback_client = (
            fallback_client if fallback_client is not None else YtDlpClient(languages=languages)
        )

    def fetch(self, url: str) -> Video:
        """Проверить ссылку и вернуть видео с транскриптом и метаданными.

        :raises NotYouTubeURLError: ссылка не на YouTube.
        :raises NoTranscriptError: у видео нет субтитров в обоих источниках.
        :raises YouTubeNetworkError: YouTube не отвечает или видео недоступно.
        """
        video_id = parse_video_id(url)
        transcript = self.fetch_transcript(video_id)
        metadata = self.fetch_metadata(video_id)
        return Video(url=url.strip(), metadata=metadata, transcript=transcript)

    def fetch_transcript(self, video_id: str) -> Transcript:
        """Взять транскрипт из основного источника, при сбое — из yt-dlp."""
        try:
            transcript = self._transcript_client.fetch(video_id)
            if not transcript.is_empty:
                return transcript
            primary_error: YouTubeAdapterError = NoTranscriptError(MESSAGE_NO_TRANSCRIPT)
        except YouTubeAdapterError as exc:
            primary_error = exc

        log.warning(
            "Основной источник субтитров не сработал (%s), пробуем yt-dlp",
            type(primary_error).__name__,
        )
        try:
            transcript = self._fallback_client.fetch_transcript(video_id)
            if not transcript.is_empty:
                return transcript
            fallback_error: YouTubeAdapterError = NoTranscriptError(MESSAGE_NO_TRANSCRIPT)
        except YouTubeAdapterError as exc:
            fallback_error = exc

        raise _transcript_error(primary_error, fallback_error) from fallback_error

    def fetch_metadata(self, video_id: str) -> VideoMetadata:
        """Прочитать метаданные; сбой не мешает обработать транскрипт."""
        try:
            return self._fallback_client.fetch_metadata(video_id)
        except YouTubeAdapterError as exc:
            log.warning("Метаданные видео недоступны (%s)", type(exc).__name__)
            return VideoMetadata(video_id=video_id)


def _transcript_error(
    primary_error: YouTubeAdapterError,
    fallback_error: YouTubeAdapterError,
) -> YouTubeAdapterError:
    if isinstance(primary_error, NoTranscriptError) or isinstance(
        fallback_error, NoTranscriptError
    ):
        return NoTranscriptError(MESSAGE_NO_TRANSCRIPT)
    if isinstance(fallback_error, VideoUnavailableError):
        return VideoUnavailableError(MESSAGE_VIDEO_UNAVAILABLE)
    return YouTubeNetworkError(MESSAGE_NETWORK)


def _caption_tracks(info: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Собрать дорожки субтитров: ручные перекрывают автоматические."""
    tracks: dict[str, list[dict[str, Any]]] = {}
    for key in ("automatic_captions", "subtitles"):
        raw = info.get(key)
        if not isinstance(raw, dict):
            continue
        for language, formats in raw.items():
            if not isinstance(language, str) or not isinstance(formats, list):
                continue
            valid = [fmt for fmt in formats if isinstance(fmt, dict) and fmt.get("url")]
            if valid:
                tracks[language] = valid
    return tracks


def _pick_caption_track(
    tracks: Mapping[str, list[dict[str, Any]]],
    languages: Sequence[str],
) -> tuple[str, list[dict[str, Any]]]:
    """Выбрать дорожку по предпочтению языков, иначе — первую доступную."""
    for preferred in languages:
        for language, formats in tracks.items():
            if language == preferred or language.startswith(f"{preferred}-"):
                return language, formats
    language = next(iter(tracks))
    return language, tracks[language]


def _pick_caption_format(formats: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for caption in formats:
        if _text(caption.get("ext")).lower() == CAPTION_FORMAT:
            return caption
    return None


def _parse_json3(payload: bytes | str) -> tuple[TranscriptSegment, ...]:
    """Разобрать субтитры YouTube в формате json3 (мс → секунды)."""
    try:
        data = json.loads(_decode(payload))
    except json.JSONDecodeError as exc:
        raise NoTranscriptError(MESSAGE_NO_TRANSCRIPT) from exc

    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return ()

    segments: list[TranscriptSegment] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        parts = event.get("segs")
        if not isinstance(parts, list):
            continue
        text = _one_line(
            "".join(_raw_text(part.get("utf8")) for part in parts if isinstance(part, dict))
        )
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                text=text,
                start_seconds=_milliseconds(event.get("tStartMs")),
                duration_seconds=_milliseconds(event.get("dDurationMs")),
            )
        )
    return tuple(segments)


def _normalize_segments(segments: Iterable[TranscriptSegment]) -> tuple[TranscriptSegment, ...]:
    normalized: list[TranscriptSegment] = []
    for segment in segments:
        if not isinstance(segment, TranscriptSegment):
            raise TypeError("сегмент транскрипта должен быть TranscriptSegment")
        if segment.text:
            normalized.append(segment)
    return tuple(normalized)


def _seconds(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} должен быть числом секунд")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} должен быть конечным числом")
    if number < 0:
        raise ValueError(f"{field_name} не может быть отрицательным")
    return number


def _milliseconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return 0.0
    return number / 1000.0


def _coerce_duration(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return int(round(number))


def _decode(payload: bytes | str) -> str:
    if isinstance(payload, str):
        return payload
    return payload.decode("utf-8", errors="replace")


def _raw_text(value: object) -> str:
    """Сырой текст без обрезки: пробелы между фрагментами json3 значимы."""
    return "" if value is None else str(value)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _one_line(value: str) -> str:
    return " ".join(value.split())


__all__ = [
    "DEFAULT_LANGUAGES",
    "FallbackClient",
    "NoTranscriptError",
    "NotYouTubeURLError",
    "SOURCE_TRANSCRIPT_API",
    "SOURCE_YTDLP",
    "Transcript",
    "TranscriptApiClient",
    "TranscriptClient",
    "TranscriptSegment",
    "Video",
    "VideoMetadata",
    "VideoUnavailableError",
    "YouTubeAdapter",
    "YouTubeAdapterError",
    "YouTubeNetworkError",
    "YtDlpClient",
    "parse_video_id",
]

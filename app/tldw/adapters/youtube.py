"""Адаптер YouTube: разбор ссылки, метаданные и транскрипт.

Модуль — единственная граница внешних вызовов YouTube (ADR-002): сервисы и
обработчики не импортируют ``youtube_transcript_api`` и ``yt_dlp`` напрямую.
Порядок получения данных:

1. ссылка проверяется и превращается в video id (:func:`parse_video_id`);
2. транскрипт берётся через ``youtube-transcript-api`` (основной источник) с
   явным таймаутом: сессия передаётся библиотеке как ``http_client``;
3. если основной источник не дал субтитров или упал по сети/блокировке —
   включается fallback через ``yt-dlp``;
4. метаданные (title, channel, duration) читаются через ``yt-dlp`` и считаются
   необязательными: без них видео всё равно можно законспектировать.
   Информация о видео извлекается один раз: если транскрипт пришёл из
   fallback, метаданные берутся из того же ответа ``yt-dlp``.

Все внешние библиотеки спрятаны за узкими протоколами :class:`TranscriptClient`
и :class:`FallbackClient`, поэтому тесты подменяют их фейками и не ходят в
сеть. Адаптер синхронный: вызывающий слой уводит его в отдельный поток, чтобы
не блокировать event loop Telegram-бота.

``requests`` — зависимость ``youtube-transcript-api`` 1.x: библиотека принимает
готовую сессию ``http_client`` и не имеет отдельного параметра таймаута,
поэтому своей строки в ``pyproject.toml`` адаптер не требует.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

# requests — жёсткая зависимость youtube-transcript-api 1.x: библиотека
# принимает её сессию в ``http_client``. Отдельной строки в pyproject нет.
from requests import Response, Session
from requests.exceptions import Timeout
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)
from yt_dlp import DownloadError, YoutubeDL

from tldw.errors import UserFacingError
from tldw.text import one_line, video_id_from_url

log = logging.getLogger(__name__)

#: Языки субтитров в порядке предпочтения: сначала оригинал, затем английский.
DEFAULT_LANGUAGES: Final[tuple[str, ...]] = ("ru", "en")
#: Таймаут запросов основного источника (секунды): зависший YouTube не должен
#: держать поток ``asyncio.to_thread`` бесконечно.
DEFAULT_TRANSCRIPT_TIMEOUT: Final[float] = 10.0
#: Основной источник субтитров — библиотека youtube-transcript-api.
SOURCE_TRANSCRIPT_API: Final = "youtube-transcript-api"
#: Резервный источник — субтитры, скачанные yt-dlp.
SOURCE_YTDLP: Final = "yt-dlp"
#: Признаки недоступности видео в тексте ``DownloadError`` от yt-dlp: у
#: библиотеки нет отдельных классов для private/unavailable.
_UNAVAILABLE_DOWNLOAD_MARKERS: Final[tuple[str, ...]] = (
    "private video",
    "video is private",
    "video unavailable",
    "this video is unavailable",
    "this video has been removed",
    "removed by the uploader",
    "no longer available",
    "account associated with this video has been terminated",
    "members-only",
    "join this channel",
)
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
        object.__setattr__(self, "text", one_line(_text(self.text)))
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
        object.__setattr__(self, "title", one_line(_text(self.title)))
        object.__setattr__(self, "channel", one_line(_text(self.channel)))
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


@dataclass(frozen=True, slots=True)
class FallbackVideo:
    """Итог одного обращения к ``yt-dlp``: транскрипт и метаданные видео."""

    transcript: Transcript
    metadata: VideoMetadata

    def __post_init__(self) -> None:
        if not isinstance(self.transcript, Transcript):
            raise TypeError("transcript должен быть Transcript")
        if not isinstance(self.metadata, VideoMetadata):
            raise TypeError("metadata должен быть VideoMetadata")


class FallbackClient(Protocol):
    """Резервный источник (``yt-dlp``): метаданные и субтитры."""

    def fetch_metadata(self, video_id: str) -> VideoMetadata:
        """Вернуть метаданные видео или поднять :class:`YouTubeAdapterError`."""

    def fetch(self, video_id: str) -> FallbackVideo:
        """Один запрос к ``yt-dlp``: транскрипт и метаданные видео.

        Метаданные строятся из того же ответа, что и субтитры, поэтому
        вызывающему не нужен второй полный ``_extract_info``.
        """


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

    try:
        video_id = video_id_from_url(url, strict=True)
    except ValueError as exc:
        raise NotYouTubeURLError(MESSAGE_NOT_YOUTUBE) from exc
    if video_id is None:
        raise NotYouTubeURLError(MESSAGE_NOT_YOUTUBE)
    return video_id


class _TimeoutSession(Session):
    """``requests.Session`` с таймаутом по умолчанию для каждого запроса.

    ``youtube-transcript-api`` 1.x не принимает таймаут в ``fetch()``, но
    принимает готовую сессию в ``http_client``. Без такого таймаута зависший
    ответ YouTube держал бы поток ``asyncio.to_thread`` бесконечно.
    """

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self.timeout = _timeout_seconds(timeout)

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        kwargs.setdefault("timeout", self.timeout)
        return super().request(method, url, **kwargs)


class TranscriptApiClient:
    """Клиент ``youtube-transcript-api`` — основной источник субтитров."""

    def __init__(
        self,
        *,
        languages: Sequence[str] = DEFAULT_LANGUAGES,
        api: object | None = None,
        timeout: float = DEFAULT_TRANSCRIPT_TIMEOUT,
    ) -> None:
        self.languages = tuple(languages)
        self.timeout = _timeout_seconds(timeout)
        self._api = api

    def fetch(self, video_id: str) -> Transcript:
        """Получить транскрипт и перевести ошибки библиотеки в типизированные."""
        api = self._api if self._api is not None else self._make_api()
        try:
            fetched = api.fetch(video_id, languages=list(self.languages))
        except (TranscriptsDisabled, NoTranscriptFound) as exc:
            raise NoTranscriptError(MESSAGE_NO_TRANSCRIPT) from exc
        except VideoUnavailable as exc:
            raise VideoUnavailableError(MESSAGE_VIDEO_UNAVAILABLE) from exc
        except Timeout as exc:  # явный таймаут сессии
            raise YouTubeNetworkError(MESSAGE_NETWORK) from exc
        except Exception as exc:  # сеть, блокировка IP, битый ответ YouTube
            raise YouTubeNetworkError(MESSAGE_NETWORK) from exc

        segments = _segments_from_fetched(fetched)
        transcript = Transcript(
            segments=segments,
            language=_text(getattr(fetched, "language_code", "")),
            source=SOURCE_TRANSCRIPT_API,
        )
        if transcript.is_empty:
            raise NoTranscriptError(MESSAGE_NO_TRANSCRIPT)
        return transcript

    def _make_api(self) -> object:
        """Создать клиент библиотеки с сессией, у которой есть таймаут."""
        return YouTubeTranscriptApi(http_client=_TimeoutSession(self.timeout))


class YtDlpClient:
    """Клиент ``yt-dlp``: метаданные и fallback-транскрипт из субтитров."""

    def __init__(
        self,
        *,
        languages: Sequence[str] = DEFAULT_LANGUAGES,
        socket_timeout: float = 15.0,
    ) -> None:
        self.languages = tuple(languages)
        self.socket_timeout = _timeout_seconds(socket_timeout, "socket_timeout")

    def fetch_metadata(self, video_id: str) -> VideoMetadata:
        """Прочитать title, channel и duration (в секундах), если они есть."""
        return self._metadata(video_id, self._extract_info(video_id))

    def fetch(self, video_id: str) -> FallbackVideo:
        """Скачать субтитры и прочитать метаданные за одно обращение к yt-dlp."""
        info = self._extract_info(video_id)
        metadata = self._metadata(video_id, info)
        return FallbackVideo(transcript=self._transcript(info), metadata=metadata)

    def _metadata(self, video_id: str, info: Mapping[str, Any]) -> VideoMetadata:
        return VideoMetadata(
            video_id=video_id,
            title=_text(info.get("title")),
            channel=_text(info.get("channel") or info.get("uploader")),
            duration_seconds=_coerce_duration(info.get("duration")),
        )

    def _transcript(self, info: Mapping[str, Any]) -> Transcript:
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
        except DownloadError as exc:
            raise _download_error(exc) from exc
        except Exception as exc:  # сеть, блокировка, таймаут
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
            # Данные приложения живут только в DATA_DIR; кэш yt-dlp по
            # умолчанию писал бы в ~/.cache/yt-dlp — отключаем его.
            "cachedir": False,
        }


class YouTubeAdapter:
    """Фасад адаптера: ссылка → видео с метаданными и транскриптом.

    Основной источник транскрипта — :class:`TranscriptApiClient`, резервный
    (yt-dlp) — :class:`YtDlpClient`. Оба клиента можно подменить фейками.
    Если основной источник не сработал, fallback пробуется при любом
    :class:`YouTubeAdapterError`. За один :meth:`fetch` ``yt-dlp`` извлекает
    информацию о видео не больше одного раза: транскрипт и метаданные
    приходят из одного ответа.
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
        :raises VideoUnavailableError: видео удалено, приватное или закрыто.
        :raises YouTubeNetworkError: YouTube не отвечает или ответил не так.
        """
        video_id = parse_video_id(url)
        transcript, metadata = self._fetch_content(video_id)
        return Video(url=url.strip(), metadata=metadata, transcript=transcript)

    def _fetch_content(self, video_id: str) -> tuple[Transcript, VideoMetadata]:
        """Взять транскрипт из основного источника, при сбое — из yt-dlp."""
        try:
            transcript = self._transcript_client.fetch(video_id)
            if transcript.is_empty:
                raise NoTranscriptError(MESSAGE_NO_TRANSCRIPT)
        except YouTubeAdapterError as primary_error:
            log.warning(
                "Основной источник субтитров не сработал (%s), пробуем yt-dlp",
                type(primary_error).__name__,
            )
            return self._fetch_fallback(video_id, primary_error)
        return transcript, self.fetch_metadata(video_id)

    def _fetch_fallback(
        self,
        video_id: str,
        primary_error: YouTubeAdapterError,
    ) -> tuple[Transcript, VideoMetadata]:
        try:
            fallback_video = self._fallback_client.fetch(video_id)
            if not fallback_video.transcript.is_empty:
                return fallback_video.transcript, fallback_video.metadata
            fallback_error: YouTubeAdapterError = NoTranscriptError(MESSAGE_NO_TRANSCRIPT)
        except YouTubeAdapterError as exc:
            fallback_error = exc

        final_error, cause = _transcript_error(primary_error, fallback_error)
        raise final_error from cause

    def fetch_metadata(self, video_id: str) -> VideoMetadata:
        """Прочитать метаданные; сбой не мешает обработать транскрипт."""
        try:
            return self._fallback_client.fetch_metadata(video_id)
        except YouTubeAdapterError as exc:
            log.warning("Метаданные видео недоступны (%s)", type(exc).__name__)
            return VideoMetadata(video_id=video_id)


def _segments_from_fetched(fetched: object) -> tuple[TranscriptSegment, ...]:
    """Нормализовать ответ ``youtube-transcript-api`` в сегменты.

    Битый ответ библиотеки (``None``, dict старого формата 0.x, строка)
    считается сбоем источника и даёт :class:`YouTubeNetworkError`, а не
    ``TypeError``/``AttributeError`` наружу.
    """
    if fetched is None or isinstance(fetched, str | bytes | Mapping):
        raise YouTubeNetworkError(MESSAGE_NETWORK)
    try:
        snippets = list(fetched)
    except TypeError as exc:
        raise YouTubeNetworkError(MESSAGE_NETWORK) from exc

    segments: list[TranscriptSegment] = []
    for snippet in snippets:
        if isinstance(snippet, Mapping):
            raise YouTubeNetworkError(MESSAGE_NETWORK)
        text = getattr(snippet, "text", None)
        if not isinstance(text, str):
            raise YouTubeNetworkError(MESSAGE_NETWORK)
        try:
            segments.append(
                TranscriptSegment(
                    text=text,
                    start_seconds=getattr(snippet, "start", None),
                    duration_seconds=getattr(snippet, "duration", 0.0),
                )
            )
        except (TypeError, ValueError) as exc:
            raise YouTubeNetworkError(MESSAGE_NETWORK) from exc
    return tuple(segments)


def _download_error(exc: Exception) -> YouTubeNetworkError:
    """Перевести ``DownloadError`` yt-dlp в типизированную ошибку.

    У yt-dlp нет отдельных классов для private/unavailable, поэтому признак
    ищется в тексте; сам текст в лог не попадает.
    """
    message = str(exc).lower()
    if any(marker in message for marker in _UNAVAILABLE_DOWNLOAD_MARKERS):
        return VideoUnavailableError(MESSAGE_VIDEO_UNAVAILABLE)
    return YouTubeNetworkError(MESSAGE_NETWORK)


def _timeout_seconds(value: object, field_name: str = "timeout") -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} должен быть числом секунд")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} должен быть конечным числом")
    if number <= 0:
        raise ValueError(f"{field_name} должен быть положительным")
    return number


def _transcript_error(
    primary_error: YouTubeAdapterError,
    fallback_error: YouTubeAdapterError,
) -> tuple[YouTubeAdapterError, YouTubeAdapterError]:
    """Выбрать итоговую ошибку и её причину для ``raise ... from``.

    Приоритет по ADR-004: «видео недоступно» важнее «нет субтитров», а
    «нет субтитров» — важнее сети. Причина — ошибка того источника, который
    определил итог: ``from fallback_error`` при победе primary вводил бы в
    заблуждение.
    """
    for error in (primary_error, fallback_error):
        if isinstance(error, VideoUnavailableError):
            return VideoUnavailableError(MESSAGE_VIDEO_UNAVAILABLE), error
    for error in (primary_error, fallback_error):
        if isinstance(error, NoTranscriptError):
            return NoTranscriptError(MESSAGE_NO_TRANSCRIPT), error
    return YouTubeNetworkError(MESSAGE_NETWORK), fallback_error


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
        text = one_line(
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


__all__ = [
    "DEFAULT_LANGUAGES",
    "DEFAULT_TRANSCRIPT_TIMEOUT",
    "FallbackClient",
    "FallbackVideo",
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

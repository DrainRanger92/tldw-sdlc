"""Адаптеры внешних систем: YouTube и LLM.

Каждый внешний вызов изолирован здесь, чтобы сервисы и тесты могли
подменять его фейковой реализацией без сети и токенов.
"""

from .youtube import (
    DEFAULT_LANGUAGES,
    SOURCE_TRANSCRIPT_API,
    SOURCE_YTDLP,
    FallbackClient,
    NoTranscriptError,
    NotYouTubeURLError,
    Transcript,
    TranscriptApiClient,
    TranscriptClient,
    TranscriptSegment,
    Video,
    VideoMetadata,
    VideoUnavailableError,
    YouTubeAdapter,
    YouTubeAdapterError,
    YouTubeNetworkError,
    YtDlpClient,
    parse_video_id,
)

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

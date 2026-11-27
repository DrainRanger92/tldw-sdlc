"""Офлайновые тесты адаптера YouTube: ссылки, транскрипт, fallback и ошибки.

Внешние библиотеки не вызываются: основной и резервный источники подменены
фейковыми клиентами, ``yt-dlp`` — фейковым ``YoutubeDL``, а у клиента
``youtube-transcript-api`` проверяются чистые преобразования и сессия с
таймаутом. Сети, токенов и настоящего ``~/.cache`` тесты не трогают.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from requests import Response, Timeout
from requests.adapters import BaseAdapter
from youtube_transcript_api import (
    FetchedTranscript,
    FetchedTranscriptSnippet,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
)
from yt_dlp import DownloadError, YoutubeDL

import tldw.adapters.youtube as youtube_module
from tldw.adapters import (
    DEFAULT_TRANSCRIPT_TIMEOUT,
    SOURCE_TRANSCRIPT_API,
    SOURCE_YTDLP,
    FallbackVideo,
    NoTranscriptError,
    NotYouTubeURLError,
    Transcript,
    TranscriptApiClient,
    TranscriptSegment,
    VideoMetadata,
    VideoUnavailableError,
    YouTubeAdapter,
    YouTubeAdapterError,
    YouTubeNetworkError,
    YtDlpClient,
    parse_video_id,
)
from tldw.adapters.youtube import _parse_json3

VIDEO_ID = "dQw4w9WgXcQ"
WATCH_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
CAPTION_PAYLOAD = json.dumps(
    {"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "Привет"}]}]}
)
CAPTION_INFO = {
    "title": "Название",
    "channel": "Канал",
    "duration": 60,
    "automatic_captions": {"ru": [{"ext": "json3", "url": "https://captions.test/ru"}]},
}


@dataclass
class FakeTranscriptClient:
    """Фейковый основной источник: отдаёт заданный транскрипт или ошибку."""

    transcript: Transcript | None = None
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def fetch(self, video_id: str) -> Transcript:
        self.calls.append(video_id)
        if self.error is not None:
            raise self.error
        assert self.transcript is not None
        return self.transcript


@dataclass
class FakeFallbackClient:
    """Фейковый yt-dlp: один ``fetch`` отдаёт и транскрипт, и метаданные."""

    metadata: VideoMetadata | None = None
    transcript: Transcript | None = None
    metadata_error: Exception | None = None
    transcript_error: Exception | None = None
    metadata_calls: list[str] = field(default_factory=list)
    fetch_calls: list[str] = field(default_factory=list)

    def fetch_metadata(self, video_id: str) -> VideoMetadata:
        self.metadata_calls.append(video_id)
        if self.metadata_error is not None:
            raise self.metadata_error
        return self.metadata or VideoMetadata(video_id=video_id)

    def fetch(self, video_id: str) -> FallbackVideo:
        self.fetch_calls.append(video_id)
        if self.transcript_error is not None:
            raise self.transcript_error
        if self.metadata_error is not None:
            raise self.metadata_error
        return FallbackVideo(
            transcript=self.transcript or Transcript(),
            metadata=self.metadata or VideoMetadata(video_id=video_id),
        )


@dataclass
class FakeTranscriptApi:
    """Фейковый объект youtube-transcript-api для проверки клиента-обёртки."""

    result: object | None = None
    error: Exception | None = None
    calls: list[tuple[str, list[str]]] = field(default_factory=list)

    def fetch(self, video_id: str, languages: list[str]) -> object:
        self.calls.append((video_id, list(languages)))
        if self.error is not None:
            raise self.error
        return self.result


class FakeYoutubeDL:
    """Подмена ``yt_dlp.YoutubeDL``: считает извлечения и не ходит в сеть."""

    instances: list[FakeYoutubeDL] = []

    def __init__(self, options: dict[str, object]) -> None:
        self.options = dict(options)
        self.info: dict[str, object] = {}
        self.caption_payload: bytes | str = b""
        self.error: Exception | None = None
        self.extract_calls = 0
        self.urlopen_calls = 0
        FakeYoutubeDL.instances.append(self)

    def __enter__(self) -> FakeYoutubeDL:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
        self.extract_calls += 1
        if self.error is not None:
            raise self.error
        return self.info

    def urlopen(self, url: str) -> io.BytesIO:
        self.urlopen_calls += 1
        payload = self.caption_payload
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        return io.BytesIO(data)


def install_fake_ytdlp(
    monkeypatch: pytest.MonkeyPatch,
    *,
    info: dict[str, object] | None = None,
    error: Exception | None = None,
    caption_payload: bytes | str = b"",
) -> list[FakeYoutubeDL]:
    """Подменить ``YoutubeDL`` в адаптере и вернуть список созданных экземпляров."""
    FakeYoutubeDL.instances = []

    class _FakeYoutubeDL(FakeYoutubeDL):
        def __init__(self, options: dict[str, object]) -> None:
            super().__init__(options)
            self.info = info if info is not None else {}
            self.error = error
            self.caption_payload = caption_payload

    monkeypatch.setattr(youtube_module, "YoutubeDL", _FakeYoutubeDL)
    return FakeYoutubeDL.instances


class RecordingAdapter(BaseAdapter):
    """Адаптер requests, который запоминает таймаут и не делает сетевой вызов."""

    def __init__(self) -> None:
        super().__init__()
        self.timeouts: list[float | None] = []

    def send(
        self,
        request: object,
        stream: bool = False,
        timeout: float | None = None,
        verify: bool = True,
        cert: object = None,
        proxies: object = None,
    ) -> Response:
        self.timeouts.append(timeout)
        response = Response()
        response.status_code = 200
        response.request = request  # type: ignore[assignment]
        response._content = b""
        return response

    def close(self) -> None:
        return None


def make_segment(
    text: str = "Первая фраза",
    start_seconds: float = 0.0,
    duration_seconds: float = 2.5,
) -> TranscriptSegment:
    return TranscriptSegment(
        text=text,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
    )


def make_transcript(*, source: str = SOURCE_TRANSCRIPT_API, language: str = "ru") -> Transcript:
    return Transcript(segments=(make_segment(),), language=language, source=source)


def make_fetched(*snippets: FetchedTranscriptSnippet) -> FetchedTranscript:
    return FetchedTranscript(
        snippets=list(snippets),
        video_id=VIDEO_ID,
        language="Russian",
        language_code="ru",
        is_generated=False,
    )


def capture_transcript_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    timeout: float = 2.5,
) -> object:
    """Создать клиент, перехватить сессию из ``YouTubeTranscriptApi`` и вернуть её."""
    sessions: list[object] = []

    class _CapturingApi:
        def __init__(self, *, http_client: object) -> None:
            sessions.append(http_client)

        def fetch(self, video_id: str, languages: list[str]) -> FetchedTranscript:
            return make_fetched(FetchedTranscriptSnippet(text="ок", start=0.0, duration=1.0))

    monkeypatch.setattr(youtube_module, "YouTubeTranscriptApi", _CapturingApi)
    TranscriptApiClient(timeout=timeout).fetch(VIDEO_ID)
    assert len(sessions) == 1
    return sessions[0]


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtube.com/watch?v={VIDEO_ID}&t=42s",
        f"http://m.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}?si=tracking",
        f"https://www.youtube.com/shorts/{VIDEO_ID}",
        f"https://youtube.com/shorts/{VIDEO_ID}?feature=share",
        f"https://www.youtube.com/embed/{VIDEO_ID}",
        f"https://youtube.com/live/{VIDEO_ID}",
        f"youtu.be/{VIDEO_ID}",
        f"www.youtube.com/watch?v={VIDEO_ID}",
    ],
)
def test_parse_video_id_extracts_id_from_supported_links(url: str) -> None:
    assert parse_video_id(url) == VIDEO_ID


@pytest.mark.parametrize(
    "url",
    [
        "https://vimeo.com/123456",
        "https://youtube.com/playlist?list=PL1234567890",
        "https://youtube.com/watch?v=too-short",
        "https://youtube.com/watch",
        f"https://youtube.com.evil.example/watch?v={VIDEO_ID}",
        f"https://example.com/youtube.com/watch?v={VIDEO_ID}",
        f"ftp://youtube.com/watch?v={VIDEO_ID}",
        "https://youtu.be/",
        "",
        "not a youtube link",
    ],
)
def test_parse_video_id_rejects_non_youtube_links(url: str) -> None:
    with pytest.raises(NotYouTubeURLError):
        parse_video_id(url)


def test_parse_video_id_preserves_cause_for_malformed_url() -> None:
    """Битый URL остаётся typed-ошибкой, но с цепочкой ``from exc`` (P3 T-016)."""
    with pytest.raises(NotYouTubeURLError) as excinfo:
        parse_video_id("https://[broken/watch?v=dQw4w9WgXcQ")

    assert isinstance(excinfo.value.__cause__, ValueError)


def test_error_classes_are_typed_and_distinct() -> None:
    assert issubclass(NotYouTubeURLError, YouTubeAdapterError)
    assert issubclass(NoTranscriptError, YouTubeAdapterError)
    assert issubclass(YouTubeNetworkError, YouTubeAdapterError)
    assert not issubclass(NoTranscriptError, YouTubeNetworkError)
    assert not issubclass(NotYouTubeURLError, NoTranscriptError)
    assert issubclass(VideoUnavailableError, YouTubeNetworkError)


def test_fetch_returns_metadata_and_primary_transcript() -> None:
    primary = FakeTranscriptClient(transcript=make_transcript())
    fallback = FakeFallbackClient(metadata=VideoMetadata(VIDEO_ID, "Название", "Канал", 630))

    video = YouTubeAdapter(primary, fallback).fetch(f"{WATCH_URL}&t=10s")

    assert video.video_id == VIDEO_ID
    assert video.url == f"{WATCH_URL}&t=10s"
    assert video.metadata == VideoMetadata(VIDEO_ID, "Название", "Канал", 630)
    assert video.metadata.duration_seconds == 630
    assert video.transcript.segments == (make_segment(),)
    assert video.transcript.source == SOURCE_TRANSCRIPT_API
    assert primary.calls == [VIDEO_ID]
    assert fallback.metadata_calls == [VIDEO_ID]
    assert fallback.fetch_calls == []


def test_fetch_rejects_non_youtube_link_without_external_calls() -> None:
    primary = FakeTranscriptClient(transcript=make_transcript())
    fallback = FakeFallbackClient(transcript=make_transcript(source=SOURCE_YTDLP))

    with pytest.raises(NotYouTubeURLError):
        YouTubeAdapter(primary, fallback).fetch("https://vimeo.com/123456")

    assert primary.calls == []
    assert fallback.metadata_calls == []
    assert fallback.fetch_calls == []


@pytest.mark.parametrize(
    "primary_error",
    [
        pytest.param(NoTranscriptError(), id="no-subtitles"),
        pytest.param(YouTubeNetworkError(), id="network-error"),
        pytest.param(VideoUnavailableError(), id="video-unavailable"),
    ],
)
def test_fetch_falls_back_to_ytdlp(primary_error: Exception) -> None:
    primary = FakeTranscriptClient(error=primary_error)
    fallback = FakeFallbackClient(
        metadata=VideoMetadata(VIDEO_ID, "Название", "Канал", 60),
        transcript=make_transcript(source=SOURCE_YTDLP, language="en"),
    )

    video = YouTubeAdapter(primary, fallback).fetch(WATCH_URL)

    assert video.transcript.source == SOURCE_YTDLP
    assert video.transcript.language == "en"
    assert video.metadata.duration_seconds == 60
    assert primary.calls == [VIDEO_ID]
    assert fallback.fetch_calls == [VIDEO_ID]


def test_empty_primary_transcript_triggers_fallback() -> None:
    primary = FakeTranscriptClient(transcript=Transcript())
    fallback = FakeFallbackClient(transcript=make_transcript(source=SOURCE_YTDLP))

    video = YouTubeAdapter(primary, fallback).fetch(WATCH_URL)

    assert video.transcript.source == SOURCE_YTDLP
    assert fallback.fetch_calls == [VIDEO_ID]


def test_no_subtitles_in_both_sources_raises_no_transcript_error() -> None:
    primary = FakeTranscriptClient(error=NoTranscriptError())
    fallback = FakeFallbackClient(transcript=Transcript())

    with pytest.raises(NoTranscriptError) as exc_info:
        YouTubeAdapter(primary, fallback).fetch(WATCH_URL)

    assert type(exc_info.value) is NoTranscriptError
    assert not isinstance(exc_info.value, YouTubeNetworkError)


@pytest.mark.parametrize(
    ("primary_error", "fallback_error", "expected_type"),
    [
        pytest.param(
            NoTranscriptError(),
            NoTranscriptError(),
            NoTranscriptError,
            id="no-subtitles-both",
        ),
        pytest.param(
            VideoUnavailableError(),
            NoTranscriptError(),
            VideoUnavailableError,
            id="primary-unavailable-fallback-no-subtitles",
        ),
        pytest.param(
            NoTranscriptError(),
            VideoUnavailableError(),
            VideoUnavailableError,
            id="primary-no-subtitles-fallback-unavailable",
        ),
        pytest.param(
            VideoUnavailableError(),
            YouTubeNetworkError(),
            VideoUnavailableError,
            id="primary-unavailable-fallback-network",
        ),
        pytest.param(
            YouTubeNetworkError(),
            VideoUnavailableError(),
            VideoUnavailableError,
            id="primary-network-fallback-unavailable",
        ),
        pytest.param(
            VideoUnavailableError(),
            VideoUnavailableError(),
            VideoUnavailableError,
            id="unavailable-both",
        ),
        pytest.param(
            NoTranscriptError(),
            YouTubeNetworkError(),
            NoTranscriptError,
            id="primary-no-subtitles-fallback-network",
        ),
        pytest.param(
            YouTubeNetworkError(),
            NoTranscriptError(),
            NoTranscriptError,
            id="primary-network-fallback-no-subtitles",
        ),
        pytest.param(
            YouTubeNetworkError(),
            YouTubeNetworkError(),
            YouTubeNetworkError,
            id="network-both",
        ),
    ],
)
def test_transcript_error_priority(
    primary_error: Exception,
    fallback_error: Exception,
    expected_type: type[YouTubeAdapterError],
) -> None:
    primary = FakeTranscriptClient(error=primary_error)
    fallback = FakeFallbackClient(transcript_error=fallback_error)

    with pytest.raises(expected_type) as exc_info:
        YouTubeAdapter(primary, fallback).fetch(WATCH_URL)

    assert type(exc_info.value) is expected_type
    assert isinstance(exc_info.value.__cause__, YouTubeAdapterError)


def test_video_unavailable_in_primary_is_the_cause_of_final_error() -> None:
    primary_error = VideoUnavailableError()
    fallback_error = NoTranscriptError()
    primary = FakeTranscriptClient(error=primary_error)
    fallback = FakeFallbackClient(transcript_error=fallback_error)

    with pytest.raises(VideoUnavailableError) as exc_info:
        YouTubeAdapter(primary, fallback).fetch(WATCH_URL)

    assert exc_info.value.__cause__ is primary_error


def test_no_subtitles_error_names_the_winning_source_as_cause() -> None:
    primary_error = YouTubeNetworkError()
    fallback_error = NoTranscriptError()
    primary = FakeTranscriptClient(error=primary_error)
    fallback = FakeFallbackClient(transcript_error=fallback_error)

    with pytest.raises(NoTranscriptError) as exc_info:
        YouTubeAdapter(primary, fallback).fetch(WATCH_URL)

    assert exc_info.value.__cause__ is fallback_error


def test_network_error_names_the_last_attempt_as_cause() -> None:
    fallback_error = YouTubeNetworkError()
    primary = FakeTranscriptClient(error=YouTubeNetworkError())
    fallback = FakeFallbackClient(transcript_error=fallback_error)

    with pytest.raises(YouTubeNetworkError) as exc_info:
        YouTubeAdapter(primary, fallback).fetch(WATCH_URL)

    assert exc_info.value.__cause__ is fallback_error


def test_empty_fallback_transcript_keeps_primary_unavailable_error() -> None:
    primary = FakeTranscriptClient(error=VideoUnavailableError())
    fallback = FakeFallbackClient(transcript=Transcript())

    with pytest.raises(VideoUnavailableError):
        YouTubeAdapter(primary, fallback).fetch(WATCH_URL)


def test_metadata_failure_does_not_break_transcript() -> None:
    primary = FakeTranscriptClient(transcript=make_transcript())
    fallback = FakeFallbackClient(metadata_error=YouTubeNetworkError())

    video = YouTubeAdapter(primary, fallback).fetch(WATCH_URL)

    assert video.metadata == VideoMetadata(video_id=VIDEO_ID)
    assert video.metadata.title == ""
    assert video.metadata.channel == ""
    assert video.metadata.duration_seconds is None
    assert video.transcript.source == SOURCE_TRANSCRIPT_API


def test_fetch_does_not_ask_metadata_again_after_fallback_transcript() -> None:
    primary = FakeTranscriptClient(error=NoTranscriptError())
    fallback = FakeFallbackClient(
        metadata=VideoMetadata(VIDEO_ID, "Название", "Канал", 60),
        transcript=make_transcript(source=SOURCE_YTDLP),
    )

    YouTubeAdapter(primary, fallback).fetch(WATCH_URL)

    assert fallback.fetch_calls == [VIDEO_ID]
    assert fallback.metadata_calls == []


def test_fetch_extracts_ytdlp_info_once_for_transcript_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = install_fake_ytdlp(
        monkeypatch,
        info=CAPTION_INFO,
        caption_payload=CAPTION_PAYLOAD,
    )
    primary = FakeTranscriptClient(error=NoTranscriptError())

    video = YouTubeAdapter(primary, YtDlpClient()).fetch(WATCH_URL)

    assert sum(instance.extract_calls for instance in instances) == 1
    assert video.metadata == VideoMetadata(VIDEO_ID, "Название", "Канал", 60)
    assert video.transcript.segments == (TranscriptSegment("Привет", 0.0, 1.0),)
    assert video.transcript.source == SOURCE_YTDLP


def test_fetch_extracts_ytdlp_info_once_when_primary_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = install_fake_ytdlp(monkeypatch, info={"title": "Название", "duration": 60})
    primary = FakeTranscriptClient(transcript=make_transcript())

    video = YouTubeAdapter(primary, YtDlpClient()).fetch(WATCH_URL)

    assert sum(instance.extract_calls for instance in instances) == 1
    assert sum(instance.urlopen_calls for instance in instances) == 0
    assert video.metadata.title == "Название"
    assert video.transcript.source == SOURCE_TRANSCRIPT_API


def test_transcript_api_client_converts_snippets_to_segments() -> None:
    api = FakeTranscriptApi(
        result=make_fetched(
            FetchedTranscriptSnippet(text=" Первая фраза ", start=0.0, duration=1.5),
            FetchedTranscriptSnippet(text="Вторая", start=1.5, duration=2.0),
        )
    )

    transcript = TranscriptApiClient(api=api).fetch(VIDEO_ID)

    assert transcript.segments == (
        TranscriptSegment("Первая фраза", 0.0, 1.5),
        TranscriptSegment("Вторая", 1.5, 2.0),
    )
    assert transcript.language == "ru"
    assert transcript.source == SOURCE_TRANSCRIPT_API
    assert api.calls == [(VIDEO_ID, ["ru", "en"])]


def test_transcript_api_client_uses_configured_languages() -> None:
    api = FakeTranscriptApi(
        result=make_fetched(FetchedTranscriptSnippet(text="Hallo", start=0.0, duration=1.0))
    )

    transcript = TranscriptApiClient(api=api, languages=("de", "en")).fetch(VIDEO_ID)

    assert transcript.segments == (TranscriptSegment("Hallo", 0.0, 1.0),)
    assert api.calls == [(VIDEO_ID, ["de", "en"])]


def test_transcript_text_joins_segments_and_drops_empty() -> None:
    transcript = Transcript(
        segments=(
            TranscriptSegment("раз", 0.0, 1.0),
            TranscriptSegment("   ", 1.0, 1.0),
            TranscriptSegment("два", 2.0, 1.0),
        )
    )

    assert transcript.text == "раз два"
    assert not transcript.is_empty


def test_transcript_api_client_treats_empty_result_as_no_subtitles() -> None:
    client = TranscriptApiClient(api=FakeTranscriptApi(result=make_fetched()))

    with pytest.raises(NoTranscriptError):
        client.fetch(VIDEO_ID)


@pytest.mark.parametrize(
    ("library_error", "expected_type"),
    [
        pytest.param(TranscriptsDisabled(VIDEO_ID), NoTranscriptError, id="transcripts-disabled"),
        pytest.param(RequestBlocked(VIDEO_ID), YouTubeNetworkError, id="request-blocked"),
        pytest.param(VideoUnavailable(VIDEO_ID), VideoUnavailableError, id="video-unavailable"),
        pytest.param(Timeout("timed out"), YouTubeNetworkError, id="session-timeout"),
        pytest.param(RuntimeError("boom"), YouTubeNetworkError, id="unexpected-error"),
    ],
)
def test_transcript_api_client_maps_library_errors(
    library_error: Exception,
    expected_type: type[YouTubeAdapterError],
) -> None:
    client = TranscriptApiClient(api=FakeTranscriptApi(error=library_error))

    with pytest.raises(expected_type) as exc_info:
        client.fetch(VIDEO_ID)

    assert type(exc_info.value) is expected_type
    assert exc_info.value.__cause__ is library_error


def test_transcript_api_client_maps_timeout_to_network_error() -> None:
    library_error = Timeout("YouTube не ответил за 10 секунд")
    client = TranscriptApiClient(api=FakeTranscriptApi(error=library_error))

    with pytest.raises(YouTubeNetworkError) as exc_info:
        client.fetch(VIDEO_ID)

    assert isinstance(exc_info.value.__cause__, Timeout)


@pytest.mark.parametrize(
    ("timeout", "expected_error"),
    [
        pytest.param(0, ValueError, id="zero"),
        pytest.param(-1, ValueError, id="negative"),
        pytest.param(float("inf"), ValueError, id="infinite"),
        pytest.param(float("nan"), ValueError, id="nan"),
        pytest.param("10", TypeError, id="string"),
        pytest.param(True, TypeError, id="bool"),
        pytest.param(None, TypeError, id="none"),
    ],
)
def test_transcript_api_client_rejects_invalid_timeout(
    timeout: object,
    expected_error: type[Exception],
) -> None:
    with pytest.raises(expected_error):
        TranscriptApiClient(timeout=timeout)  # type: ignore[arg-type]


def test_transcript_api_client_default_timeout_is_positive() -> None:
    client = TranscriptApiClient()

    assert client.timeout == DEFAULT_TRANSCRIPT_TIMEOUT
    assert client.timeout > 0


def test_transcript_api_client_passes_timeout_session_to_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = capture_transcript_session(monkeypatch, timeout=2.5)

    assert session.timeout == 2.5


def test_timeout_session_applies_timeout_to_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = capture_transcript_session(monkeypatch, timeout=2.5)
    adapter = RecordingAdapter()
    session.mount("https://", adapter)  # type: ignore[attr-defined]

    session.get("https://www.youtube.com/watch?v=test")  # type: ignore[attr-defined]

    assert adapter.timeouts == [2.5]


@pytest.mark.parametrize(
    "result",
    [
        pytest.param(None, id="none"),
        pytest.param(
            {"snippets": [{"text": "старый формат", "start": 0.0, "duration": 1.0}]},
            id="old-dict-format",
        ),
        pytest.param(
            [{"text": "старый формат", "start": 0.0, "duration": 1.0}],
            id="list-of-dicts",
        ),
        pytest.param("events", id="plain-string"),
        pytest.param(42, id="not-iterable"),
    ],
)
def test_transcript_api_client_rejects_broken_response(result: object) -> None:
    client = TranscriptApiClient(api=FakeTranscriptApi(result=result))

    with pytest.raises(YouTubeNetworkError) as exc_info:
        client.fetch(VIDEO_ID)

    assert type(exc_info.value) is YouTubeNetworkError


def test_transcript_api_client_rejects_snippet_with_bad_numbers() -> None:
    api = FakeTranscriptApi(
        result=make_fetched(
            FetchedTranscriptSnippet(text="ок", start=None, duration=1.0),  # type: ignore[arg-type]
        )
    )

    with pytest.raises(YouTubeNetworkError):
        TranscriptApiClient(api=api).fetch(VIDEO_ID)


def test_parse_json3_returns_segments_in_seconds() -> None:
    payload = json.dumps(
        {
            "events": [
                {"tStartMs": 0, "dDurationMs": 1500, "segs": [{"utf8": "Привет"}]},
                {
                    "tStartMs": 1500,
                    "dDurationMs": 2500,
                    "segs": [{"utf8": "мир"}, {"utf8": "!"}],
                },
                {
                    "tStartMs": 4000,
                    "dDurationMs": 1000,
                    "segs": [{"utf8": "Hello "}, {"utf8": "world"}],
                },
                {"tStartMs": 5000, "dDurationMs": 100, "segs": [{"utf8": "\n"}]},
                {"tStartMs": 5100},
            ]
        }
    )

    assert _parse_json3(payload) == (
        TranscriptSegment("Привет", 0.0, 1.5),
        TranscriptSegment("мир!", 1.5, 2.5),
        TranscriptSegment("Hello world", 4.0, 1.0),
    )


def test_parse_json3_rejects_broken_payload() -> None:
    with pytest.raises(NoTranscriptError):
        _parse_json3(b"{not-json")


def test_ytdlp_client_maps_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_ytdlp(
        monkeypatch,
        info={"title": "  Название  ", "channel": "Канал", "duration": 630},
    )

    metadata = YtDlpClient().fetch_metadata(VIDEO_ID)

    assert metadata == VideoMetadata(VIDEO_ID, "Название", "Канал", 630)


def test_ytdlp_client_uses_uploader_and_ignores_bad_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_ytdlp(
        monkeypatch,
        info={"title": "Название", "uploader": "Автор", "duration": "unknown"},
    )

    metadata = YtDlpClient().fetch_metadata(VIDEO_ID)

    assert metadata.channel == "Автор"
    assert metadata.duration_seconds is None


def test_ytdlp_client_builds_transcript_from_json3(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_ytdlp(
        monkeypatch,
        info={
            "automatic_captions": {
                "en": [{"ext": "json3", "url": "https://captions.test/en"}],
                "ru": [{"ext": "json3", "url": "https://captions.test/ru"}],
            },
            "subtitles": {"de": [{"ext": "json3", "url": "https://captions.test/de"}]},
        },
        caption_payload=json.dumps(
            {"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "Привет"}]}]}
        ),
    )

    video = YtDlpClient(languages=("ru", "en")).fetch(VIDEO_ID)

    assert video.transcript.source == SOURCE_YTDLP
    assert video.transcript.language == "ru"
    assert video.transcript.segments == (TranscriptSegment("Привет", 0.0, 1.0),)
    assert video.metadata == VideoMetadata(VIDEO_ID)


def test_ytdlp_client_raises_no_transcript_without_captions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_ytdlp(monkeypatch, info={"title": "Название"})

    with pytest.raises(NoTranscriptError):
        YtDlpClient().fetch(VIDEO_ID)


@pytest.mark.parametrize(
    "message",
    [
        "Private video. Sign in if you've been granted access to this video",
        "This video is private",
        "Video unavailable",
        "This video has been removed by the uploader",
        "This video is no longer available because the account has been terminated",
        "Members-only content. Join this channel to get access",
    ],
)
def test_ytdlp_private_or_unavailable_maps_to_video_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    install_fake_ytdlp(monkeypatch, error=DownloadError(message))

    with pytest.raises(VideoUnavailableError) as exc_info:
        YtDlpClient().fetch_metadata(VIDEO_ID)

    assert isinstance(exc_info.value.__cause__, DownloadError)


@pytest.mark.parametrize(
    "message",
    [
        "Unable to download webpage: <urlopen error timed out>",
        "HTTP Error 429: Too Many Requests",
        "Failed to parse JSON",
    ],
)
def test_ytdlp_other_download_errors_map_to_network(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    install_fake_ytdlp(monkeypatch, error=DownloadError(message))

    with pytest.raises(YouTubeNetworkError) as exc_info:
        YtDlpClient().fetch_metadata(VIDEO_ID)

    assert type(exc_info.value) is YouTubeNetworkError


def test_ytdlp_unavailable_error_reaches_user_through_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_ytdlp(monkeypatch, error=DownloadError("Private video."))

    with pytest.raises(VideoUnavailableError):
        YouTubeAdapter(FakeTranscriptClient(error=NoTranscriptError()), YtDlpClient()).fetch(
            WATCH_URL
        )


def test_ytdlp_cache_is_disabled_and_writes_nothing_to_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = install_fake_ytdlp(monkeypatch, info={"title": "Название"})

    YtDlpClient().fetch_metadata(VIDEO_ID)

    options = instances[0].options
    assert options["cachedir"] is False

    cache_home = tmp_path / "xdg-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    with YoutubeDL(options, auto_init=False) as ydl:
        ydl.cache.store("tldw-test", "key", {"value": 1})

    assert not cache_home.exists()
    assert not (tmp_path / "home").exists()


@pytest.mark.parametrize(
    ("socket_timeout", "expected_error"),
    [
        pytest.param(0, ValueError, id="zero"),
        pytest.param(float("inf"), ValueError, id="infinite"),
        pytest.param("15", TypeError, id="string"),
    ],
)
def test_ytdlp_client_rejects_invalid_socket_timeout(
    socket_timeout: object,
    expected_error: type[Exception],
) -> None:
    with pytest.raises(expected_error):
        YtDlpClient(socket_timeout=socket_timeout)  # type: ignore[arg-type]

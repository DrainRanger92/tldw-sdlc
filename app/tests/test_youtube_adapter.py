"""Офлайновые тесты адаптера YouTube: ссылки, транскрипт, fallback и ошибки.

Внешние библиотеки не вызываются: основной и резервный источники подменены
фейковыми клиентами, а у конкретных клиентов — только чистые преобразования
или monkeypatch приватных методов без сети.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from youtube_transcript_api import (
    FetchedTranscript,
    FetchedTranscriptSnippet,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
)

from tldw.adapters import (
    SOURCE_TRANSCRIPT_API,
    SOURCE_YTDLP,
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
    """Фейковый yt-dlp: отдельно управляет метаданными и транскриптом."""

    metadata: VideoMetadata | None = None
    transcript: Transcript | None = None
    metadata_error: Exception | None = None
    transcript_error: Exception | None = None
    metadata_calls: list[str] = field(default_factory=list)
    transcript_calls: list[str] = field(default_factory=list)

    def fetch_metadata(self, video_id: str) -> VideoMetadata:
        self.metadata_calls.append(video_id)
        if self.metadata_error is not None:
            raise self.metadata_error
        return self.metadata or VideoMetadata(video_id=video_id)

    def fetch_transcript(self, video_id: str) -> Transcript:
        self.transcript_calls.append(video_id)
        if self.transcript_error is not None:
            raise self.transcript_error
        return self.transcript or Transcript()


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
    assert fallback.transcript_calls == []


def test_fetch_rejects_non_youtube_link_without_external_calls() -> None:
    primary = FakeTranscriptClient(transcript=make_transcript())
    fallback = FakeFallbackClient(transcript=make_transcript(source=SOURCE_YTDLP))

    with pytest.raises(NotYouTubeURLError):
        YouTubeAdapter(primary, fallback).fetch("https://vimeo.com/123456")

    assert primary.calls == []
    assert fallback.metadata_calls == []
    assert fallback.transcript_calls == []


@pytest.mark.parametrize(
    "primary_error",
    [
        pytest.param(NoTranscriptError(), id="no-subtitles"),
        pytest.param(YouTubeNetworkError(), id="network-error"),
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
    assert primary.calls == [VIDEO_ID]
    assert fallback.transcript_calls == [VIDEO_ID]


def test_empty_primary_transcript_triggers_fallback() -> None:
    primary = FakeTranscriptClient(transcript=Transcript())
    fallback = FakeFallbackClient(transcript=make_transcript(source=SOURCE_YTDLP))

    video = YouTubeAdapter(primary, fallback).fetch(WATCH_URL)

    assert video.transcript.source == SOURCE_YTDLP
    assert fallback.transcript_calls == [VIDEO_ID]


def test_no_subtitles_in_both_sources_raises_no_transcript_error() -> None:
    primary = FakeTranscriptClient(error=NoTranscriptError())
    fallback = FakeFallbackClient(transcript=Transcript())

    with pytest.raises(NoTranscriptError) as exc_info:
        YouTubeAdapter(primary, fallback).fetch(WATCH_URL)

    assert not isinstance(exc_info.value, YouTubeNetworkError)
    assert not isinstance(exc_info.value, NotYouTubeURLError)


@pytest.mark.parametrize(
    ("primary_error", "fallback_error"),
    [
        pytest.param(YouTubeNetworkError(), NoTranscriptError(), id="fallback-no-subtitles"),
        pytest.param(NoTranscriptError(), YouTubeNetworkError(), id="primary-no-subtitles"),
    ],
)
def test_no_subtitles_signal_wins_over_network_error(
    primary_error: Exception,
    fallback_error: Exception,
) -> None:
    primary = FakeTranscriptClient(error=primary_error)
    fallback = FakeFallbackClient(transcript_error=fallback_error)

    with pytest.raises(NoTranscriptError):
        YouTubeAdapter(primary, fallback).fetch(WATCH_URL)


def test_network_failure_in_both_sources_raises_network_error() -> None:
    primary = FakeTranscriptClient(error=YouTubeNetworkError())
    fallback = FakeFallbackClient(transcript_error=YouTubeNetworkError())

    with pytest.raises(YouTubeNetworkError) as exc_info:
        YouTubeAdapter(primary, fallback).fetch(WATCH_URL)

    assert type(exc_info.value) is YouTubeNetworkError
    assert not isinstance(exc_info.value, NoTranscriptError)


def test_video_unavailable_in_fallback_is_preserved() -> None:
    primary = FakeTranscriptClient(error=YouTubeNetworkError())
    fallback = FakeFallbackClient(transcript_error=VideoUnavailableError())

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
    client = YtDlpClient()
    monkeypatch.setattr(
        client,
        "_extract_info",
        lambda video_id: {"title": "  Название  ", "channel": "Канал", "duration": 630},
    )

    assert client.fetch_metadata(VIDEO_ID) == VideoMetadata(VIDEO_ID, "Название", "Канал", 630)


def test_ytdlp_client_uses_uploader_and_ignores_bad_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = YtDlpClient()
    monkeypatch.setattr(
        client,
        "_extract_info",
        lambda video_id: {"title": "Название", "uploader": "Автор", "duration": "unknown"},
    )

    metadata = client.fetch_metadata(VIDEO_ID)

    assert metadata.channel == "Автор"
    assert metadata.duration_seconds is None


def test_ytdlp_client_builds_transcript_from_json3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = YtDlpClient(languages=("ru", "en"))
    info = {
        "automatic_captions": {
            "en": [{"ext": "json3", "url": "https://captions.test/en"}],
            "ru": [{"ext": "json3", "url": "https://captions.test/ru"}],
        },
        "subtitles": {"de": [{"ext": "json3", "url": "https://captions.test/de"}]},
    }
    payload = json.dumps(
        {"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "Привет"}]}]}
    )
    monkeypatch.setattr(client, "_extract_info", lambda video_id: info)
    monkeypatch.setattr(client, "_read_caption", lambda url: payload)

    transcript = client.fetch_transcript(VIDEO_ID)

    assert transcript.source == SOURCE_YTDLP
    assert transcript.language == "ru"
    assert transcript.segments == (TranscriptSegment("Привет", 0.0, 1.0),)


def test_ytdlp_client_raises_no_transcript_without_captions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = YtDlpClient()
    monkeypatch.setattr(client, "_extract_info", lambda video_id: {"title": "Название"})

    with pytest.raises(NoTranscriptError):
        client.fetch_transcript(VIDEO_ID)

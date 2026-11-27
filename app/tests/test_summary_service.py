"""Офлайновые тесты сервиса конспекта.

Сервис проверяется с фейковыми YouTube- и LLM-адаптерами: сеть и токены не
нужны, каталог данных живёт в ``tmp_path``. Покрыты успешный путь, порог
длительности (включая границу ``duration == порог``), отсутствие субтитров,
``LLMError`` и усечение транскрипта без поломки JSON-пайплайна.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from tldw.adapters.llm import (
    LLMClient,
    LLMError,
    SummaryQuote,
    SummaryTimelineEntry,
    VideoSummary,
    build_system_prompt,
)
from tldw.adapters.youtube import (
    NoTranscriptError,
    Transcript,
    TranscriptSegment,
    Video,
    VideoMetadata,
)
from tldw.config import DEFAULT_MAX_VIDEO_DURATION_SECONDS, load_settings
from tldw.errors import UserFacingError
from tldw.services.note import (
    SECTION_HEADINGS,
    Note,
    format_timecode,
    note_filename,
    render_note,
)
from tldw.services.summary import (
    MESSAGE_EMPTY_TRANSCRIPT,
    TRANSCRIPT_TRUNCATION_MARKER,
    EmptyTranscriptError,
    SummaryService,
    VideoTooLongError,
    build_note,
    join_transcript,
)

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO_ID = "dQw4w9WgXcQ"
FAKE_API_KEY = "test-llm-key-not-a-secret"
MODEL = "test-model"
BASE_URL = "https://llm.test/v1"
TIMECODE_LINE_RE = re.compile(r"^\[\d{2}:\d{2}(?::\d{2})?\] .+$")


@dataclass
class FakeVideoSource:
    """Фейковый источник видео: возвращает заданный ``Video`` или ошибку."""

    video: Video | None = None
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str) -> Video:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        assert self.video is not None
        return self.video


@dataclass
class FakeSummarizer:
    """Фейковый LLM: запоминает промпт и возвращает заданный конспект."""

    summary: VideoSummary | None = None
    error: Exception | None = None
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    def summarize(self, transcript: str, *, title: str = "", channel: str = "") -> VideoSummary:
        self.calls.append((transcript, title, channel))
        if self.error is not None:
            raise self.error
        assert self.summary is not None
        return self.summary


def make_transcript() -> Transcript:
    """Небольшой транскрипт с таймкодами в секундах."""
    return Transcript(
        segments=(
            TranscriptSegment("Вступление", 0),
            TranscriptSegment("Первая мысль", 15),
            TranscriptSegment("Практика", 754),
        ),
        language="ru",
        source="test",
    )


def make_video(
    *,
    duration: int | None = 600,
    transcript: Transcript | None = None,
    url: str = URL,
) -> Video:
    return Video(
        url=url,
        metadata=VideoMetadata(
            video_id=VIDEO_ID,
            title="Название видео",
            channel="Канал автора",
            duration_seconds=duration,
        ),
        transcript=transcript if transcript is not None else make_transcript(),
    )


def make_summary() -> VideoSummary:
    """Валидный ответ LLM по всем пяти разделам шаблона."""
    return VideoSummary(
        tldr=("Первое предложение.", "Второе предложение.", "Третье предложение."),
        key_ideas=tuple(f"Идея номер {index}" for index in range(1, 6)),
        timeline=(
            SummaryTimelineEntry("00:00", "Вступление"),
            SummaryTimelineEntry("12:34", "Практика"),
        ),
        quotes=(
            SummaryQuote("Первая цитата.", "03:21"),
            SummaryQuote("Вторая цитата.", "27:08"),
        ),
        follow_ups=("Проверить пример из видео.",),
    )


def valid_payload() -> dict[str, Any]:
    """JSON-ответ LLM, проходящий строгую валидацию адаптера."""
    return {
        "tldr": ["Первое предложение.", "Второе предложение.", "Третье предложение."],
        "key_ideas": [f"Идея номер {index}" for index in range(1, 6)],
        "timeline": [
            {"timecode": "00:00", "title": "Вступление"},
            {"timecode": "12:34", "title": "Практика"},
        ],
        "quotes": [
            {"timecode": "03:21", "text": "Первая цитата."},
            {"timecode": "27:08", "text": "Вторая цитата."},
        ],
        "follow_ups": ["Проверить пример из видео."],
    }


def chat_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def make_long_transcript(count: int = 30, text_length: int = 90) -> Transcript:
    segments = tuple(
        TranscriptSegment(f"сегмент {index:02d} " + "я" * text_length, index * 10)
        for index in range(count)
    )
    return Transcript(segments=segments, language="ru", source="test")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def video_source() -> FakeVideoSource:
    return FakeVideoSource(video=make_video())


@pytest.fixture
def summarizer() -> FakeSummarizer:
    return FakeSummarizer(summary=make_summary())


@pytest.fixture
def service(
    video_source: FakeVideoSource,
    summarizer: FakeSummarizer,
    data_dir: Path,
) -> SummaryService:
    return SummaryService(video_source, summarizer, data_dir=data_dir)


def frontmatter(markdown: str) -> dict[str, Any]:
    lines = markdown.splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    return yaml.safe_load("\n".join(lines[1:end]))


def test_success_returns_note_and_renders_full_template(
    service: SummaryService,
    video_source: FakeVideoSource,
    summarizer: FakeSummarizer,
) -> None:
    result = service.process(URL)

    assert isinstance(result.note, Note)
    assert result.note.title == "Название видео"
    assert result.note.channel == "Канал автора"
    assert result.note.duration_seconds == 600
    assert result.note.source == URL, "source — исходная ссылка, а не video id"
    assert result.note.date == date.today()
    assert result.note.tldr == ("Первое предложение.", "Второе предложение.", "Третье предложение.")
    assert result.note.key_ideas == tuple(f"Идея номер {index}" for index in range(1, 6))
    assert [entry.timecode for entry in result.note.timeline] == ["00:00", "12:34"]
    assert [quote.timecode for quote in result.note.quotes] == ["03:21", "27:08"]

    assert result.markdown == render_note(result.note)
    metadata = frontmatter(result.markdown)
    assert metadata["title"] == "Название видео"
    assert metadata["source"] == URL
    assert metadata["channel"] == "Канал автора"
    assert metadata["duration"] == 600
    assert metadata["date"] == date.today().isoformat()
    assert metadata["tags"] == []
    assert metadata["status"] == "processed"

    positions = [result.markdown.index(heading) for heading in SECTION_HEADINGS]
    assert positions == sorted(positions), "секции должны идти в порядке шаблона"
    assert "- [00:00] Вступление" in result.markdown
    assert "> Первая цитата." in result.markdown
    assert "> — [03:21]" in result.markdown

    assert video_source.calls == [URL]
    assert len(summarizer.calls) == 1
    prompt, title, channel = summarizer.calls[0]
    assert title == "Название видео"
    assert channel == "Канал автора"
    assert prompt == "[00:00] Вступление\n[00:15] Первая мысль\n[12:34] Практика"


def test_success_writes_markdown_file(
    service: SummaryService,
    data_dir: Path,
) -> None:
    result = service.process(URL)

    assert result.filename == note_filename(result.note)
    assert result.filename.endswith(".md")
    assert result.path == data_dir / result.filename
    assert result.path is not None and result.path.is_file()
    assert result.path.read_text(encoding="utf-8") == result.markdown
    assert list(data_dir.iterdir()) == [result.path], "в каталоге не должно быть временных файлов"


def test_source_is_original_user_link(
    service: SummaryService,
    video_source: FakeVideoSource,
) -> None:
    """Даже если адаптер вернул нормализованный URL, source — исходная ссылка."""
    video_source.video = make_video(url="https://youtu.be/normalized-link")

    result = service.process(URL)

    assert result.note.source == URL
    assert f'source: "{URL}"' in result.markdown


def test_without_data_dir_returns_markdown_but_writes_nothing(
    video_source: FakeVideoSource,
    summarizer: FakeSummarizer,
    tmp_path: Path,
) -> None:
    service = SummaryService(video_source, summarizer)

    result = service.process(URL)

    assert result.path is None
    assert result.markdown.startswith("---\n")
    assert list(tmp_path.rglob("*.md")) == []


def test_video_longer_than_default_threshold_raises_without_note(
    data_dir: Path,
) -> None:
    assert DEFAULT_MAX_VIDEO_DURATION_SECONDS == 2 * 60 * 60
    source = FakeVideoSource(video=make_video(duration=DEFAULT_MAX_VIDEO_DURATION_SECONDS + 1))
    client = FakeSummarizer(summary=make_summary())
    service = SummaryService(source, client, data_dir=data_dir)

    with pytest.raises(VideoTooLongError) as excinfo:
        service.process(URL)

    assert excinfo.value.duration_seconds == DEFAULT_MAX_VIDEO_DURATION_SECONDS + 1
    assert excinfo.value.max_duration_seconds == DEFAULT_MAX_VIDEO_DURATION_SECONDS
    assert "02:00:01" in str(excinfo.value)
    assert "02:00:00" in str(excinfo.value)
    assert client.calls == [], "LLM не должен вызываться для слишком длинного видео"
    assert list(data_dir.rglob("*.md")) == [], "частичной заметки быть не должно"
    assert not data_dir.exists()


def test_duration_equal_to_default_threshold_is_allowed(data_dir: Path) -> None:
    source = FakeVideoSource(video=make_video(duration=DEFAULT_MAX_VIDEO_DURATION_SECONDS))
    client = FakeSummarizer(summary=make_summary())
    service = SummaryService(source, client, data_dir=data_dir)

    result = service.process(URL)

    assert result.path is not None and result.path.is_file()
    assert len(client.calls) == 1


def test_custom_threshold_boundary(data_dir: Path) -> None:
    source = FakeVideoSource(video=make_video(duration=600))
    client = FakeSummarizer(summary=make_summary())
    service = SummaryService(source, client, max_duration_seconds=600, data_dir=data_dir)

    result = service.process(URL)
    assert result.path is not None and result.path.is_file()

    source.video = make_video(duration=601)
    with pytest.raises(VideoTooLongError):
        service.process(URL)


def test_threshold_and_data_dir_come_from_settings(
    fake_env: dict[str, str],
) -> None:
    settings = load_settings({**fake_env, "VIDEO_MAX_DURATION_SECONDS": "60"})
    source = FakeVideoSource(video=make_video(duration=61))
    client = FakeSummarizer(summary=make_summary())
    service = SummaryService.from_settings(settings, source, client)

    assert service.max_duration_seconds == 60
    with pytest.raises(VideoTooLongError):
        service.process(URL)
    assert list(settings.data_dir.rglob("*.md")) == []

    source.video = make_video(duration=60)
    result = service.process(URL)

    assert result.path == settings.data_dir / result.filename
    assert result.path.is_file()


def test_unknown_duration_is_allowed(
    service: SummaryService,
    video_source: FakeVideoSource,
) -> None:
    video_source.video = make_video(duration=None)

    result = service.process(URL)

    assert result.note.duration_seconds is None
    assert "duration: null" in result.markdown


def test_transcript_timecodes_are_kept_in_prompt(
    service: SummaryService,
    summarizer: FakeSummarizer,
    video_source: FakeVideoSource,
) -> None:
    video_source.video = make_video(
        transcript=Transcript(
            segments=(
                TranscriptSegment("Начало", 0),
                TranscriptSegment("Минута", 62),
                TranscriptSegment("Час", 3723),
            ),
            language="ru",
            source="test",
        )
    )

    service.process(URL)

    prompt, _, _ = summarizer.calls[0]
    assert prompt.splitlines() == [
        "[00:00] Начало",
        "[01:02] Минута",
        "[01:02:03] Час",
    ]


def test_prompt_documents_transcript_truncation() -> None:
    """LLM предупреждён о маркере усечения, а не получает молча обрезанный текст."""
    assert "транскрипт усечён" in build_system_prompt()
    assert "не выдумывай" in build_system_prompt()


def test_join_transcript_handles_empty_transcript() -> None:
    material = join_transcript(Transcript())

    assert material.is_empty is True
    assert material.truncated is False
    assert material.used_segments == material.total_segments == 0


def test_join_transcript_without_truncation_returns_all_segments() -> None:
    transcript = make_transcript()

    material = join_transcript(transcript, max_chars=1000)

    assert material.truncated is False
    assert material.used_segments == material.total_segments == 3
    assert material.text == "[00:00] Вступление\n[00:15] Первая мысль\n[12:34] Практика"
    assert TRANSCRIPT_TRUNCATION_MARKER.format(used=3, total=3) not in material.text


def test_long_transcript_is_truncated_at_segment_boundaries() -> None:
    transcript = make_long_transcript(count=30, text_length=90)

    material = join_transcript(transcript, max_chars=500)

    assert material.truncated is True
    assert material.total_segments == 30
    assert 0 < material.used_segments < 30
    assert len(material.text) <= 500, "маркер входит в лимит"
    lines = material.text.splitlines()
    assert lines[-1] == TRANSCRIPT_TRUNCATION_MARKER.format(
        used=material.used_segments,
        total=30,
    )
    assert len(lines) == material.used_segments + 1
    assert all(TIMECODE_LINE_RE.fullmatch(line) for line in lines[:-1])
    expected = [
        f"[{format_timecode(index * 10)}] сегмент {index:02d} " + "я" * 90
        for index in range(material.used_segments)
    ]
    assert lines[:-1] == expected, "усечение не должно резать сегмент посередине"


def test_truncation_keeps_limit_even_if_first_segment_is_too_long() -> None:
    transcript = Transcript(segments=(TranscriptSegment("я" * 1000, 0),))

    material = join_transcript(transcript, max_chars=120)

    assert material.truncated is True
    assert material.used_segments == 1
    assert len(material.text) <= 120
    first_line, marker_line = material.text.splitlines()
    assert first_line.startswith("[00:00] ")
    assert first_line.endswith("…")
    assert marker_line == TRANSCRIPT_TRUNCATION_MARKER.format(used=1, total=1)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_join_transcript_rejects_invalid_limit(limit: object) -> None:
    with pytest.raises(ValueError):
        join_transcript(make_transcript(), max_chars=limit)  # type: ignore[arg-type]


def test_join_transcript_handles_limit_smaller_than_marker() -> None:
    transcript = make_transcript()
    marker = TRANSCRIPT_TRUNCATION_MARKER.format(used=3, total=3)

    material = join_transcript(transcript, max_chars=len(marker) - 1)

    assert material.truncated is True
    assert material.is_empty is True, "обрезанный маркер не должен уходить в промпт"
    assert material.text == ""
    assert material.used_segments == 0
    assert material.total_segments == 3
    assert len(material.text) < len(marker)


def test_join_transcript_with_zero_budget_does_not_count_empty_segment() -> None:
    """``limit == len(маркера) + 1`` даёт budget 0: пустая строка — не сегмент."""
    transcript = make_transcript()
    marker = TRANSCRIPT_TRUNCATION_MARKER.format(used=3, total=3)

    material = join_transcript(transcript, max_chars=len(marker) + 1)

    assert material.truncated is True
    assert material.text == "", "пустая строка не должна начинать материал"
    assert material.used_segments == 0, "пустая строка не считается сегментом"
    assert material.total_segments == 3
    assert len(material.text) <= len(marker) + 1


def test_join_transcript_exact_marker_fit_is_degenerate_not_truncated_marker() -> None:
    transcript = make_transcript()
    marker = TRANSCRIPT_TRUNCATION_MARKER.format(used=3, total=3)

    material = join_transcript(transcript, max_chars=len(marker))

    assert material.is_empty is True
    assert material.text == ""
    assert material.used_segments == 0


def test_empty_transcript_is_rejected_before_llm_and_file(data_dir: Path) -> None:
    source = FakeVideoSource(video=make_video(transcript=Transcript()))
    client = FakeSummarizer(summary=make_summary())
    service = SummaryService(source, client, data_dir=data_dir)

    with pytest.raises(EmptyTranscriptError) as exc_info:
        service.process(URL)

    error = exc_info.value
    assert isinstance(error, UserFacingError)
    assert str(error) == MESSAGE_EMPTY_TRANSCRIPT
    assert "субтитр" in str(error)
    assert client.calls == [], "LLM не вызывается для пустого транскрипта"
    assert list(data_dir.rglob("*")) == [], "ни .md, ни временных файлов"
    assert not data_dir.exists()
    assert source.calls == [URL], "источник видео вызывается один раз"


def test_degenerate_transcript_limit_is_rejected_before_llm(data_dir: Path) -> None:
    """Лимит меньше маркера усечения не должен давать LLM пустой материал."""
    source = FakeVideoSource(video=make_video())
    client = FakeSummarizer(summary=make_summary())
    service = SummaryService(source, client, max_transcript_chars=10, data_dir=data_dir)

    with pytest.raises(EmptyTranscriptError):
        service.process(URL)

    assert client.calls == []
    assert list(data_dir.rglob("*")) == []
    assert not data_dir.exists()


def test_truncated_transcript_does_not_break_llm_json_pipeline(data_dir: Path) -> None:
    """Усечённый транскрипт остаётся текстом промпта, а JSON-ответ разбирается."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return chat_response(json.dumps(valid_payload(), ensure_ascii=False))

    client = LLMClient(
        api_key=FAKE_API_KEY,
        model=MODEL,
        base_url=BASE_URL,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    source = FakeVideoSource(video=make_video(transcript=make_long_transcript()))
    service = SummaryService(
        source,
        client,
        max_transcript_chars=500,
        data_dir=data_dir,
    )

    result = service.process(URL)

    assert result.transcript.truncated is True
    assert result.note.tldr == tuple(valid_payload()["tldr"])
    assert result.note.timeline[0].timecode == "00:00"
    assert result.path is not None and result.path.is_file()

    body = json.loads(requests[0].content)
    user_prompt = body["messages"][1]["content"]
    expected_marker = TRANSCRIPT_TRUNCATION_MARKER.format(
        used=result.transcript.used_segments,
        total=result.transcript.total_segments,
    )
    assert expected_marker in user_prompt
    assert "[00:00] сегмент 00" in user_prompt


def test_no_subtitles_propagates_and_writes_nothing(
    data_dir: Path,
) -> None:
    source = FakeVideoSource(error=NoTranscriptError())
    client = FakeSummarizer(summary=make_summary())
    service = SummaryService(source, client, data_dir=data_dir)

    with pytest.raises(NoTranscriptError):
        service.process(URL)

    assert client.calls == []
    assert list(data_dir.rglob("*.md")) == []
    assert not data_dir.exists()


def test_llm_error_propagates_and_leaves_no_partial_note(
    data_dir: Path,
    video_source: FakeVideoSource,
) -> None:
    client = FakeSummarizer(error=LLMError())
    service = SummaryService(video_source, client, data_dir=data_dir)

    with pytest.raises(LLMError):
        service.process(URL)

    assert len(client.calls) == 1
    assert list(data_dir.rglob("*.md")) == []


def test_build_note_keeps_single_string_as_one_item() -> None:
    """Строка в разделе ответа не должна разбираться на символы."""
    summary = VideoSummary(
        tldr="Одно предложение.",
        key_ideas=("Идея 1", "Идея 2", "Идея 3", "Идея 4", "Идея 5"),
        timeline=(SummaryTimelineEntry("00:00", "Вступление"),),
        quotes=(SummaryQuote("Цитата.", "00:10"),),
        follow_ups=("Проверить.",),
    )

    note = build_note(make_video(), summary)

    assert note.tldr == ("Одно предложение.",)
    assert note.key_ideas[0] == "Идея 1"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_duration_seconds": 0},
        {"max_duration_seconds": -1},
        {"max_duration_seconds": True},
        {"max_transcript_chars": 0},
        {"max_transcript_chars": True},
    ],
)
def test_service_rejects_invalid_limits(
    video_source: FakeVideoSource,
    summarizer: FakeSummarizer,
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        SummaryService(video_source, summarizer, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("url", ["", "   ", None])
def test_empty_url_is_rejected(service: SummaryService, url: object) -> None:
    with pytest.raises(ValueError):
        service.process(url)  # type: ignore[arg-type]

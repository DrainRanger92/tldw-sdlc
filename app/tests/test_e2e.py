"""Сквозной сценарий T-009: сообщение со ссылкой → ``.md``-файл.

Тест поднимает настоящую цепочку приложения без сети и токенов:

``Dispatcher`` → ``LinkHandler`` → ``SummaryService`` → ``storage``

Подменены только внешние границы: сессия Telegram, источник видео YouTube и
суммаризатор LLM — общие фейки из ``support.py``. Внутри работает настоящий
``SummaryService`` с настоящим рендером заметки, поэтому проверяется полный
шаблон: 7 полей frontmatter, 5 секций в порядке шаблона, имя файла и факт его
отправки документом.

Ошибочные сценарии идут через тот же путь: чужой Telegram ID, видео без
субтитров, слишком длинное видео и сбой LLM. В каждом случае пользователь
видит своё сообщение, а заметка (и её временные файлы) не появляется.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from aiogram.methods import SendDocument, SendMessage
from aiogram.types import FSInputFile
from support import (
    VIDEO_CHANNEL,
    VIDEO_DURATION_SECONDS,
    VIDEO_TITLE,
    VIDEO_URL,
    FakeSummarizer,
    FakeVideoSource,
    frontmatter_keys,
    make_summary,
    make_video,
    parse_frontmatter,
    request_types,
    run_message,
)

from tldw.adapters.llm import MESSAGE_LLM_UNAVAILABLE, LLMError
from tldw.adapters.youtube import MESSAGE_NO_TRANSCRIPT, NoTranscriptError
from tldw.config import load_settings
from tldw.handlers import (
    MESSAGE_ACCEPTED,
    MESSAGE_ACCESS_DENIED,
    MESSAGE_DONE,
    MESSAGE_PROCESSING,
)
from tldw.services.note import FRONTMATTER_KEYS, NOTE_STATUS, SECTION_HEADINGS
from tldw.services.summary import MESSAGE_VIDEO_TOO_LONG, SummaryService

MESSAGE_WITH_LINK = f"Посмотри это видео: {VIDEO_URL} — кажется, полезное."


def test_link_from_message_becomes_md_by_full_template(
    fake_env: dict[str, str],
) -> None:
    """Сообщение со ссылкой даёт ``.md`` по полному шаблону и уходит документом."""
    settings = load_settings(fake_env)
    video = make_video()
    summary = make_summary()
    source = FakeVideoSource(video=video)
    llm = FakeSummarizer(summary=summary)
    file_existed_at_send: list[bool] = []
    started_on = date.today().isoformat()

    def on_request(method: Any) -> None:
        if isinstance(method, SendDocument):
            file_existed_at_send.append(Path(method.document.path).is_file())

    requests = run_message(
        SummaryService.from_settings(settings, source, llm),
        MESSAGE_WITH_LINK,
        allowed_user_ids=settings.allowed_user_ids,
        on_request=on_request,
    )
    finished_on = date.today().isoformat()

    # Путь пользователя: «принял → обрабатываю → документ → готово».
    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]
    assert requests[0].text == MESSAGE_ACCEPTED
    assert requests[1].text == MESSAGE_PROCESSING
    assert requests[3].text == MESSAGE_DONE

    # Ссылка дошла до YouTube-адаптера без искажений, LLM получил транскрипт
    # с таймкодами и метаданные видео.
    assert source.calls == [VIDEO_URL]
    assert len(llm.calls) == 1
    transcript, title, channel = llm.calls[0]
    assert title == VIDEO_TITLE
    assert channel == VIDEO_CHANNEL
    assert "[00:00] Вступление и план видео" in transcript
    assert "[00:12] Метод интервальных повторений" in transcript
    assert "[01:05] Сон закрепляет память" in transcript

    # Файл лежит в DATA_DIR, существовал в момент отправки и ушёл документом.
    document = requests[2].document
    assert isinstance(document, FSInputFile)
    assert document.filename == "как-учиться-эффективно.md"
    assert file_existed_at_send == [True]
    note_path = Path(document.path)
    assert note_path.parent == settings.data_dir
    assert note_path.is_file()

    markdown = note_path.read_text(encoding="utf-8")
    assert markdown.endswith("\n") and not markdown.endswith("\n\n")

    # --- Полный шаблон: 7 полей frontmatter в фиксированном порядке. ---
    frontmatter, body = parse_frontmatter(markdown)
    # Заметка могла быть создана до или после полуночи: оба дня валидны.
    assert frontmatter.pop("date") in {started_on, finished_on}
    assert frontmatter == {
        "title": VIDEO_TITLE,
        "source": VIDEO_URL,
        "channel": VIDEO_CHANNEL,
        "duration": VIDEO_DURATION_SECONDS,
        "tags": [],
        "status": NOTE_STATUS,
    }
    assert frontmatter_keys(markdown) == list(FRONTMATTER_KEYS)

    # --- Полный шаблон: 5 секций в фиксированном порядке. ---
    positions = [body.index(heading) for heading in SECTION_HEADINGS]
    assert positions == sorted(positions), "секции должны идти в порядке шаблона"
    assert body.count("## ") == len(SECTION_HEADINGS), "лишних секций быть не должно"
    assert " ".join(summary.tldr) in body
    for idea in summary.key_ideas:
        assert f"- {idea}" in body
    for entry in summary.timeline:
        assert f"- [{entry.timecode}] {entry.title}" in body
    for quote in summary.quotes:
        assert f"> {quote.text}\n> — [{quote.timecode}]" in body
    for item in summary.follow_ups:
        assert f"- [ ] {item}" in body

    # В каталоге только готовая заметка: временных файлов атомарной записи нет.
    assert sorted(path.name for path in settings.data_dir.iterdir()) == [document.filename]


def test_stranger_id_gets_access_denied_without_any_processing(
    fake_env: dict[str, str],
    tmp_path: Path,
) -> None:
    """Чужой ID не доходит ни до YouTube, ни до LLM, ни до файла."""
    settings = load_settings({**fake_env, "TELEGRAM_ALLOWED_USER_IDS": "1001"})
    source = FakeVideoSource(video=make_video())
    llm = FakeSummarizer(summary=make_summary())

    requests = run_message(
        SummaryService.from_settings(settings, source, llm),
        MESSAGE_WITH_LINK,
        allowed_user_ids=settings.allowed_user_ids,
        user_id=42,
    )

    assert request_types(requests) == [SendMessage]
    assert requests[0].text == MESSAGE_ACCESS_DENIED
    assert source.calls == []
    assert llm.calls == []
    assert list(tmp_path.rglob("*.md")) == []


@pytest.mark.parametrize(
    ("case", "expected_message"),
    [
        ("no-transcript", MESSAGE_NO_TRANSCRIPT),
        (
            "too-long",
            MESSAGE_VIDEO_TOO_LONG.format(duration="02:00:01", limit="02:00:00"),
        ),
        ("llm-error", MESSAGE_LLM_UNAVAILABLE),
    ],
)
def test_error_scenarios_show_their_message_and_leave_no_note(
    case: str,
    expected_message: str,
    fake_env: dict[str, str],
    tmp_path: Path,
) -> None:
    """Ошибки после «принял» дают свой текст и не создают ``.md``."""
    settings = load_settings(fake_env)
    source = FakeVideoSource(video=make_video())
    llm = FakeSummarizer(summary=make_summary())
    if case == "no-transcript":
        source = FakeVideoSource(error=NoTranscriptError())
    elif case == "too-long":
        source = FakeVideoSource(video=make_video(duration_seconds=7201))
    elif case == "llm-error":
        llm = FakeSummarizer(error=LLMError())
    else:  # pragma: no cover - защита от опечатки в параметризации
        raise AssertionError(f"неизвестный кейс {case}")

    requests = run_message(
        SummaryService.from_settings(settings, source, llm),
        MESSAGE_WITH_LINK,
        allowed_user_ids=settings.allowed_user_ids,
    )

    assert request_types(requests) == [SendMessage, SendMessage, SendMessage]
    assert requests[0].text == MESSAGE_ACCEPTED
    assert requests[1].text == MESSAGE_PROCESSING
    assert requests[-1].text == expected_message
    assert "Traceback" not in requests[-1].text, "стектрейс не должен уходить пользователю"
    assert source.calls == [VIDEO_URL]
    assert list(tmp_path.rglob("*.md")) == []
    assert list(tmp_path.rglob("*.tmp")) == []

    if case == "llm-error":
        assert len(llm.calls) == 1
    else:
        assert llm.calls == [], "до ошибки источник не должен тратить вызов LLM"

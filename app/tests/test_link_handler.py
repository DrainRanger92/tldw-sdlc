"""Тесты Telegram-обработчика ссылки на фейковом боте.

Сеть и токены не нужны: aiogram-сессия подменена записывающей ``FakeSession``,
сервис конспекта — фейком (кроме одного интеграционного кейса с настоящим
``SummaryService`` и каталогом данных в ``tmp_path``). Проверяются:

- порядок ответов «принял → обрабатываю → документ → готово»;
- тяжёлый вызов сервиса выполняется в рабочем потоке и не блокирует loop;
- текст без ссылки даёт подсказку про YouTube, а из нескольких ссылок
  обрабатывается первая;
- доступ по allowlist: разрешённый ID обрабатывается, чужой получает «доступ
  закрыт» до разбора ссылки и вызовов сервиса, пустой список пропускает всех;
- ``.md`` лежит в ``DATA_DIR`` до отправки и уходит документом с именем
  из шаблона заметки;
- активный шаблон отправителя доходит до сервиса конспекта, встроенный путь
  остаётся байт-в-байт прежним, сбой хранилища и повреждённый файл деградируют
  до встроенного шаблона с ``WARNING``, а шаблон одного пользователя не
  применяется к заметке другого (T-008).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aiogram.methods import SendDocument, SendMessage
from aiogram.types import FSInputFile
from support import (
    FAKE_BEARER_TOKEN,
    FAKE_BOT_TOKEN,
    FAKE_ENV_KEY,
    VIDEO_URL,
    FakeSummarizer,
    FakeTemplateRepository,
    FakeVideoSource,
    make_harness,
    make_result,
    make_summary,
    make_video,
    request_types,
    run_message,
)

from tldw.adapters.llm import MESSAGE_LLM_UNAVAILABLE, LLMError
from tldw.adapters.youtube import (
    MESSAGE_NO_TRANSCRIPT,
    MESSAGE_NOT_YOUTUBE,
    NoTranscriptError,
    NotYouTubeURLError,
)
from tldw.config import load_settings
from tldw.errors import MESSAGE_UNEXPECTED, REDACTED
from tldw.handlers import (
    MESSAGE_ACCEPTED,
    MESSAGE_ACCESS_DENIED,
    MESSAGE_DONE,
    MESSAGE_NEED_LINK,
    MESSAGE_NO_FILE,
    MESSAGE_PROCESSING,
    create_router,
    extract_first_url,
)
from tldw.services.note import SECTION_HEADINGS, Note, note_filename
from tldw.services.summary import (
    MESSAGE_VIDEO_TOO_LONG,
    SummaryResult,
    SummaryService,
    TranscriptText,
    VideoTooLongError,
)
from tldw.services.template import ActiveTemplate, NoteTemplate, TemplateOrigin, TemplateService
from tldw.storage import TemplateStore


@dataclass
class FakeSummaryService:
    """Фейковый сервис конспекта: результат, ошибка и запись вызовов.

    ``templates`` хранит снимок шаблона, переданный обработчиком: ``None`` —
    прежний встроенный путь MVP, ``ActiveTemplate`` — пользовательский шаблон.
    """

    result: SummaryResult | None = None
    error: Exception | None = None
    started: threading.Event | None = None
    release: threading.Event | None = None
    calls: list[str] = field(default_factory=list)
    templates: list[ActiveTemplate | None] = field(default_factory=list)
    thread_ids: list[int] = field(default_factory=list)

    def process(
        self,
        url: str,
        *,
        template: ActiveTemplate | None = None,
    ) -> SummaryResult:
        self.calls.append(url)
        self.templates.append(template)
        self.thread_ids.append(threading.get_ident())
        if self.started is not None:
            self.started.set()
        if self.release is not None and not self.release.wait(timeout=5):
            raise AssertionError("тест не отпустил обработку")
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def test_success_flow_sends_accepted_processing_document_and_done(tmp_path: Path) -> None:
    result = make_result(tmp_path)
    service = FakeSummaryService(result=result)
    file_existed_at_send: list[bool] = []

    def on_request(method: Any) -> None:
        if isinstance(method, SendDocument):
            file_existed_at_send.append(Path(method.document.path).is_file())

    requests = run_message(service, VIDEO_URL, on_request=on_request)

    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]
    assert requests[0].text == MESSAGE_ACCEPTED
    assert "принял" in requests[0].text.lower()
    assert requests[1].text == MESSAGE_PROCESSING
    assert "обрабатываю" in requests[1].text.lower()

    document = requests[2].document
    assert isinstance(document, FSInputFile)
    assert document.filename == result.filename
    assert Path(document.path) == result.path
    assert file_existed_at_send == [True], "файл должен существовать до отправки"

    assert requests[3].text == MESSAGE_DONE
    assert "готово" in requests[3].text.lower()
    assert service.calls == [VIDEO_URL]


@pytest.mark.parametrize("text", ["привет", "посоветуй фильм про космос", "как дела?"])
def test_text_without_link_asks_for_youtube_link(text: str) -> None:
    service = FakeSummaryService()

    requests = run_message(service, text)

    assert request_types(requests) == [SendMessage]
    assert requests[0].text == MESSAGE_NEED_LINK
    assert "YouTube" in requests[0].text
    assert service.calls == []


def test_allowed_user_id_is_processed(tmp_path: Path) -> None:
    service = FakeSummaryService(result=make_result(tmp_path))

    requests = run_message(service, VIDEO_URL, allowed_user_ids=(42, 1001), user_id=42)

    assert service.calls == [VIDEO_URL]
    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]
    assert requests[0].text == MESSAGE_ACCEPTED


def test_stranger_gets_access_denied_and_no_processing() -> None:
    service = FakeSummaryService()

    requests = run_message(service, VIDEO_URL, allowed_user_ids=(1001,), user_id=42)

    assert request_types(requests) == [SendMessage]
    assert requests[0].text == MESSAGE_ACCESS_DENIED
    assert "доступ закрыт" in requests[0].text.lower()
    assert service.calls == [], "чужой ID не должен запускать обработку видео"


def test_access_is_checked_before_link_parsing() -> None:
    """Посторонний без ссылки получает отказ, а не подсказку про YouTube."""
    service = FakeSummaryService()

    requests = run_message(
        service, "привет, а что ты умеешь?", allowed_user_ids=(1001,), user_id=42
    )

    assert request_types(requests) == [SendMessage]
    assert requests[0].text == MESSAGE_ACCESS_DENIED
    assert requests[0].text != MESSAGE_NEED_LINK


def test_message_without_sender_is_denied_when_allowlist_is_set() -> None:
    """При заданном allowlist запрос без отправителя (канал) закрыт."""
    service = FakeSummaryService()

    requests = run_message(
        service,
        VIDEO_URL,
        allowed_user_ids=(1001,),
        with_sender=False,
    )

    assert request_types(requests) == [SendMessage]
    assert requests[0].text == MESSAGE_ACCESS_DENIED
    assert service.calls == []


def test_empty_allowlist_lets_a_stranger_through(tmp_path: Path) -> None:
    service = FakeSummaryService(result=make_result(tmp_path))

    requests = run_message(service, VIDEO_URL, allowed_user_ids=(), user_id=777)

    assert service.calls == [VIDEO_URL]
    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]


def test_first_link_in_text_is_processed(tmp_path: Path) -> None:
    service = FakeSummaryService(result=make_result(tmp_path))
    text = "Смотри https://youtu.be/dQw4w9WgXcQ, а потом https://youtu.be/aaaaaaaaaaa."

    requests = run_message(service, text)

    assert service.calls == ["https://youtu.be/dQw4w9WgXcQ"]
    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]


def test_non_youtube_link_reaches_service_for_adapter_validation() -> None:
    """Обработчик не фильтрует домены: не-YouTube отвергает адаптер (FR-2)."""
    service = FakeSummaryService(error=NotYouTubeURLError())

    requests = run_message(service, "Вот https://example.com/page — посмотри")

    assert service.calls == ["https://example.com/page"]
    assert isinstance(requests[-1], SendMessage)


def test_processing_runs_in_a_worker_thread(tmp_path: Path) -> None:
    service = FakeSummaryService(result=make_result(tmp_path))
    loop_thread: list[int] = []

    async def scenario() -> None:
        loop_thread.append(threading.get_ident())
        harness = make_harness(service)
        try:
            await harness.send(VIDEO_URL)
            await harness.handler.wait_idle()
        finally:
            await harness.close()

    asyncio.run(scenario())

    assert service.thread_ids, "сервис не был вызван"
    assert all(thread_id != loop_thread[0] for thread_id in service.thread_ids), (
        "process должен выполняться вне потока event loop (asyncio.to_thread)"
    )


def test_event_loop_is_not_blocked_while_video_is_processed(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    service = FakeSummaryService(
        result=make_result(tmp_path),
        started=started,
        release=release,
    )

    async def scenario() -> list[Any]:
        harness = make_harness(service)
        try:
            # Обработчик обязан вернуться сразу, не дожидаясь сервиса.
            started_at = time.monotonic()
            await asyncio.wait_for(harness.send(VIDEO_URL), timeout=1.0)
            assert time.monotonic() - started_at < 1.0
            assert not release.is_set()

            # Ждём старта сервиса в рабочем потоке; сам цикл ожидания
            # доказывает, что event loop жив, пока поток заблокирован.
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)

            assert started.is_set(), "обработка не стартовала в рабочем потоке"
            assert not release.is_set(), "сервис не должен завершиться до release"
            assert request_types(harness.session.requests) == [SendMessage, SendMessage]
        finally:
            release.set()
            await harness.handler.wait_idle()
            await harness.close()
        return harness.session.requests

    requests = asyncio.run(scenario())

    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]


def test_service_error_sends_generic_failure() -> None:
    service = FakeSummaryService(error=RuntimeError("boom"))

    requests = run_message(service, VIDEO_URL)

    assert request_types(requests) == [SendMessage, SendMessage, SendMessage]
    assert requests[-1].text == MESSAGE_UNEXPECTED
    assert service.calls == [VIDEO_URL]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (NotYouTubeURLError(), MESSAGE_NOT_YOUTUBE),
        (NoTranscriptError(), MESSAGE_NO_TRANSCRIPT),
        (
            VideoTooLongError(duration_seconds=7201, max_duration_seconds=7200),
            MESSAGE_VIDEO_TOO_LONG.format(duration="02:00:01", limit="02:00:00"),
        ),
        (LLMError(), MESSAGE_LLM_UNAVAILABLE),
    ],
    ids=["not-youtube", "no-transcript", "too-long", "llm-unavailable"],
)
def test_known_error_gets_its_own_message_and_logs_reason(
    error: Exception,
    expected: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Пользователь видит свой текст, в логе есть причина, стектрейса нет."""
    service = FakeSummaryService(error=error)

    with caplog.at_level(logging.WARNING, logger="tldw.handlers.link"):
        requests = run_message(service, VIDEO_URL)

    assert request_types(requests) == [SendMessage, SendMessage, SendMessage]
    answer = requests[-1].text
    assert answer == expected
    assert str(error) in answer
    assert "Traceback" not in answer
    assert "RuntimeError" not in answer

    records = [record for record in caplog.records if record.name == "tldw.handlers.link"]
    assert records, "сбой должен попасть в лог"
    assert all(record.levelno == logging.WARNING for record in records), (
        "известная ошибка логируется предупреждением, без ERROR"
    )
    assert type(error).__name__ in caplog.text
    assert str(error) in caplog.text
    assert VIDEO_URL not in caplog.text, "полная ссылка не должна попадать в лог"


def test_unknown_error_gets_generic_message_and_error_log_with_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = FakeSummaryService(error=RuntimeError("boom"))

    with caplog.at_level(logging.ERROR, logger="tldw.handlers.link"):
        requests = run_message(service, VIDEO_URL)

    assert request_types(requests) == [SendMessage, SendMessage, SendMessage]
    assert requests[-1].text == MESSAGE_UNEXPECTED
    assert "Traceback" not in requests[-1].text
    assert "boom" not in requests[-1].text

    errors = [
        record
        for record in caplog.records
        if record.name == "tldw.handlers.link" and record.levelno == logging.ERROR
    ]
    assert len(errors) == 1
    logged = errors[0].getMessage()
    assert "RuntimeError: boom" in logged
    assert "Traceback (most recent call last)" in logged
    assert VIDEO_URL not in logged


def test_failure_log_redacts_tokens_and_api_keys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    leaked = (
        f"https://api.telegram.org/bot{FAKE_BOT_TOKEN}/sendMessage "
        f"Authorization: Bearer {FAKE_BEARER_TOKEN} LLM_API_KEY={FAKE_ENV_KEY}"
    )
    service = FakeSummaryService(error=RuntimeError(leaked))

    with caplog.at_level(logging.ERROR, logger="tldw.handlers.link"):
        requests = run_message(service, VIDEO_URL)

    assert requests[-1].text == MESSAGE_UNEXPECTED
    for secret in (FAKE_BOT_TOKEN, FAKE_BEARER_TOKEN, FAKE_ENV_KEY):
        assert secret not in caplog.text, "секрет не должен попадать в лог"
    assert REDACTED in caplog.text
    assert VIDEO_URL not in caplog.text


@pytest.mark.parametrize("indent", ["", "    "], ids=["plain", "indented"])
def test_failure_log_does_not_contain_full_transcript(
    indent: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    transcript = "\n".join(
        f"{indent}сегмент-{index} " + "x" * 40 for index in range(120)
    )
    service = FakeSummaryService(error=RuntimeError(transcript))

    with caplog.at_level(logging.ERROR, logger="tldw.handlers.link"):
        requests = run_message(service, VIDEO_URL)

    assert requests[-1].text == MESSAGE_UNEXPECTED
    assert transcript not in caplog.text
    assert "\n    сегмент-" not in caplog.text, "строки с отступом не должны идти в лог"
    assert "фрагмент обрезан" in caplog.text


@pytest.mark.parametrize("with_path", [False, True], ids=["path-none", "file-missing"])
def test_missing_saved_file_is_reported(with_path: bool, tmp_path: Path) -> None:
    service = FakeSummaryService(
        result=SummaryResult(
            note=Note(title="Без файла"),
            markdown="# Без файла\n",
            filename="bez-fayla.md",
            transcript=TranscriptText(),
            path=(tmp_path / "missing.md") if with_path else None,
        )
    )

    requests = run_message(service, VIDEO_URL)

    assert request_types(requests) == [SendMessage, SendMessage, SendMessage]
    assert requests[-1].text == MESSAGE_NO_FILE


def test_create_router_registers_link_handler() -> None:
    router = create_router(FakeSummaryService())

    assert router.name == "tldw"
    assert len(router.message.handlers) == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Смотри https://youtu.be/dQw4w9WgXcQ.", "https://youtu.be/dQw4w9WgXcQ"),
        ("youtu.be/dQw4w9WgXcQ", "youtu.be/dQw4w9WgXcQ"),
        ("youtube.com/watch?v=dQw4w9WgXcQ", "youtube.com/watch?v=dQw4w9WgXcQ"),
        (
            "https://youtube.com/shorts/dQw4w9WgXcQ?feature=share",
            "https://youtube.com/shorts/dQw4w9WgXcQ?feature=share",
        ),
        ("https://example.com https://youtu.be/bbb", "https://example.com"),
        ("<https://youtu.be/abc>", "https://youtu.be/abc"),
        ("Моя почта user@example.com", None),
        ("никакой ссылки", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_first_url(text: str | None, expected: str | None) -> None:
    assert extract_first_url(text) == expected


def test_note_is_saved_in_data_dir_before_sending(fake_env: dict[str, str]) -> None:
    settings = load_settings(fake_env)
    video = make_video()
    service = SummaryService(
        FakeVideoSource(video=video),
        FakeSummarizer(summary=make_summary()),
        data_dir=settings.data_dir,
    )
    file_existed_at_send: list[bool] = []

    def on_request(method: Any) -> None:
        if isinstance(method, SendDocument):
            file_existed_at_send.append(Path(method.document.path).is_file())

    requests = run_message(service, VIDEO_URL, on_request=on_request)

    expected_path = settings.data_dir / note_filename(Note(title=video.metadata.title))
    assert expected_path.is_file()
    assert expected_path.parent == settings.data_dir

    document = next(request for request in requests if isinstance(request, SendDocument))
    assert Path(document.document.path) == expected_path
    assert document.document.filename == expected_path.name
    assert "## TL;DR" in expected_path.read_text(encoding="utf-8")
    assert file_existed_at_send == [True]


# --- Активный шаблон в обработчике ссылки (T-008) ---------------------------

#: Пользовательский шаблон сценария: разделы в порядке применения и тон.
SAVED_TEMPLATE = NoteTemplate(sections=("Коротко", "Примеры кода"), tone="короче")
#: Другой шаблон — проверка «пока не сменён».
CHANGED_TEMPLATE = NoteTemplate(sections=("Что попробовать",), tone="строго по делу")


class BrokenTemplateRepository:
    """Хранилище, чтение которого падает: проверка деградации до встроенного."""

    def __init__(self) -> None:
        self.loads = 0

    def load(self, user_id: int) -> tuple[tuple[str, ...], str] | None:
        self.loads += 1
        raise OSError("каталог данных недоступен")

    def save(self, user_id: int, record: tuple[tuple[str, ...], str]) -> None:
        raise AssertionError("обработчик ссылки не должен сохранять шаблон")

    def delete(self, user_id: int) -> None:
        raise AssertionError("обработчик ссылки не должен сбрасывать шаблон")


def saved_template_service(
    user_id: int = 42,
    template: NoteTemplate = SAVED_TEMPLATE,
) -> tuple[TemplateService, FakeTemplateRepository]:
    """Сервис шаблонов с заранее сохранённым шаблоном пользователя."""
    repository = FakeTemplateRepository()
    repository.records[user_id] = (template.sections, template.tone)
    return TemplateService(repository), repository


def sent_markdown(request: Any) -> tuple[Path, str]:
    """Путь и содержимое `.md`, отправленного документом."""
    assert isinstance(request, SendDocument)
    path = Path(request.document.path)
    return path, path.read_text(encoding="utf-8")


def test_saved_template_is_passed_to_summary_service(tmp_path: Path) -> None:
    """Снимок активного шаблона отправителя уходит в сервис конспекта."""
    service = FakeSummaryService(result=make_result(tmp_path))
    templates, repository = saved_template_service()

    requests = run_message(
        service,
        VIDEO_URL,
        user_id=42,
        template_service=templates,
    )

    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]
    assert requests[0].text == MESSAGE_ACCEPTED
    assert requests[-1].text == MESSAGE_DONE
    assert service.calls == [VIDEO_URL]

    (template,) = service.templates
    assert isinstance(template, ActiveTemplate)
    assert template.sections == SAVED_TEMPLATE.sections
    assert template.tone == SAVED_TEMPLATE.tone
    assert template.origin is TemplateOrigin.USER
    assert repository.loads == [42], "шаблон читается для отправителя ссылки"


def test_without_user_template_the_mvp_path_is_byte_identical(
    fake_env: dict[str, str],
) -> None:
    """Без пользовательского шаблона сообщения и `.md` те же, что без сервиса."""
    settings = load_settings(fake_env)
    repository = FakeTemplateRepository()
    templates = TemplateService(repository)

    def run(template_service: TemplateService | None) -> tuple[list[Any], str, Any]:
        summarizer = FakeSummarizer(summary=make_summary())
        service = SummaryService(
            FakeVideoSource(video=make_video()),
            summarizer,
            data_dir=settings.data_dir,
        )
        requests = run_message(service, VIDEO_URL, template_service=template_service)
        document = next(request for request in requests if isinstance(request, SendDocument))
        _, markdown = sent_markdown(document)
        return requests, markdown, summarizer

    requests_with, markdown_with, llm_with = run(templates)
    requests_without, markdown_without, llm_without = run(None)

    assert request_types(requests_with) == request_types(requests_without)
    assert [getattr(request, "text", None) for request in requests_with] == [
        getattr(request, "text", None) for request in requests_without
    ]
    assert markdown_with == markdown_without
    # На встроенном пути сервис вызывается без разделов и тона (прежний контракт).
    assert llm_with.templates == [(None, "")]
    assert llm_without.templates == [(None, "")]
    assert repository.loads == [42], "хватило одного чтения шаблона на обработку"


def test_template_storage_failure_degrades_to_builtin_with_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Сбой чтения хранилища не теряет ссылку: встроенный шаблон и ``WARNING``."""
    repository = BrokenTemplateRepository()
    service = FakeSummaryService(result=make_result(tmp_path))

    with caplog.at_level(logging.WARNING, logger="tldw.handlers.link"):
        requests = run_message(
            service,
            VIDEO_URL,
            template_service=TemplateService(repository),
        )

    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]
    assert requests[-1].text == MESSAGE_DONE
    assert service.templates == [None], "при сбое применяется встроенный шаблон"
    assert repository.loads == 1

    warnings = [
        record
        for record in caplog.records
        if record.name == "tldw.handlers.link" and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "встроенный" in warnings[0].getMessage()
    assert "OSError" in warnings[0].getMessage()
    assert VIDEO_URL not in caplog.text, "ссылка не должна попадать в лог"


def test_corrupt_saved_template_degrades_to_builtin_with_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Повреждённый файл шаблона даёт встроенный шаблон, ссылка обрабатывается."""
    repository = FakeTemplateRepository()
    repository.records[42] = ((), "")  # пустой список разделов — вне лимитов FR-2
    service = FakeSummaryService(result=make_result(tmp_path))

    with caplog.at_level(logging.WARNING, logger="tldw.services.template"):
        requests = run_message(
            service,
            VIDEO_URL,
            template_service=TemplateService(repository),
        )

    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]
    assert service.templates == [None]
    warnings = [
        record
        for record in caplog.records
        if record.name == "tldw.services.template" and record.levelno == logging.WARNING
    ]
    assert warnings, "повреждённый шаблон должен давать предупреждение"
    assert "встроенный" in warnings[0].getMessage()


def test_corrupt_template_file_in_data_dir_degrades_to_builtin(
    fake_env: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Битый JSON в ``DATA_DIR`` даёт встроенный шаблон, ссылка обрабатывается."""
    settings = load_settings(fake_env)
    templates_dir = settings.data_dir / "templates"
    templates_dir.mkdir(parents=True)
    corrupt = templates_dir / "42.json"
    corrupt.write_text('{"version": 1, "sections": [', encoding="utf-8")
    service = FakeSummaryService(result=make_result(settings.data_dir))

    with caplog.at_level(logging.WARNING, logger="tldw.storage.templates"):
        requests = run_message(
            service,
            VIDEO_URL,
            template_service=TemplateService(TemplateStore(settings.data_dir)),
        )

    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]
    assert requests[-1].text == MESSAGE_DONE
    assert service.templates == [None]
    assert corrupt.is_file(), "повреждённый файл не удаляется молча"
    warnings = [
        record
        for record in caplog.records
        if record.name == "tldw.storage.templates" and record.levelno == logging.WARNING
    ]
    assert warnings, "битый файл шаблона должен давать предупреждение"
    assert "встроенный" in warnings[0].getMessage()


def test_template_is_not_read_for_a_stranger(tmp_path: Path) -> None:
    """Allowlist проверяется раньше чтения шаблона: чужой шаблон не читается."""
    service = FakeSummaryService(result=make_result(tmp_path))
    templates, repository = saved_template_service(user_id=42)

    requests = run_message(
        service,
        VIDEO_URL,
        allowed_user_ids=(1001,),
        user_id=42,
        template_service=templates,
    )

    assert request_types(requests) == [SendMessage]
    assert requests[0].text == MESSAGE_ACCESS_DENIED
    assert service.calls == []
    assert repository.loads == [], "посторонний не должен читать шаблон"


def test_message_without_sender_uses_builtin_without_lookup(tmp_path: Path) -> None:
    """У сообщения из канала нет отправителя: шаблон не читается, путь встроенный."""
    service = FakeSummaryService(result=make_result(tmp_path))
    templates, repository = saved_template_service()

    requests = run_message(
        service,
        VIDEO_URL,
        with_sender=False,
        template_service=templates,
    )

    assert request_types(requests) == [SendMessage, SendMessage, SendDocument, SendMessage]
    assert service.templates == [None]
    assert repository.loads == []


def test_user_template_is_not_applied_to_another_user(
    fake_env: dict[str, str],
    tmp_path: Path,
) -> None:
    """Изоляция: шаблон A не попадает в заметку B, B получает заметку MVP-вида."""
    settings = load_settings(fake_env)
    repository = FakeTemplateRepository()
    repository.records[42] = (SAVED_TEMPLATE.sections, SAVED_TEMPLATE.tone)
    templates = TemplateService(repository)

    def run_for(user_id: int, title: str, url: str) -> tuple[str, FakeSummarizer]:
        summarizer = FakeSummarizer(summary=make_summary(), sections_from_request=True)
        service = SummaryService.from_settings(
            settings,
            FakeVideoSource(video=make_video(title=title, url=url)),
            summarizer,
        )
        requests = run_message(
            service,
            url,
            user_id=user_id,
            allowed_user_ids=(42, 1001),
            template_service=templates,
        )
        document = next(request for request in requests if isinstance(request, SendDocument))
        _, markdown = sent_markdown(document)
        return markdown, summarizer

    markdown_a, llm_a = run_for(42, "Урок пользователя A", VIDEO_URL)
    markdown_b, llm_b = run_for(1001, "Урок пользователя B", "https://youtu.be/bbbbbbbbbbb")

    # A: разделы своего шаблона в своём порядке.
    assert llm_a.templates == [(SAVED_TEMPLATE.sections, SAVED_TEMPLATE.tone)]
    positions = [markdown_a.index(f"## {heading}") for heading in SAVED_TEMPLATE.sections]
    assert positions == sorted(positions)
    assert "## TL;DR" not in markdown_a

    # B: прежние пять секций MVP, чужой шаблон не применён.
    assert llm_b.templates == [(None, "")]
    for heading in SECTION_HEADINGS:
        assert heading in markdown_b
    for heading in SAVED_TEMPLATE.sections:
        assert f"## {heading}" not in markdown_b

    # Общее хранилище: у каждого пользователя свой ключ, готовых заметок две.
    assert sorted(repository.loads) == [42, 1001]
    assert sorted(path.name for path in settings.data_dir.glob("*.md")) == [
        "урок-пользователя-a.md",
        "урок-пользователя-b.md",
    ]


def test_open_allowlist_keeps_template_isolation(
    fake_env: dict[str, str],
) -> None:
    """При пустом allowlist доступ открыт всем, изоляция по user ID сохраняется."""
    settings = load_settings(fake_env)
    repository = FakeTemplateRepository()
    repository.records[42] = (SAVED_TEMPLATE.sections, SAVED_TEMPLATE.tone)
    templates = TemplateService(repository)

    def template_for(user_id: int) -> tuple[tuple[str, ...] | None, str]:
        summarizer = FakeSummarizer(summary=make_summary(), sections_from_request=True)
        service = SummaryService.from_settings(
            settings,
            FakeVideoSource(video=make_video(title=f"Урок {user_id}")),
            summarizer,
        )
        run_message(service, VIDEO_URL, user_id=user_id, template_service=templates)
        assert len(summarizer.templates) == 1
        return summarizer.templates[0]

    assert template_for(42) == (SAVED_TEMPLATE.sections, SAVED_TEMPLATE.tone)
    assert template_for(1001) == (None, "")
    assert repository.loads == [42, 1001]


def test_template_stays_active_for_next_links_until_changed_or_reset(
    fake_env: dict[str, str],
) -> None:
    """Сохранённый шаблон действует на все ссылки; готовые заметки не меняются."""
    settings = load_settings(fake_env)
    templates = TemplateService(FakeTemplateRepository())
    templates.save(42, SAVED_TEMPLATE)

    def run(title: str, url: str) -> tuple[Path, str, FakeSummarizer, FakeVideoSource]:
        source = FakeVideoSource(video=make_video(title=title, url=url))
        summarizer = FakeSummarizer(summary=make_summary(), sections_from_request=True)
        service = SummaryService.from_settings(settings, source, summarizer)
        requests = run_message(service, url, template_service=templates)
        document = next(request for request in requests if isinstance(request, SendDocument))
        path, markdown = sent_markdown(document)
        return path, markdown, summarizer, source

    first_path, first_markdown, first_llm, first_source = run("Первый урок", VIDEO_URL)
    _, second_markdown, second_llm, _ = run(
        "Второй урок", "https://youtu.be/bbbbbbbbbbb"
    )
    assert first_llm.templates == [(SAVED_TEMPLATE.sections, SAVED_TEMPLATE.tone)]
    assert second_llm.templates == [(SAVED_TEMPLATE.sections, SAVED_TEMPLATE.tone)]
    assert f"## {SAVED_TEMPLATE.sections[0]}" in second_markdown

    # Смена шаблона действует на следующую ссылку.
    templates.save(42, CHANGED_TEMPLATE)
    _, third_markdown, third_llm, _ = run(
        "Третий урок", "https://youtu.be/ccccccccccc"
    )
    assert third_llm.templates == [(CHANGED_TEMPLATE.sections, CHANGED_TEMPLATE.tone)]
    assert f"## {CHANGED_TEMPLATE.sections[0]}" in third_markdown
    assert f"## {SAVED_TEMPLATE.sections[0]}" not in third_markdown

    # Сброс возвращает встроенный шаблон.
    templates.reset(42)
    fourth_path, fourth_markdown, fourth_llm, _ = run(
        "Четвёртый урок", "https://youtu.be/ddddddddddd"
    )
    assert fourth_llm.templates == [(None, "")]
    for heading in SECTION_HEADINGS:
        assert heading in fourth_markdown

    # Уже готовые заметки не перегенерируются: файл на месте и не изменился.
    assert first_path.is_file()
    assert first_path.read_text(encoding="utf-8") == first_markdown
    assert "## TL;DR" not in first_markdown
    assert first_source.calls == [VIDEO_URL]
    assert fourth_path != first_path

"""Тесты домена шаблона заметки: модель, лимиты, слияние правки и сервис.

Все проверки офлайновые: Telegram, YouTube и LLM не используются, хранилище
подменяется фейком в памяти, файлы не пишутся. ``tldw.services.template`` —
нейтральный модуль (без ``httpx`` и aiogram), поэтому тесты не требуют сети и
токенов.
"""

from __future__ import annotations

import logging
import re
from dataclasses import FrozenInstanceError

import pytest
from support import FakeTemplateRepository

import tldw.services
import tldw.services.template as template_module
from tldw.errors import UserFacingError
from tldw.services import (
    BUILTIN_ACTIVE_TEMPLATE,
    BUILTIN_SECTIONS,
    BUILTIN_TEMPLATE,
    MAX_DESCRIPTION_LENGTH,
    MAX_HEADING_LENGTH,
    MAX_SECTION_ITEMS,
    MAX_SECTIONS,
    MAX_TONE_LENGTH,
    MIN_DESCRIPTION_LENGTH,
    MIN_SECTION_ITEMS,
    MIN_SECTIONS,
    ActiveTemplate,
    NoteTemplate,
    TemplateOrigin,
    TemplateParseError,
    TemplatePatch,
    TemplateRepository,
    TemplateService,
    TemplateValidationError,
    merge_template,
)
from tldw.services.note import (
    SECTION_FOLLOW_UPS,
    SECTION_HEADINGS,
    SECTION_KEY_IDEAS,
    SECTION_QUOTES,
    SECTION_TIMELINE,
    SECTION_TLDR,
    Note,
    render_note,
)

CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
TEMPLATE_LOGGER = "tldw.services.template"

#: Публичный контракт T-001: имена обязаны жить и в модуле, и в пакете.
TEMPLATE_API = (
    "ActiveTemplate",
    "BUILTIN_ACTIVE_TEMPLATE",
    "BUILTIN_SECTIONS",
    "BUILTIN_TEMPLATE",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_HEADING_LENGTH",
    "MAX_SECTION_ITEMS",
    "MAX_SECTIONS",
    "MAX_TONE_LENGTH",
    "MIN_DESCRIPTION_LENGTH",
    "MIN_SECTION_ITEMS",
    "MIN_SECTIONS",
    "NoteTemplate",
    "TemplateOrigin",
    "TemplateParseError",
    "TemplatePatch",
    "TemplateRepository",
    "TemplateService",
    "TemplateValidationError",
    "merge_template",
)


# --- Публичный контракт и встроенный шаблон ---------------------------------


@pytest.mark.parametrize("name", TEMPLATE_API)
def test_template_module_defines_public_name(name: str) -> None:
    assert hasattr(template_module, name), f"в tldw.services.template нет {name}"


@pytest.mark.parametrize("name", TEMPLATE_API)
def test_services_package_reexports_template_name(name: str) -> None:
    assert name in tldw.services.__all__
    assert getattr(tldw.services, name) is getattr(template_module, name)


def test_limit_constants_match_spec() -> None:
    assert MIN_SECTIONS == 1
    assert MAX_SECTIONS == 12
    assert MAX_HEADING_LENGTH == 80
    assert MAX_TONE_LENGTH == 1000
    assert MIN_DESCRIPTION_LENGTH == 1
    assert MAX_DESCRIPTION_LENGTH == 4000
    assert MIN_SECTION_ITEMS == 1
    assert MAX_SECTION_ITEMS == 10


def test_builtin_sections_are_five_mvp_headings_in_order() -> None:
    assert BUILTIN_SECTIONS == (
        "TL;DR",
        "Ключевые идеи",
        "Таймкоды",
        "Цитаты",
        "Что проверить",
    )


def test_builtin_template_has_builtin_sections_and_no_tone() -> None:
    assert BUILTIN_TEMPLATE.sections == BUILTIN_SECTIONS
    assert BUILTIN_TEMPLATE.tone == ""


def test_builtin_active_template_is_marked_builtin() -> None:
    assert BUILTIN_ACTIVE_TEMPLATE.template is BUILTIN_TEMPLATE
    assert BUILTIN_ACTIVE_TEMPLATE.origin is TemplateOrigin.BUILTIN
    assert BUILTIN_ACTIVE_TEMPLATE.is_builtin is True


def test_note_module_keeps_legacy_section_names() -> None:
    assert SECTION_TLDR == "## TL;DR"
    assert SECTION_KEY_IDEAS == "## Ключевые идеи"
    assert SECTION_TIMELINE == "## Таймкоды"
    assert SECTION_QUOTES == "## Цитаты"
    assert SECTION_FOLLOW_UPS == "## Что проверить"


def test_note_section_headings_are_derived_from_builtin_sections() -> None:
    assert tuple(f"## {heading}" for heading in BUILTIN_SECTIONS) == SECTION_HEADINGS


def test_note_render_still_contains_all_builtin_sections_in_order() -> None:
    markdown = render_note(Note(title="Тест"))
    positions = [markdown.index(heading) for heading in SECTION_HEADINGS]
    assert positions == sorted(positions)
    assert markdown.startswith("---\n")


# --- Нормализация NoteTemplate ----------------------------------------------


def test_sections_order_is_preserved() -> None:
    template = NoteTemplate(sections=("Коротко", "Примеры кода", "Что попробовать"))
    assert template.sections == ("Коротко", "Примеры кода", "Что попробовать")


def test_sections_accept_list_and_store_tuple() -> None:
    template = NoteTemplate(sections=["А", "Б"])
    assert isinstance(template.sections, tuple)
    assert template.sections == ("А", "Б")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("## TL;DR", "TL;DR"),
        ("### Примеры кода", "Примеры кода"),
        ("  #  Ключевые   идеи  ", "Ключевые идеи"),
        ("Примеры\nкода", "Примеры кода"),
        ("# # Двойной", "Двойной"),
        ("\t Код \t", "Код"),
    ],
)
def test_heading_is_normalized(raw: str, expected: str) -> None:
    template = NoteTemplate(sections=(raw,))
    assert template.sections == (expected,)
    assert not template.sections[0].startswith("#")


def test_tone_default_is_empty() -> None:
    assert NoteTemplate(sections=("Раздел",)).tone == ""


def test_tone_is_normalized() -> None:
    assert NoteTemplate(sections=("Раздел",), tone="  короче,\n с примерами  ").tone == (
        "короче, с примерами"
    )


def test_tone_none_means_empty_tone() -> None:
    assert NoteTemplate(sections=("Раздел",), tone=None).tone == ""


def test_note_template_is_frozen() -> None:
    template = NoteTemplate(sections=("Раздел",))
    with pytest.raises(FrozenInstanceError):
        template.tone = "короче"  # type: ignore[misc]


def test_note_template_compares_by_value() -> None:
    first = NoteTemplate(sections=("Раздел",), tone="короче")
    second = NoteTemplate(sections=("# Раздел",), tone=" короче ")
    assert first == second
    assert len({first, second}) == 1


# --- Лимиты разделов и заголовков -------------------------------------------


@pytest.mark.parametrize("sections", [(), []])
def test_template_requires_at_least_one_section(sections: list[str] | tuple[str, ...]) -> None:
    with pytest.raises(TemplateValidationError):
        NoteTemplate(sections=sections)


def test_template_allows_one_section() -> None:
    assert NoteTemplate(sections=("Единственный раздел",)).sections == ("Единственный раздел",)


def test_template_allows_max_sections() -> None:
    sections = tuple(f"Раздел {index}" for index in range(MAX_SECTIONS))
    assert NoteTemplate(sections=sections).sections == sections


def test_template_rejects_too_many_sections() -> None:
    sections = tuple(f"Раздел {index}" for index in range(MAX_SECTIONS + 1))
    with pytest.raises(TemplateValidationError, match="до 12 разделов"):
        NoteTemplate(sections=sections)


@pytest.mark.parametrize(
    "heading",
    ["", "   ", "#", "###  ", "\n\t"],
)
def test_template_rejects_empty_heading(heading: str) -> None:
    with pytest.raises(TemplateValidationError, match="не может быть пустым"):
        NoteTemplate(sections=(heading,))


def test_heading_length_boundary() -> None:
    longest = "я" * MAX_HEADING_LENGTH
    assert NoteTemplate(sections=(longest,)).sections == (longest,)


def test_markdown_prefix_is_not_counted_in_heading_length() -> None:
    longest = "я" * MAX_HEADING_LENGTH
    assert NoteTemplate(sections=(f"## {longest}",)).sections == (longest,)


def test_too_long_heading_rejected() -> None:
    with pytest.raises(TemplateValidationError, match="80"):
        NoteTemplate(sections=("я" * (MAX_HEADING_LENGTH + 1),))


@pytest.mark.parametrize(
    "sections",
    [
        ("TL;DR", "TL;DR"),
        ("TL;DR", " tl;dr "),
        ("Ключевые идеи", "ключевые ИДЕИ"),
        ("## Цитаты", "цитаты"),
    ],
)
def test_duplicate_headings_rejected(sections: tuple[str, ...]) -> None:
    with pytest.raises(TemplateValidationError, match="повторяться"):
        NoteTemplate(sections=sections)


@pytest.mark.parametrize(
    "heading",
    [
        "---",
        "Раздел --- подраздел",
        "title: заметка --- x",
        "title: x\n---\ntags: []",
        "# ---",
        "Раздел\x00",
        "Раздел\x1b",
    ],
)
def test_unsafe_heading_rejected(heading: str) -> None:
    with pytest.raises(TemplateValidationError, match="---"):
        NoteTemplate(sections=(heading,))


@pytest.mark.parametrize("heading", [42, None, ("Раздел",), ["Раздел"]])
def test_non_text_heading_rejected(heading: object) -> None:
    with pytest.raises(TemplateValidationError, match="должен быть текстом"):
        NoteTemplate(sections=(heading,))


@pytest.mark.parametrize("sections", ["TL;DR", b"TL;DR", 42, None, {"TL;DR"}])
def test_sections_must_be_sequence_not_string(sections: object) -> None:
    with pytest.raises(TemplateValidationError, match="списком заголовков"):
        NoteTemplate(sections=sections)


# --- Лимиты тона ------------------------------------------------------------


def test_tone_length_boundary() -> None:
    tone = "я" * MAX_TONE_LENGTH
    assert NoteTemplate(sections=("Раздел",), tone=tone).tone == tone


def test_too_long_tone_rejected() -> None:
    with pytest.raises(TemplateValidationError, match="1000"):
        NoteTemplate(sections=("Раздел",), tone="я" * (MAX_TONE_LENGTH + 1))


@pytest.mark.parametrize("tone", [42, object()])
def test_non_text_tone_rejected(tone: object) -> None:
    with pytest.raises(TemplateValidationError, match="должен быть текстом"):
        NoteTemplate(sections=("Раздел",), tone=tone)


def test_blank_tone_is_allowed_and_means_no_tone() -> None:
    assert NoteTemplate(sections=("Раздел",), tone="   \n ").tone == ""


# --- Ошибки валидации -------------------------------------------------------


def test_validation_error_is_user_facing_and_russian() -> None:
    with pytest.raises(TemplateValidationError) as excinfo:
        NoteTemplate(sections=("я" * (MAX_HEADING_LENGTH + 1),))
    error = excinfo.value
    assert isinstance(error, UserFacingError)
    assert CYRILLIC_RE.search(str(error)), "сообщение должно быть на русском"
    assert "я" * 20 not in str(error), "текст шаблона в сообщение не попадает"


def test_parse_error_is_user_facing_and_russian() -> None:
    with pytest.raises(TemplateParseError) as excinfo:
        merge_template(NoteTemplate(sections=("Раздел",)), TemplatePatch())
    error = excinfo.value
    assert isinstance(error, UserFacingError)
    assert CYRILLIC_RE.search(str(error))


# --- ActiveTemplate ---------------------------------------------------------


def test_active_template_with_user_origin() -> None:
    template = NoteTemplate(sections=("Коротко",), tone="короче")
    active = ActiveTemplate(template, TemplateOrigin.USER)
    assert active.is_builtin is False
    assert active.sections == ("Коротко",)
    assert active.tone == "короче"


def test_active_template_coerces_origin_from_string() -> None:
    active = ActiveTemplate(NoteTemplate(sections=("Раздел",)), "user")  # type: ignore[arg-type]
    assert active.origin is TemplateOrigin.USER


def test_active_template_requires_note_template() -> None:
    with pytest.raises(TypeError):
        ActiveTemplate(("Раздел",), TemplateOrigin.USER)  # type: ignore[arg-type]


def test_active_template_is_frozen() -> None:
    active = ActiveTemplate(NoteTemplate(sections=("Раздел",)), TemplateOrigin.USER)
    with pytest.raises(FrozenInstanceError):
        active.origin = TemplateOrigin.BUILTIN  # type: ignore[misc]


# --- Протокол хранилища -----------------------------------------------------


def test_fake_repository_satisfies_template_repository() -> None:
    assert isinstance(FakeTemplateRepository(), TemplateRepository)
    assert not isinstance(object(), TemplateRepository)


def test_template_repository_declares_storage_methods() -> None:
    for name in ("load", "save", "delete"):
        assert hasattr(TemplateRepository, name)


# --- TemplateService: активный шаблон, сохранение, сброс --------------------


@pytest.fixture
def repository() -> FakeTemplateRepository:
    return FakeTemplateRepository()


@pytest.fixture
def service(repository: FakeTemplateRepository) -> TemplateService:
    return TemplateService(repository)


def test_get_active_without_user_template_returns_builtin(
    service: TemplateService, repository: FakeTemplateRepository
) -> None:
    active = service.get_active(101)
    assert active.is_builtin is True
    assert active.template == BUILTIN_TEMPLATE
    assert repository.loads == [101]
    assert repository.saves == []


def test_save_stores_primitives_not_domain_model(
    service: TemplateService, repository: FakeTemplateRepository
) -> None:
    service.save(7, NoteTemplate(sections=("Коротко",), tone="короче"))
    assert repository.saves == [(7, (("Коротко",), "короче"))]
    record = repository.saves[0][1]
    assert type(record) is tuple  # noqa: E721 - проверяем именно примитивы
    assert not isinstance(record, NoteTemplate)


def test_get_active_returns_saved_user_template(
    service: TemplateService, repository: FakeTemplateRepository
) -> None:
    template = NoteTemplate(sections=("Коротко", "Примеры кода"), tone="короче")
    service.save(7, template)
    active = service.get_active(7)
    assert active.is_builtin is False
    assert active.origin is TemplateOrigin.USER
    assert active.template == template
    assert active.sections == ("Коротко", "Примеры кода")


def test_reset_removes_user_template_and_returns_builtin(
    service: TemplateService, repository: FakeTemplateRepository
) -> None:
    service.save(7, NoteTemplate(sections=("Коротко",)))
    service.reset(7)
    assert repository.deletes == [7]
    assert service.get_active(7).is_builtin is True


def test_reset_is_idempotent(
    service: TemplateService, repository: FakeTemplateRepository
) -> None:
    service.reset(7)
    service.reset(7)
    assert repository.deletes == [7, 7]
    assert service.get_active(7).is_builtin is True


def test_templates_of_different_users_are_isolated(
    service: TemplateService, repository: FakeTemplateRepository
) -> None:
    service.save(7, NoteTemplate(sections=("Коротко",), tone="короче"))
    assert service.get_active(7).is_builtin is False
    assert service.get_active(8).is_builtin is True
    assert 8 not in repository.records


@pytest.mark.parametrize(
    "record",
    [
        ((), ""),
        (tuple(f"Раздел {index}" for index in range(MAX_SECTIONS + 1)), ""),
        ("TL;DR", ""),
        (("TL;DR", "tl;dr"), ""),
        (("TL;DR",), 42),
    ],
)
def test_invalid_stored_record_degrades_to_builtin(
    service: TemplateService,
    repository: FakeTemplateRepository,
    record: tuple[object, object],
) -> None:
    repository.records[5] = record  # type: ignore[assignment]
    assert service.get_active(5).is_builtin is True


def test_invalid_stored_record_logs_warning_without_template_text(
    service: TemplateService,
    repository: FakeTemplateRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository.records[5] = (("",), "секретный тон пользователя")
    with caplog.at_level(logging.WARNING, logger=TEMPLATE_LOGGER):
        service.get_active(5)
    records = [item for item in caplog.records if item.name == TEMPLATE_LOGGER]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert "встроенный" in message
    assert "секретный тон" not in message


def test_valid_stored_template_does_not_log_warning(
    service: TemplateService,
    repository: FakeTemplateRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service.save(5, NoteTemplate(sections=("Коротко",)))
    with caplog.at_level(logging.WARNING, logger=TEMPLATE_LOGGER):
        service.get_active(5)
    assert [item for item in caplog.records if item.name == TEMPLATE_LOGGER] == []


# --- Слияние правки с текущим шаблоном --------------------------------------


def test_merge_keeps_tone_when_only_sections_change() -> None:
    current = NoteTemplate(sections=("TL;DR",), tone="короче, с примерами")
    merged = merge_template(current, TemplatePatch(sections=("Коротко", "Примеры")))
    assert merged.sections == ("Коротко", "Примеры")
    assert merged.tone == "короче, с примерами"


def test_merge_keeps_sections_when_only_tone_changes() -> None:
    current = NoteTemplate(sections=("TL;DR", "Цитаты"), tone="короче")
    merged = merge_template(current, TemplatePatch(tone="подробнее, на английском"))
    assert merged.sections == ("TL;DR", "Цитаты")
    assert merged.tone == "подробнее, на английском"


def test_merge_replaces_both_parts() -> None:
    current = NoteTemplate(sections=("TL;DR",), tone="короче")
    merged = merge_template(
        current, TemplatePatch(sections=("# Коротко",), tone=" с примерами ")
    )
    assert merged.sections == ("Коротко",)
    assert merged.tone == "с примерами"


def test_merge_normalizes_new_headings() -> None:
    merged = merge_template(
        NoteTemplate(sections=("TL;DR",)),
        TemplatePatch(sections=("  ## Примеры   кода ",)),
    )
    assert merged.sections == ("Примеры кода",)


def test_merge_preserves_patch_order() -> None:
    merged = merge_template(
        NoteTemplate(sections=("TL;DR",)),
        TemplatePatch(sections=("Второй", "Первый", "Третий")),
    )
    assert merged.sections == ("Второй", "Первый", "Третий")


@pytest.mark.parametrize("tone", ["", "   ", "\n\t"])
def test_empty_tone_clears_tone(tone: str) -> None:
    current = NoteTemplate(sections=("TL;DR",), tone="короче")
    merged = merge_template(current, TemplatePatch(tone=tone))
    assert merged.tone == ""
    assert merged.sections == current.sections


def test_patch_without_any_change_is_parse_error() -> None:
    current = NoteTemplate(sections=("TL;DR",), tone="короче")
    with pytest.raises(TemplateParseError, match="не нашлось изменений"):
        merge_template(current, TemplatePatch())


def test_patch_equal_to_current_is_parse_error() -> None:
    current = NoteTemplate(sections=("TL;DR", "Цитаты"), tone="короче")
    with pytest.raises(TemplateParseError, match="совпадает с текущим"):
        merge_template(current, TemplatePatch(sections=(" ## TL;DR ", "Цитаты")))
    with pytest.raises(TemplateParseError, match="совпадает с текущим"):
        merge_template(current, TemplatePatch(tone=" короче "))


def test_patch_empty_property() -> None:
    assert TemplatePatch().is_empty is True
    assert TemplatePatch(sections=("Раздел",)).is_empty is False
    assert TemplatePatch(tone="").is_empty is False


def test_merge_validates_result_limits() -> None:
    sections = tuple(f"Раздел {index}" for index in range(MAX_SECTIONS + 1))
    with pytest.raises(TemplateValidationError):
        merge_template(NoteTemplate(sections=("TL;DR",)), TemplatePatch(sections=sections))


def test_merge_does_not_change_current_template() -> None:
    current = NoteTemplate(sections=("TL;DR", "Цитаты"), tone="короче")
    merge_template(current, TemplatePatch(sections=("Другое",), tone=""))
    assert current.sections == ("TL;DR", "Цитаты")
    assert current.tone == "короче"


# --- TemplateService.merge_patch: слияние с активным шаблоном ----------------


def test_merge_patch_uses_builtin_sections_when_no_user_template(
    service: TemplateService, repository: FakeTemplateRepository
) -> None:
    candidate = service.merge_patch(3, TemplatePatch(tone="короче, с примерами кода"))
    assert candidate.sections == BUILTIN_SECTIONS
    assert candidate.tone == "короче, с примерами кода"
    assert repository.saves == [], "кандидат не сохраняется автоматически"


def test_merge_patch_keeps_user_template_tone(
    service: TemplateService, repository: FakeTemplateRepository
) -> None:
    service.save(3, NoteTemplate(sections=("TL;DR",), tone="на английском"))
    candidate = service.merge_patch(3, TemplatePatch(sections=("Коротко",)))
    assert candidate.sections == ("Коротко",)
    assert candidate.tone == "на английском"
    assert repository.saves == [(3, (("TL;DR",), "на английском"))]

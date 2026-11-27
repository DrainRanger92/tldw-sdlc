"""Тесты файлового хранилища шаблонов по Telegram user ID (T-002).

Все проверки офлайновые и работают в ``tmp_path``: сеть, Telegram и LLM не
используются. Проверяются схема JSON v1, переживание перезапуска, атомарность
записи, устойчивое чтение повреждённых файлов, изоляция пользователей и
идемпотентное удаление.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import threading
from pathlib import Path

import pytest

import tldw.storage.templates as templates_module
from tldw.errors import UserFacingError
from tldw.services import TemplateRepository
from tldw.storage import SCHEMA_VERSION, TemplateStorageError, TemplateStore

USER_ID = 424_242
OTHER_USER_ID = 777_777
RECORD: tuple[tuple[str, ...], str] = (
    ("Коротко", "Примеры кода", "Что попробовать"),
    "короче, с акцентом на код",
)
OTHER_RECORD: tuple[tuple[str, ...], str] = (("Итоги", "Вопросы"), "подробнее")
TEMPLATE_LOGGER = "tldw.storage.templates"


def template_path(data_dir: Path, user_id: int = USER_ID) -> Path:
    """Путь файла шаблона для проверки структуры каталога."""
    return data_dir / "templates" / f"{user_id}.json"


def storage_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Только предупреждения нашего модуля, без чужих записей."""
    return [
        record
        for record in caplog.records
        if record.name == TEMPLATE_LOGGER and record.levelno >= logging.WARNING
    ]


def storage_records(
    caplog: pytest.LogCaptureFixture,
    level: int,
) -> list[logging.LogRecord]:
    """Записи нашего модуля ровно заданного уровня."""
    return [
        record
        for record in caplog.records
        if record.name == TEMPLATE_LOGGER and record.levelno == level
    ]


def write_raw_template(data_dir: Path, raw: str, user_id: int = USER_ID) -> Path:
    """Положить произвольное содержимое в файл шаблона."""
    path = template_path(data_dir, user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return path


# --- Контракт и путь --------------------------------------------------------


def test_store_structurally_implements_template_repository(tmp_path: Path) -> None:
    assert isinstance(TemplateStore(tmp_path), TemplateRepository)


def test_storage_module_does_not_import_services() -> None:
    source = Path(templates_module.__file__).read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    assert imported
    assert not [
        name for name in imported if name == "tldw.services" or name.startswith("tldw.services.")
    ]


def test_templates_dir_lives_inside_data_dir(tmp_path: Path) -> None:
    store = TemplateStore(tmp_path / "data")

    assert store.templates_dir == tmp_path / "data" / "templates"

    store.save(USER_ID, RECORD)
    assert template_path(tmp_path / "data").exists()


@pytest.mark.parametrize(
    "user_id",
    [0, -1, True, False, "1", 1.5, None, "../evil", b"1"],
)
def test_invalid_user_id_is_rejected(tmp_path: Path, user_id: object) -> None:
    store = TemplateStore(tmp_path)

    with pytest.raises(ValueError):
        store.load(user_id)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        store.save(user_id, RECORD)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        store.delete(user_id)  # type: ignore[arg-type]

    assert not store.templates_dir.exists()


# --- Roundtrip, схема и перезапуск ------------------------------------------


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    store = TemplateStore(tmp_path)

    store.save(USER_ID, RECORD)

    assert store.load(USER_ID) == RECORD
    path = template_path(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "version": SCHEMA_VERSION,
        "sections": list(RECORD[0]),
        "tone": RECORD[1],
    }
    assert list(payload) == ["version", "sections", "tone"]
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_saved_template_is_read_by_new_store_instance(tmp_path: Path) -> None:
    TemplateStore(tmp_path).save(USER_ID, RECORD)

    restarted = TemplateStore(tmp_path)

    assert restarted.load(USER_ID) == RECORD


def test_save_replaces_previous_template(tmp_path: Path) -> None:
    store = TemplateStore(tmp_path)
    store.save(USER_ID, RECORD)

    store.save(USER_ID, OTHER_RECORD)

    assert store.load(USER_ID) == OTHER_RECORD


def test_save_accepts_list_record_without_storing_it_by_reference(tmp_path: Path) -> None:
    store = TemplateStore(tmp_path)
    sections = ["Первый", "Второй"]

    store.save(USER_ID, (sections, "тон"))  # type: ignore[arg-type]
    sections.append("Третий")

    assert store.load(USER_ID) == (("Первый", "Второй"), "тон")


@pytest.mark.parametrize(
    "record",
    [
        (("TL;DR",),),
        (("TL;DR",), "", "лишнее"),
        "TL;DR",
        ("TL;DR", ""),
        (("TL;DR", 1), ""),
        (("TL;DR",), 5),
        (("TL;DR",), None),
    ],
)
def test_save_rejects_invalid_record(tmp_path: Path, record: object) -> None:
    store = TemplateStore(tmp_path)

    with pytest.raises(ValueError):
        store.save(USER_ID, record)  # type: ignore[arg-type]

    assert not store.templates_dir.exists()


# --- Устойчивое чтение ------------------------------------------------------


def test_load_missing_template_logs_debug_and_returns_none(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Отсутствие своего шаблона — штатное состояние: DEBUG, а не WARNING."""
    store = TemplateStore(tmp_path)

    with caplog.at_level(logging.DEBUG, logger=TEMPLATE_LOGGER):
        assert store.load(USER_ID) is None

    assert storage_warnings(caplog) == [], "нет шаблона — не повод для WARNING"
    records = storage_records(caplog, logging.DEBUG)
    assert len(records) == 1
    assert "не сохранён" in records[0].getMessage()
    assert not store.templates_dir.exists()


def test_missing_file_and_broken_json_use_different_log_levels(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Два пути чтения различаются: нет файла — DEBUG, битый файл — WARNING."""
    store = TemplateStore(tmp_path)

    with caplog.at_level(logging.DEBUG, logger=TEMPLATE_LOGGER):
        assert store.load(USER_ID) is None
        write_raw_template(tmp_path, '{"version": 1, "sections": ["TL;DR"]')
        assert store.load(USER_ID) is None

    levels = [
        record.levelno for record in caplog.records if record.name == TEMPLATE_LOGGER
    ]
    assert levels == [logging.DEBUG, logging.WARNING]


def test_load_broken_json_warns_returns_none_and_keeps_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = '{"version": 1, "sections": ["TL;DR"]'
    path = write_raw_template(tmp_path, raw)

    with caplog.at_level(logging.WARNING, logger=TEMPLATE_LOGGER):
        assert TemplateStore(tmp_path).load(USER_ID) is None

    assert len(storage_warnings(caplog)) == 1
    assert path.read_text(encoding="utf-8") == raw


def test_load_unknown_version_warns_returns_none_and_keeps_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = {"version": SCHEMA_VERSION + 1, "sections": ["TL;DR"], "tone": ""}
    raw = json.dumps(payload, ensure_ascii=False)
    path = write_raw_template(tmp_path, raw)

    with caplog.at_level(logging.WARNING, logger=TEMPLATE_LOGGER):
        assert TemplateStore(tmp_path).load(USER_ID) is None

    warnings = storage_warnings(caplog)
    assert len(warnings) == 1
    assert "версия" in warnings[0].getMessage()
    assert path.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "строка",
        42,
        {"sections": ["TL;DR"], "tone": ""},
        {"version": "1", "sections": ["TL;DR"], "tone": ""},
        {"version": True, "sections": ["TL;DR"], "tone": ""},
        {"version": 1, "tone": ""},
        {"version": 1, "sections": "TL;DR", "tone": ""},
        {"version": 1, "sections": ["TL;DR", 3], "tone": ""},
        {"version": 1, "sections": ["TL;DR"]},
        {"version": 1, "sections": ["TL;DR"], "tone": None},
        {"version": 1, "sections": ["TL;DR"], "tone": 5},
    ],
)
def test_load_invalid_schema_warns_returns_none_and_keeps_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    payload: object,
) -> None:
    raw = json.dumps(payload, ensure_ascii=False)
    path = write_raw_template(tmp_path, raw)

    with caplog.at_level(logging.WARNING, logger=TEMPLATE_LOGGER):
        assert TemplateStore(tmp_path).load(USER_ID) is None

    assert len(storage_warnings(caplog)) == 1
    assert path.read_text(encoding="utf-8") == raw


def test_load_non_utf8_file_returns_none(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = template_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"version": 1, \xff}')

    with caplog.at_level(logging.WARNING, logger=TEMPLATE_LOGGER):
        assert TemplateStore(tmp_path).load(USER_ID) is None

    assert len(storage_warnings(caplog)) == 1
    assert path.exists()


def test_load_directory_in_place_of_file_returns_none(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    template_path(tmp_path).mkdir(parents=True)

    with caplog.at_level(logging.WARNING, logger=TEMPLATE_LOGGER):
        assert TemplateStore(tmp_path).load(USER_ID) is None

    assert len(storage_warnings(caplog)) == 1


def test_invalid_file_warning_does_not_include_template_text(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_text = "СЕКРЕТНЫЙ-РАЗДЕЛ-123"
    payload = {"version": 1, "sections": [secret_text], "tone": 5}
    write_raw_template(tmp_path, json.dumps(payload, ensure_ascii=False))

    with caplog.at_level(logging.WARNING, logger=TEMPLATE_LOGGER):
        assert TemplateStore(tmp_path).load(USER_ID) is None

    assert secret_text not in caplog.text


# --- Атомарность ------------------------------------------------------------


def test_save_leaves_only_json_file_without_tmp(tmp_path: Path) -> None:
    store = TemplateStore(tmp_path)

    store.save(USER_ID, RECORD)
    store.save(USER_ID, OTHER_RECORD)

    assert [path.name for path in store.templates_dir.iterdir()] == [f"{USER_ID}.json"]


def test_save_uses_temp_file_next_to_target_and_os_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TemplateStore(tmp_path)
    target = template_path(tmp_path)
    seen: dict[str, Path] = {}
    real_replace = os.replace

    def spy_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        seen["source"] = source_path
        seen["target"] = Path(destination)
        assert source_path.parent == target.parent
        assert source_path.name.endswith(".tmp")
        assert source_path.exists()
        real_replace(source_path, destination)

    monkeypatch.setattr(os, "replace", spy_replace)

    store.save(USER_ID, RECORD)

    assert seen["target"] == target
    assert seen["source"].name.startswith(f".{USER_ID}-")
    assert store.load(USER_ID) == RECORD


def test_write_error_keeps_previous_template_and_raises_storage_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TemplateStore(tmp_path)
    store.save(USER_ID, RECORD)
    path = template_path(tmp_path)
    previous = path.read_text(encoding="utf-8")

    def broken_replace(source: str | Path, destination: str | Path) -> None:
        raise OSError("диск переполнен")

    monkeypatch.setattr(os, "replace", broken_replace)

    with pytest.raises(TemplateStorageError) as excinfo:
        store.save(USER_ID, OTHER_RECORD)

    assert isinstance(excinfo.value, UserFacingError)
    assert "шаблон" in str(excinfo.value).lower()
    assert path.read_text(encoding="utf-8") == previous
    assert store.load(USER_ID) == RECORD
    assert [item.name for item in store.templates_dir.iterdir()] == [f"{USER_ID}.json"]


def test_failed_write_cleans_temp_file_and_leaves_no_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TemplateStore(tmp_path)

    def broken_fsync(descriptor: int) -> None:
        raise OSError("нет места на диске")

    monkeypatch.setattr(os, "fsync", broken_fsync)

    with pytest.raises(TemplateStorageError):
        store.save(USER_ID, RECORD)

    assert list(store.templates_dir.iterdir()) == []


def test_parallel_saves_leave_valid_json_and_last_replaced_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TemplateStore(tmp_path)
    records = [((f"Раздел {index}",), f"тон {index}") for index in range(6)]
    barrier = threading.Barrier(len(records))
    replaced_texts: list[str] = []
    order_lock = threading.Lock()
    errors: list[BaseException] = []
    real_replace = os.replace

    def tracking_replace(source: str | Path, destination: str | Path) -> None:
        text = Path(source).read_text(encoding="utf-8")
        with order_lock:
            real_replace(source, destination)
            replaced_texts.append(text)

    def worker(record: tuple[tuple[str, ...], str]) -> None:
        try:
            barrier.wait(timeout=10)
            store.save(USER_ID, record)
        except Exception as exc:  # ошибку проверит основной поток
            errors.append(exc)

    monkeypatch.setattr(os, "replace", tracking_replace)
    threads = [threading.Thread(target=worker, args=(record,)) for record in records]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert errors == []
    assert len(replaced_texts) == len(records)
    path = template_path(tmp_path)
    final_text = path.read_text(encoding="utf-8")
    assert final_text == replaced_texts[-1]
    payload = json.loads(final_text)
    assert payload["version"] == SCHEMA_VERSION
    assert store.load(USER_ID) == (tuple(payload["sections"]), payload["tone"])
    assert [item.name for item in store.templates_dir.iterdir()] == [f"{USER_ID}.json"]


# --- Изоляция и удаление ----------------------------------------------------


def test_templates_of_different_users_are_isolated(tmp_path: Path) -> None:
    store = TemplateStore(tmp_path)
    store.save(USER_ID, RECORD)
    store.save(OTHER_USER_ID, OTHER_RECORD)

    assert store.load(USER_ID) == RECORD
    assert store.load(OTHER_USER_ID) == OTHER_RECORD

    store.delete(USER_ID)

    assert store.load(USER_ID) is None
    assert store.load(OTHER_USER_ID) == OTHER_RECORD
    assert not template_path(tmp_path, USER_ID).exists()
    assert template_path(tmp_path, OTHER_USER_ID).exists()


def test_delete_is_idempotent(tmp_path: Path) -> None:
    store = TemplateStore(tmp_path)
    store.save(USER_ID, RECORD)
    path = template_path(tmp_path)

    store.delete(USER_ID)
    store.delete(USER_ID)
    store.delete(USER_ID)

    assert not path.exists()
    assert store.load(USER_ID) is None


def test_delete_removes_corrupt_file(tmp_path: Path) -> None:
    path = write_raw_template(tmp_path, "{битый json")
    store = TemplateStore(tmp_path)

    store.delete(USER_ID)

    assert not path.exists()


def test_delete_error_raises_storage_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TemplateStore(tmp_path)
    store.save(USER_ID, RECORD)

    def broken_unlink(self: Path, missing_ok: bool = False) -> None:
        raise OSError("нет прав")

    monkeypatch.setattr(Path, "unlink", broken_unlink)

    with pytest.raises(TemplateStorageError) as excinfo:
        store.delete(USER_ID)

    assert isinstance(excinfo.value, UserFacingError)
    assert "удалить" in str(excinfo.value).lower()

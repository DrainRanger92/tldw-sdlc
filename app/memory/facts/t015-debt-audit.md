# Аудит долга T-015: поведение шаблона, заметки и голоса

Дата: 2026-11-27. Задача: T-015 «Поведение шаблона, заметки и голоса:
приватность лога, формат разделов и симметрия ввода». Источники:
`t001-t010-review-issues.md` (8 пунктов), `t012-review-issues.md` (7 P3),
`t005-review-issues.md` (8 пунктов, из них 3 P1 закрыты в T-013), плюс два
пункта из описания задачи, которые надо было закрыть как устаревшие.
Каждый пункт сверен с текущим кодом, а не с номерами строк из старых
отчётов; прочитаны `adr-015-speech-adapter.md`, `adr-016-voice-input-flow.md`.

Статусы: **актуален** — воспроизводился в коде до правки и исправлен здесь;
**устарел** — в коде уже не воспроизводится, закрыт с доказательством;
**процессный** — не код; **передан** — актуален, но по критериям относится к
другой задаче спеки 003 (T-016/T-017/T-018).

## Таблица «пункт → статус → доказательство»

| № | Пункт | Статус | Доказательство |
|---:|---|---|---|
| T-001…010.1 | P2: `_check_section_heading` пишет в `WARNING` заголовок шаблона и ответ LLM | **актуален → исправлен** | `adapters/llm.py::_check_section_heading` теперь логирует только `sections[index].heading не совпал с шаблоном`; `_fail` остаётся единственной записью `WARNING`. Тест `test_llm_template.py::test_heading_mismatch_logs_only_index_without_template_or_llm_text` — заголовки-ловушки `СЕКРЕТ-ИЗ-ШАБЛОНА-777`/`СЕКРЕТ-ИЗ-ОТВЕТА-LLM-888` отсутствуют в `caplog`, а индекс `sections[0].heading` есть |
| T-001…010.2 | P2: отсутствие файла шаблона (штатное состояние) шумит `WARNING` на каждой ссылке | **актуален → исправлен** | `storage/templates.py::TemplateStore.load`: `FileNotFoundError` → `log.debug`, остальные ветки (не-UTF8, `OSError`, битый JSON/схема/версия) остались `WARNING`. Тесты `test_storage_templates.py::test_load_missing_template_logs_debug_and_returns_none` и `test_missing_file_and_broken_json_use_different_log_levels` (уровни `[DEBUG, WARNING]`), прежние `test_load_broken_json_warns...`/`test_load_unknown_version_warns...` зелёные |
| T-001…010.3 | P2: неизвестный сбой в `/template` показывает `MESSAGE_UNEXPECTED` про видео | **актуален → исправлен** | `errors.py`: `MESSAGE_UNEXPECTED_TEMPLATE` + параметр `unknown_message` у `describe_error`/`log_failure` (по умолчанию прежний текст про видео). Все пять `log_failure` в `handlers/template.py` (разбор, голос, скачивание, сохранение, сброс) передают текст о шаблоне. Тесты: `test_template_errors.py::test_unknown_failure_gives_generic_message_and_cleaned_error_log` (текст), `test_voice_handler.py::test_unknown_voice_failure_gives_template_message` (голос), `test_errors.py` — переопределение не подменяет известные ошибки |
| T-001…010.4 | P2: динамический путь теряет формат «Таймкодов» (`- 01:23`) и «Цитат» (пустое `> —`) | **актуален → исправлен** | `services/note.py`: `_timeline_item` разбирает ведущий `MM:SS`/`HH:MM:SS` (в т.ч. `[01:23]`, разделитель «—»/«–»/«-»/пробел) через `_split_timecode`, `_render_quotes` больше не добавляет `> —` без таймкода. Сквозной тест `test_summary_templates.py::test_dynamic_builtin_timeline_and_quotes_keep_native_format`: ответ настоящего `LLMClient` → `SummaryService.process` → `render_note`; `- [01:23] Начало`, `- [02:05] Практика`, `> …` без `> —`. Юнит-кейсы — `test_note_dynamic.py` |
| T-001…010.5 | P3: дубли валидаторов/блоков и второй источник лимитов разделов | **передан в T-017** | Это критерии T-017 («метки „встроенный/пользовательский“ и валидаторы `_template_current_*`/`_requested_*` сведены к одному источнику», «`MIN/MAX_SECTION_ITEMS` либо применяются, либо удалены»). В T-015 не трогалось: правка `adapters/llm.py` и домена шаблона расширила бы поверхность |
| T-001…010.6 | P3: `MAX_PATCH_OUTPUT_TOKENS=1024` может обрезать максимальный патч | **передан в T-018** | Прямо в критериях T-018: «`MAX_PATCH_OUTPUT_TOKENS` достаточен для максимального патча… или ограничение документировано и покрыто тестом». Константа не менялась |
| T-001…010.7 | P3: синхронный файловый I/O в корутинах обработчиков | **передан в T-016** | Критерий T-016: «синхронный файловый I/O в корутинах уходит в `asyncio.to_thread`». В T-015 не менялся порядок вызовов `TemplateService.get_active` |
| T-001…010.8 | P3: границы диффа (`specs/` в изменении, untracked) | **процессный → закрыт** | Не код: правки `specs/` делает оркестратор (`review-spec-edit-artifact.md`), разработчик их не откатывает. Новые файлы этой задачи (`facts/t015-debt-audit.md`) попадают в дифф через `git add -N` — см. «Гейты» |
| T-012.1 | P3: неполнота тестов — нет «ненастроенный адаптер + 61 с» | **актуален → исправлен (частично)** | Новый тест `test_voice_handler.py::test_unconfigured_adapter_with_long_voice_logs_no_warning`: при `configured=False` и `duration=61` пользователь видит `MESSAGE_VOICE_UNAVAILABLE`, адаптер не вызывается. Вторая половина пункта (сброс показанного кандидата при сбое распознавания) остаётся в T-018 — там она в критериях |
| T-012.2 | P3: два текста одного лимита (`MESSAGE_VOICE_TOO_LONG` и `MESSAGE_SPEECH_TOO_LONG`) | **актуален → исправлен** | Остался один ресурс `errors.py::MESSAGE_AUDIO_TOO_LONG`; обработчик форматирует его с `tldw.config.MAX_AUDIO_DURATION_SECONDS`, `SpeechTooLongError` — тоже (через `_too_long_message`). Старые имена удалены. Тесты `test_voice_handler.py::test_voice_limit_text_and_handler_have_one_source`, `test_speech_adapter.py::test_too_long_message_has_single_source`, `test_errors.py::test_audio_limit_message_is_shared_and_formatted` |
| T-012.3 | P3: два пути импорта `MAX_AUDIO_DURATION_SECONDS` | **актуален → исправлен** | Константа убрана из `__all__` `adapters/speech.py` и из реэкспорта `adapters/__init__.py`; единственный публичный путь — `tldw.config`. Тест `test_speech_adapter.py::test_limit_has_single_public_import_path` проверяет отсутствие имени в обоих `__all__` и идентичность объекта |
| T-012.4 | P3: дубль списка состояний (`_DESCRIPTION_STATES` и `StateFilter(...)`) | **актуален → исправлен** | `handlers/template.py::register` использует `StateFilter(*_DESCRIPTION_STATES)`; повторного перечисления состояний нет. FSM-сценарии `test_template_handler.py`/`test_voice_handler.py` проходят |
| T-012.5 | P3: асимметрия голоса и текста — ссылка в надиктованном описании не отбивается | **актуален → исправлен** | Проверка `extract_first_url` перенесена из `handle_description` в общий `_present_description`, поэтому оба ввода дают одинаковую подсказку до вызова LLM. Тесты `test_voice_handler.py::test_voice_link_gets_same_hint_as_text_and_skips_llm` (`parser.calls == []`) и `..._while_showing_candidate_keeps_candidate_hint`; прежний текстовый кейс `test_template_handler.py::test_link_while_waiting_for_description_does_not_start_video` зелёный |
| T-012.6 | P3: порядок «состояние → `is_configured`» | **устарел (UX-решение)** | Ревью T-012 сняло пункт как вопрос UX, не дефект. Поведение осознанно: `handle_voice` проверяет `current_state not in _DESCRIPTION_STATES` до `is_configured` (`handlers/template.py`), и это закреплено ADR-016 п. 2 («Состояние — до конфигурации: вне сценария пользователь получает подсказку, как войти, независимо от настроек»). Тест `test_voice_handler.py::test_voice_outside_description_state_gets_a_hint` фиксирует подсказку. Кода не менял |
| T-012.7 | P3: дубль `get_file_requests`/`AUDIO`/`VOICE_TEXT` в двух тестах | **передан в T-018** | Критерий T-018: «`get_file_requests`, `AUDIO`/`VOICE_TEXT` … живут в `tests/support.py`/`conftest.py`, дублей нет». Перенос каркаса — отдельная поверхность, в T-015 не делался |
| T-005.1–3 | P1: пустой/вырожденный транскрипт, `budget == 0` | **устарел (закрыт в T-013)** | `t013-debt-audit.md`: `EmptyTranscriptError` до LLM, `join_transcript` при лимите меньше маркера и `budget == 0` возвращает пустой `TranscriptText`. Код T-015 не менял |
| T-005.4 | P2: дубль фильтрации сегментов (`join_transcript` и `_normalize_segments`) | **передан в T-017** | Прямо в критериях T-017: «`join_transcript` не повторяет фильтрацию `_normalize_segments`». В T-015 не трогалось |
| T-005.5 | P2: `int(segment.start_seconds)` — усечение вместо округления | **актуален → исправлен** | `services/summary.py::join_transcript` использует `round(...)` — то же правило, что `int(round(...))` в `adapters/youtube.py::_coerce_duration`. Тест `test_summary_service.py::test_transcript_timecodes_are_rounded_like_adapter_duration`: 15.9 → `[00:16]`, 61.6 → `[01:02]` |
| T-005.6 | P2: `_string_items` превращает `dict`/`list` в Python-repr | **актуален → исправлен** | `_string_items` принимает строку как один элемент и требует строки в последовательности, иначе `TypeError`; `str(item)` удалён. Тест `test_summary_service.py::test_build_note_rejects_non_string_items_without_python_repr` |
| T-005.7 | P2: `process` передаёт в `fetch` неочищенный URL, а в `source` — `url.strip()` | **актуален → исправлен** | `SummaryService.process` снимает пробелы один раз (`clean_url`) и передаёт одно значение в `fetch` и `build_note(source=...)`. Тест `test_summary_service.py::test_url_is_stripped_once_for_fetch_and_source`: `source.calls == [URL]`, `note.source == URL` |
| T-005.8 | P2: косметика сообщений — английское `max_duration_seconds`, у `_transcript_limit` нет имени поля | **актуален → исправлен** | `_transcript_limit` удалён: оба лимита валидирует `_positive_int` с русским именем («Порог длительности видео», «Лимит текста транскрипта»); английского `max_duration_seconds` в тексте нет. Тест `test_summary_service.py::test_invalid_limit_messages_use_readable_russian_names` |
| Описание задачи | Двойной `WARNING` при выключенном адаптере | **устарел → закрыт** | Воспроизведения нет: `handle_voice` при `speech is None or not speech.is_configured` отвечает `MESSAGE_VOICE_UNAVAILABLE` и возвращается, `SpeechAdapter._check_duration` (единственный `WARNING` о длительности) не вызывается; `speech.calls == []`. Новый тест `test_voice_handler.py::test_unconfigured_adapter_with_long_voice_logs_no_warning` проверяет отсутствие `WARNING` логгеров `tldw.*`. Кода не менял |

## Что именно изменилось в коде

- `adapters/llm.py`: причина несовпадения заголовка раздела — только индекс.
- `storage/templates.py`: отсутствующий файл шаблона — `DEBUG`; битые данные — `WARNING`.
- `errors.py`: `MESSAGE_UNEXPECTED_TEMPLATE`, `MESSAGE_AUDIO_TOO_LONG`,
  параметр `unknown_message` у `describe_error`/`log_failure`.
- `handlers/template.py`: общий для текста и голоса путь
  `_present_description` с проверкой ссылки; `StateFilter(*_DESCRIPTION_STATES)`;
  один текст лимита; все сбои сообщают о шаблоне.
- `adapters/speech.py` и `adapters/__init__.py`: лимит и текст не
  реэкспортируются; текст берётся из `errors.py`.
- `services/note.py`: разбор ведущего таймкода в пункте «Таймкодов»;
  цитата без таймкода — без пустого `> —`.
- `services/summary.py`: округление секунд, отказ от `str(item)`, один
  `clean_url`, русские имена лимитов в `ValueError`.

## Гейты

- `pytest -q`: **1081 passed** (было 1056; +25 нетто), офлайн, без токенов.
- `ruff check .`: чисто.
- Тексты по умолчанию, формат `.md` и встроенный путь не менялись:
  `test_summary_service.py`, `test_note.py`, `test_e2e.py`,
  `test_template_e2e.py`, `test_voice_e2e.py` зелёные.
- Секретов и текста шаблона в логах нет: тест с заголовками-ловушками,
  прежние тесты `caplog`/`FAKE_SECRETS` и `test_readme.py` зелёные.

## Передано дальше (не потеряно)

- **T-016:** синхронный файловый I/O в корутинах (T-001…010.7).
- **T-017:** дубли валидаторов, метки происхождения шаблона, лимиты разделов,
  дубль фильтрации сегментов.
- **T-018:** `MAX_PATCH_OUTPUT_TOKENS`, сброс кандидата при сбое распознавания,
  общие тестовые помощники (`get_file_requests`/`AUDIO`/`VOICE_TEXT`).

## Итог ревью

`VERDICT: APPROVE` с первой итерации, FIX=0, 7 P3 (все неблокирующие) —
`t015-review-issues.md`. Гейт `pytest` ревьюер не прогнал: read-only
песочница без writable temp-каталога, 0 тестов из 1081; зелёность принята
по арифметике 1056 + 25, LEARN подтвердил **1081 passed** после вердикта.
`ruff check .` у ревьюера чист. Аудит-таблица в диффе сработала второй раз:
P2 «не подтверждено» не появился.

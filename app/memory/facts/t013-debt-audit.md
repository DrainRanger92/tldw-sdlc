# Аудит долга T-013: что закрыто, что устарело, что передано дальше

Дата: 2026-11-27. T-013 требовала таблицу «пункт → актуален/устарел/процессный
→ доказательство». Разработчик составил её в финальном сообщении, но **в дифф
она не попала**, поэтому ревьюер не смог подтвердить критерий из рабочей копии
(`t013-review-issues.md`, `audit-artifact-not-in-diff.md`). По `plan.md` спеки
003 итог аудита переносит в память LEARN — это и есть данный файл. Он не
заменяет код и тесты: доказательства — ссылки на файлы/тесты из диффа T-013.

## Закрыто в T-013

| Пункт | Было | Доказательство |
|---|---|---|
| P1 T-004: `httpx.InvalidURL`/`RuntimeError` мимо `LLMError` | актуален | `config.validate_http_url` + `llm._make_client`/`client.post` в `try`; тесты `test_broken_base_url_*`, `test_request_url_error_*`, `test_missing_prompt_resource_*` |
| P2 T-004: `MAX_FOLLOW_UPS` отсутствовал в `__all__` | актуален (память ошибочно считала «убрано») | константа добавлена в `__all__`; тест `test_follow_up_limits_are_both_in_public_surface` |
| P1 T-005: пустой транскрипт уходил в LLM | актуален | `SummaryService.process` → `EmptyTranscriptError` до LLM; тест `test_empty_transcript_is_rejected_before_llm_and_file` |
| P1 T-005: лимит меньше маркера давал обрезанный маркер | актуален | `join_transcript` → пустой `TranscriptText`; тест `test_join_transcript_handles_limit_smaller_than_marker` |
| P1 T-005: `budget == 0` считал пустую строку сегментом | актуален | тот же путь, `used_segments=0`; тест `test_join_transcript_with_zero_budget_does_not_count_empty_segment` |
| P2 T-011: `InvalidURL` мимо `SpeechError`, `_make_client` вне `try` | актуален | `speech.py` обёртки создания клиента и запроса; тесты `test_broken_base_url_*`, `test_request_url_error_*` |
| P3 T-011: `media_type` не валидировался | актуален | `ALLOWED_AUDIO_MEDIA_TYPES` + `_media_type`; тесты `test_unsafe_media_type_*`, `test_safe_media_type_*` |
| P2 T-004/T-011: третья копия `_one_line` | устарел | единственное определение — `tldw/text.py`; адаптеры импортируют `one_line` |
| P3 T-004: версии/CHANGELOG расходятся | устарел | `tldw.__version__ = 0.17.0` = `pyproject.toml`; дублей записей CHANGELOG нет |

Итого **9 групп закрыто** (7 кодом и тестами, 2 закрыты как устаревшие с
доказательством). Процессный handoff не входит в счёт: он закрыт в цикле 12,
а в T-013 лишь подтверждён (`git add -N` не требовался, 11 файлов tracked).
Пункт `MAX_FOLLOW_UPS` — важный случай: память
(`t004-review-issues.md`) утверждала, что константа «убрана из `__all__`», и
PLAN записал её в «устаревшее». Аудит показал обратное: её там не было, и это
реальный дефект публичной поверхности. **Память — не доказательство; каждый
пункт надо сверять с кодом.**

## Передано следующим задачам спеки 003

| Пункт | Куда | Почему не в T-013 |
|---|---|---|
| `_format_seconds`, лимиты разделов, валидаторы, `__all__` | T-017 | требует одного нейтрального источника, а не локальной правки |
| prompt injection из `title`/`channel`/`</transcript>` | T-016 | поведение промпта и данных |
| дубль фильтрации сегментов, `int()` вместо округления, `str(item)`, `url` | T-015/T-017 | поведение заметки, не ошибок |
| заголовки шаблона/LLM в `WARNING`, `WARNING` на отсутствии файла, «видео» в `/template`, формат «Таймкодов»/«Цитат» | T-015 | поведение шаблона и логов |
| очистка логов в обход `cli.py`, fail-open, отмена задач, `_LINK_RE`, `video_id` | T-016 | жизненный цикл и доступ |
| тесты `timeline`/конструктора LLM/упаковки, E2E P1, README/`MESSAGE_*`, флаки | T-018 | тестовая поверхность |
| таймаут transcript-api, двойной `_extract_info`, приоритет ошибок, кэш yt-dlp, `TypeError` | T-014 | отдельный YouTube-контракт |

## Устарело/процессное — не заводить задачу

- `inf`/`nan` в `_parse_float` — закрыто в цикле 12 (`math.isfinite`).
- порядок «длительность → `is_configured`» и двойной `WARNING` — закрыто в
  цикле 12; ревью T-012 сняло порядок как UX-вопрос.
- `FAKE_TELEGRAM_TOKEN` — не существует; дубль закрыт в T-009 как
  `FAKE_BOT_TOKEN`.
- `_one_line` в критерии T-017 — неверное имя, помощник называется `one_line`.
- handoff-пункты (diffstat, `git add -N`) — процессные; в T-013 handoff чистый.
- правка `specs/` в диффе — артефакт `pull_from_backlog`/`mark_done` плюс
  содержательные правки ANALYZE (`review-spec-edit-artifact.md`).

## Что аудит говорит о качестве памяти

1. **Один пункт из 13 источников был записан неверно** (`MAX_FOLLOW_UPS`), ещё
   два имени устарели (`_one_line`, `FAKE_TELEGRAM_TOKEN`). Это 3 ошибки в
   описаниях задач, которые ANALYZE исправил только потому, что перечитал
   код. Если бы T-013 выполнялась без аудита, разработчик «закрыл бы»
   несуществующее.
2. **Статус «устарел» надо писать явно.** Формулировка «`MAX_FOLLOW_UPS`
   убран из `__all__`» читалась и как «дефект устранён», и как «константу
   удалили». В фактах писать «дефект: константы нет в `__all__`; статус:
   открыт/закрыт».
3. **Аудит стоит дорого, но окупается:** 9 групп за 241 шаг; если бы они
   остались, каждая следующая задача читала бы те же 13 файлов и не доверяла
   памяти.

Связанные: `cycle-13-spend.md`, `t013-review-issues.md`,
`t004-review-issues.md`, `t005-review-issues.md`, `t011-review-issues.md`,
`invalidurl-escapes-every-adapter.md`, `review-debt-accumulates.md`,
`adr-017-typed-error-contract-url-validation.md`,
`adr-018-review-debt-spec-audit.md`, `audit-artifact-not-in-diff.md`.

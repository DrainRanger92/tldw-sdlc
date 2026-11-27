# Замечания ревью T-012: `APPROVE` с семью P3, FIX не было

Дата: 2026-11-27. Ревью цикла 12 проверяло diff против `7e81432` — одну
задачу T-012 (голосовой ввод шаблона). Вердикт — **`VERDICT: APPROVE`** с
первой итерации, 7 неблокирующих P3; раунда исправлений не было, поэтому
все пункты остаются в коде и переходят в долг
(`review-debt-accumulates.md`). Diff: **16 файлов, +1547/−43**,
untracked-файлов нет. Read-only прогон: **703 passed + 224 env-errors из
927, 0 failed**; `ruff check .` — чисто; `test_voice_handler.py` 15/15,
`test_readme.py` 41/41, `test_voice_e2e.py` не исполнялся (пишет
`DATA_DIR`).

## Пункты

1. **P3 — неполнота тестов.** Нет кейса «ненастроенный адаптер + 61 с →
   «голос недоступен», а не «слишком длинное»», хотя ADR-015 требует
   `is_configured` до длительности; `test_unconfigured_adapter_offers_text_input`
   проверяет только 10 с. Нет и кейса сброса уже показанного кандидата при
   сбое распознавания (`state=confirming_candidate`).
2. **P3 — два текста одного лимита.** `MESSAGE_VOICE_TOO_LONG` в
   `handlers/template.py` и `MESSAGE_SPEECH_TOO_LONG` в
   `adapters/speech.py` — две редакции правила «60 секунд». После
   предпроверки в обработчике сообщение адаптера из Telegram-пути
   недостижимо; один источник — в обработчике.
3. **P3 — два пути импорта `MAX_AUDIO_DURATION_SECONDS`.**
   Константа переехала в `tldw/config.py`, но `adapters/speech.py` оставил
   её в `__all__`, `tests/test_readme.py` импортирует из адаптера, новые
   тесты — из `config`. Нужен один публичный путь (или явный deprecated-
   реэкспорт с тестом).
4. **P3 — дубль списка состояний.** `_DESCRIPTION_STATES` и
   `StateFilter(TemplateStates.waiting_description,
   TemplateStates.confirming_candidate)` в `register()` — два места,
   которые могут разойтись. Достаточно `StateFilter(*_DESCRIPTION_STATES)`.
5. **P3 — асимметрия голоса и текста.** `handle_description` отбивает
   сообщение со ссылкой (`extract_first_url`), голосовой путь — нет:
   надиктованная ссылка уйдёт в LLM-разбор вместо подсказки «закончите
   описание».
6. **P3 — порядок проверок в голосовом пути.** Состояние проверяется до
   `is_configured`: при выключенном распознавании голос вне сценария
   получает «откройте /template и нажмите „Изменить“», и только потом
   «недоступно». Для пользователя это менее полезно, чем сразу
   «доступен текст»; вопрос UX, не дефект.
7. **P3 — дубль тестовых помощников.** `get_file_requests` и константы
   `AUDIO`/`VOICE_TEXT` продублированы в `test_voice_handler.py` и
   `test_voice_e2e.py`; место — `tests/support.py` (каркас
   `Harness`/`FakeSession` при этом переиспользован правильно).

## Что закрыто по пути из T-011

- `math.isfinite` в `_parse_float`: `inf/nan` таймаута → `ConfigError`, а не
  `ValueError` в адаптере;
- порядок «`is_configured` до длительности» — в обработчике, как требовал
  P3 T-011;
- двойной `WARNING` исчез: при выключенном адаптере `transcribe` не
  вызывается;
- handoff-P2 не повторился: `git add -N` выполнен, 16 файлов в diffstat.

**Осталось:** P2 `httpx.InvalidURL` мимо `SpeechError` (T-011) — не входил
в критерии T-012; `media_type` (P3 T-011) — не передаётся из Telegram, но
валидация так и не добавлена; P1 T-004/T-005 открыты.

## Что это значит

- **`APPROVE` снова не означает «долга нет»:** семь P3 добавлены, три
  группы T-011 закрыты; итог **83 → ≈87** пунктов/групп.
- **Для следующей задачи на долг:** включить в критерии (1) тест
  «ненастроенный + 61 с», (2) один источник текста лимита, (3) один путь
  импорта константы, (4) `StateFilter(*_DESCRIPTION_STATES)`, (5)
  `extract_first_url` в голосовом пути, (6) `InvalidURL` → `SpeechError` в
  адаптере. Пункты 6 и 3 — дешёвые и закрывают риск, который иначе
  переедет в третий цикл.
- **Оценка долга:** это M2-задача (2–3 файла + тесты), а не S: правки
  затрагивают `handlers/template.py`, `adapters/speech.py`, `config.py`,
  `tests/support.py` и два тестовых файла.

Связанные: `review-debt-accumulates.md`, `t011-review-issues.md`,
`cycle-12-spend.md`, `tldw-baseline-after-t012.md`,
`invalidurl-escapes-every-adapter.md`, `adr-015-speech-adapter.md`,
`adr-016-voice-input-flow.md`, `app/skills/optional-boundary/SKILL.md`,
`app/skills/shared-helpers/SKILL.md`.

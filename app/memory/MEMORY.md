# Память приложения — индекс

Факты и решения, накопленные этапом LEARN. Одна строка на файл. Обновлён
после цикла 6 (T-006, 282 теста, версия 0.9.0).

## Факты

- [Расход цикла 1 и калибровка оценки M](facts/cycle-01-spend.md) — T-001 (M): 81 шаг, 73,5 тыс. output, 3,85 млн токенов; первый замер, гипотеза «M ведёт себя как L».
- [Расход цикла 2: specs, T-002 и ревью](facts/cycle-02-spend.md) — подготовка specs + T-002: 83 шага, 3,20 млн токенов до LEARN; T-002 вдвое дешевле T-001, ревью — 40 % расхода задачи.
- [Расход цикла 3: T-003 и ревью](facts/cycle-03-spend.md) — T-003 (M): 89 шагов, 94,1 тыс. output, 5,86 млн токенов; 46 % вызовов — до первой строки кода.
- [Расход цикла 4: T-004 и ревью](facts/cycle-04-spend.md) — T-004 (M): 100 шагов, 113,4 тыс. output, 7,31 млн токенов; самый дорогой по output цикл, ревью — 40 шагов и 37 % output.
- [Расход цикла 5: T-005 и ревью](facts/cycle-05-spend.md) — T-005 (M): 94 шага (66 + 28), 79,2 тыс. output, 7,10 млн токенов; первое попадание прогноза, ревью — 28 шагов и 25 % output.
- [Расход цикла 6: T-006 и ревью](facts/cycle-06-spend.md) — T-006 (M): 101 шаг (62 + 39), 80,7 тыс. output, 6,90 млн токенов; прогноз 80–120/70–100 попал, ревью — 39 % шагов и 32 % output.
- [LEARN стоит как половина задачи и в бюджет не входит](facts/learn-cost-unaccounted.md) — циклы 1–5: 51–117 шагов и 51–70 тыс. output (63–117 % шага задачи), цикл 6 — ≥ 82 шага и ≥ 73 тыс.; считать «до LEARN» и «с LEARN» отдельно.
- [Оценка M стабилизировалась: EXECUTE предсказуем, разброс даёт ревью](facts/estimate-m-stabilized.md) — шесть M: EXECUTE 58–66 шагов, REVIEW 23–40; T-005/T-006 попали в прогноз, строить вилку двумя слагаемыми.
- [Калибровка размера M: два попадания подряд](facts/estimate-calibration-m.md) — M = EXECUTE 58–70 + REVIEW 25–40 (60–110 шагов, 60–110 тыс. output); композиция и слой в знакомом фреймворке — M, новый чужой контракт — L.
- [Прогнозы занижены для новых интеграций, а не для любой M](facts/estimate-forecast-underestimates.md) — T-004: 100 против 50–70; T-005: 94 против 80–120; T-006: 101 против 80–120 — коррекция 1,5–2× нужна только при новом чужом API.
- [Композиция готовых контрактов дешевле новой интеграции](facts/composition-is-cheaper-than-integration.md) — T-005: 94 шага и 79 тыс. output без нового внешнего контракта; триггер L — новая поверхность, а не число слоёв; строки тестов не считать.
- [Базовая линия TLDW после T-001](facts/tldw-baseline-after-t001.md) — исторический срез: каркас, env-конфиг, 58 тестов.
- [Базовая линия TLDW после T-002](facts/tldw-baseline-after-t002.md) — исторический срез: модель `Note`, Obsidian-рендер, имя файла, 116 тестов.
- [Базовая линия TLDW после T-003](facts/tldw-baseline-after-t003.md) — исторический срез: YouTube-адаптер, `parse_video_id`, transcript-api + fallback yt-dlp, 163 теста.
- [Базовая линия TLDW после T-004](facts/tldw-baseline-after-t004.md) — исторический срез: LLM-адаптер (`LLMClient`, `VideoSummary`, ресурсы промпта, повторы 429/5xx), 212 тестов.
- [Базовая линия TLDW после T-005](facts/tldw-baseline-after-t005.md) — исторический срез: `SummaryService.process`, `storage/notes.py`, 257 тестов.
- [Базовая линия TLDW после T-006](facts/tldw-baseline-after-t006.md) — актуальный срез: `handlers/link.py`, `to_thread`, отправка `.md`, инъекция сервиса в сборку, 282 теста; T-007–T-009 ещё нет.
- [spec.md и plan.md 001-mvp — заготовки](facts/specs-001-artifacts-stubs.md) — исторический факт цикла 1; закрыто в цикле 2.
- [specs/001-mvp: spec.md и plan.md заполнены](facts/specs-001-filled-in-cycle-02.md) — FR-1…FR-11, план этапов 1–7, источник истины; `plan.md` хардкодит текущий инкремент.
- [`plan.md` фиксирует устаревший «текущий инкремент»](facts/plan-md-increment-drift.md) — 5/5 циклов: строка всё ещё называет T-002, отметки «сделано» только у T-001; убрать номер задачи из фразы.
- [Мелкие замечания ревью T-001](facts/t001-review-minor-issues.md) — inf/nan в timeout, дубли тестовых помощников, имя теста, порядок в build_application.
- [Замечания ревью T-002, оставшиеся в коде](facts/t002-review-issues.md) — строка вместо кортежа, bool-таймкод, noncharacters в YAML, slug в байтах, дублирование.
- [Замечания ревью T-003, оставшиеся в коде](facts/t003-review-issues.md) — P1: нет таймаута transcript-api, двойной `_extract_info`, приоритет ошибок, кэш yt-dlp.
- [Замечания ревью T-004, оставшиеся в коде](facts/t004-review-issues.md) — P1: `InvalidURL`/`RuntimeError` мимо `LLMError` (открыты после T-006); `MAX_FOLLOW_UPS` убран из `__all__`; третья копия `_one_line`, prompt injection, версии.
- [Замечания ревью T-005, оставшиеся в коде](facts/t005-review-issues.md) — P1: пустой транскрипт не отвергается (открыт после T-006), вырожденный маркер усечения, учёт при `budget == 0`; P2 — дубль фильтрации, `int()` вместо округления, `str(item)`, `url`.
- [Замечания ревью T-006, оставшиеся в коде](facts/t006-review-issues.md) — P2: untracked-файлы не попали в diffstat; P3: граница `_LINK_RE`, URL в логе, `close()` без отмены задач, нет лимита параллелизма, флаки-тест; долг P1 T-004/T-005 не закрыт.
- [Долг ревью копится шесть циклов](facts/review-debt-accumulates.md) — 45 пунктов за 6 циклов, все APPROVE, отдельной задачи на долг нет; в T-006 закрыты версии, P1 маскируются `except Exception`.
- [Ревью и untracked-файлы](facts/review-diff-untracked.md) — 2/2 цикла сломано до `git add -N`, 3/3 чистых после (T-003–T-005), регрессия в цикле 6: 109 строк вместо ~721.
- [Ревьюер в read-only песочнице не может прогнать pytest](facts/reviewer-readonly-tests.md) — 6/6 циклов; в цикле 6 `-p no:cacheprovider -s` дал 198 passed + 84 env-errors, новый файл — 13/21; APPROVE не равен зелёным тестам.
- [Интеграция со сторонними библиотеками: сначала проба API](facts/third-party-api-discovery.md) — T-003: 46 % вызовов до кода; T-004: 22 %, стоимость ушла в контракт; T-006: 43,5 %, но aiogram уже стоял и был офлайновый путь.
- [Офлайновые тесты aiogram-обработчика](facts/aiogram-offline-handler-tests.md) — `FakeSession(BaseSession)` + `Dispatcher.feed_update`, `wait_idle` для фоновых задач, `threading.Event` вместо sleep, `tmp_path` только файловым кейсам.
- [Git в DSH-сессиях сломан двумя переменными](facts/git-env-broken-in-dsh.md) — 6/6 циклов: `GIT_CONFIG_KEY_0` и `dubious ownership`; обёртка `git_ws`, 5–15 шагов на сессию; скил `git-in-sandbox`.

## Решения (ADR)

- [ADR-001: конфигурация только из env](decisions/adr-001-config-env-only.md) — fail-fast, секреты вне repr, `.env` не перетирает env.
- [ADR-002: слои пакета и адаптеры](decisions/adr-002-layered-adapters.md) — handlers → services → adapters/storage, сборка без сети.
- [ADR-003: модель заметки и ручной YAML-рендер](decisions/adr-003-note-model-manual-yaml.md) — frozen dataclasses и чистые функции в `services/note.py`, YAML вручную, PyYAML только в тестах.
- [ADR-004: контракт YouTube-адаптера](decisions/adr-004-youtube-adapter-contract.md) — два источника за протоколами, fallback yt-dlp, типизированные ошибки и их приоритет, метаданные необязательны, адаптер синхронный.
- [ADR-005: контракт LLM-адаптера](decisions/adr-005-llm-adapter-contract.md) — промпт/шаблон как ресурсы пакета, строгая JSON-валидация в `VideoSummary`, повтор только на 429/5xx, `LLMError` и подклассы, синхронный клиент для `to_thread`.
- [ADR-006: контракт сервиса конспекта](decisions/adr-006-summary-service.md) — композиция за протоколами `VideoSource`/`SummaryProvider`, порог до LLM, усечение по сегментам с маркером в лимите, `source` — ссылка пользователя, атомарная запись в `storage/`.
- [ADR-007: Telegram-слой](decisions/adr-007-telegram-handler.md) — тонкий обработчик, «принял» до тяжёлых вызовов, фоновая задача + `asyncio.to_thread`, `FSInputFile` по сохранённому пути, протокол `SummaryProcessor`, инъекция сервиса в `build_application`, общее сообщение об ошибке до T-008.

## Скилы

- [intake](../skills/intake/SKILL.md) — как уточнять заявки в этом проекте; «Контекст проекта» обновлён после T-006 (Telegram-сценарий, 282 теста, M = EXECUTE 55–70 + REVIEW 25–40, новая чужая интеграция → L).
- [cycle-accounting](../skills/cycle-accounting/SKILL.md) — как восстановить расход цикла по кэшу DSH-сессий и журналам: шаги, токены, роли, LEARN отдельно, сверка с оценкой.
- [offline-tests](../skills/offline-tests/SKILL.md) — офлайновые тесты: fake_env, tmp_path для DATA_DIR, фейковые адаптеры и `httpx.MockTransport`, фейковая сессия aiogram + `Dispatcher.feed_update`, `wait_idle`, ресурсы пакета, инъекция `sleep`.
- [review-handoff](../skills/review-handoff/SKILL.md) — `git add -N` + `git diff HEAD`, сверка `git status -uall` с diffstat (регрессия цикла 6), read-only pytest (`-p no:cacheprovider -s`), граница `specs/`, бюджет ревью.
- [git-in-sandbox](../skills/git-in-sandbox/SKILL.md) — обёртка `git_ws`: обход `missing config key GIT_CONFIG_KEY_0` и `dubious ownership` в DSH-сессиях.
- [shared-helpers](../skills/shared-helpers/SKILL.md) — правило трёх копий для `_one_line`/`format_timecode`, единый источник числовых лимитов, нейтральный модуль без инверсии слоёв; в T-006 новых копий не появилось.

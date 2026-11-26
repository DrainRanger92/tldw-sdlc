---
name: cycle-accounting
description: Как восстановить фактический расход цикла TLDW (шаги, output, токены, время по ролям) из данных DSH-сессий, когда journal.jsonl ещё не содержит текущий цикл, и как сравнить его с оценкой размера задачи.
---

# Замер расхода цикла

Каждый LEARN в этом проекте начинает с одного и того же: `journal.jsonl`
содержит только **завершённые** циклы, а текущий в нём ещё не записан.
Процедура восстановления по кэшу сессий повторялась в циклах 1–7; шаги ниже —
её устойчивая форма. Без неё LEARN каждый раз заново выясняет, где лежат
токены и как отличить шаг модели от вызова инструмента.

## Где лежат данные

- `.aidevteam/journal.jsonl` — по строке на завершённый цикл:
  `cycle`, `started`, `finished`, `ok`, `commit`, `summary`, `usage`.
  `usage` оркестратора пустой (0 токенов), поэтому для отчёта он не годится.
- `.aidevteam/state.json` — `cycles` (сколько завершено), `increment_no`
  (номер текущего цикла), `last_processed_commit`.
- `.aidevteam/dsh-home/storages/session_projcache/sessions/session-*.json` —
  по файлу на сессию: `record.identity.createdAt` (мс) и
  `record.rows.tokenUsage.val.totals` =
  `{uncachedInputTokens, outputTokens, cacheReadTokens, cacheWriteTokens}`.
- `.aidevteam/dsh-home/sessions/--workspace--/session-*/session.v4.jsonl.zstd` —
  сжатый zstd JSONL журнал сессии: события `step/start` (шаг модели),
  `tool/call` (вызов инструмента), `user/message` (роль и промпт),
  `assistant/message` (финальный отчёт), `turn/start`/`turn/end`.

## Процедура

1. Возьмите границу цикла: `finished` последней строки `journal.jsonl`.
2. Отберите сессии с `createdAt` позже этой границы (не по mtime: mtime —
   время последней записи, оно сдвигается).
3. Классифицируйте роль по первому `user/message` (`seq` ≈ 11):
   «Выполни задачу T-…» — developer (стадия EXECUTE); «Исправь замечания
   ревьюера…» — тот же developer, но стадия **FIX**; «Проанализируй
   изменения…» — reviewer (стадии REVIEW-1, REVIEW-2 и т.д.); LEARN/analyst —
   остальные. Промпт содержит и статистику диффа, её удобно взять в отчёт.
4. Для каждой сессии: число шагов = число `step/start`; вызовов инструментов =
   число `tool/call`; токены — из `tokenUsage.totals`.
5. Сложите стадии. **Всего токенов = uncachedInput + output + cacheRead**
   (так считались циклы 1–8). Cache write в этих замерах 0.
6. Отдельно посчитайте «до первой записи кода»: номер первого `write`/`edit`
   по коду в списке `tool/call` / общее число вызовов.
7. LEARN-сессию (текущую) в расход цикла не включайте: сравнивайте «цикл до
   LEARN», как в `cycle-0N-spend.md`.

## Цикл с раундом исправлений (с цикла 8)

Пока ревью отвечало `APPROVE` с первой итерации, в цикле было две сессии. С
первым `ISSUES` (T-008) сессий стало четыре; в цикле 9 (T-009) — снова четыре,
то есть **пять стадий стали нормой, а не исключением**. Их надо считать
**отдельным слагаемым**, иначе «прогноз не попал» будет валить вилку M, хотя
она верна:

1. Отсортируйте сессии по `createdAt` и разметьте стадии:
   `EXECUTE → REVIEW-1 → FIX → REVIEW-2` (при повторном `ISSUES` — ещё
   `FIX → REVIEW-3`).
2. В таблицу отчёта добавьте строки FIX и каждого раунда ревью отдельно;
   в «Итого» — сумму.
3. Считайте производные:
   - **доля ревью** = (сумма REVIEW) / итог по шагам и по output; в цикле 8 —
     75/178 = 42 % шагов и 42,7 % output (в циклах 1–7 — 20–40 %);
   - **FIX к EXECUTE** = шаги FIX / шаги EXECUTE; в цикле 8 — 55/48 = 1,15,
     в цикле 9 — 49/41 = 1,20 (не «резерв 0,3–0,5»: раунд исправлений дороже
     работы);
   - **REVIEW-2 к REVIEW-1** — в цикле 8 — 52/23 = 2,3 по шагам и 4,2 по
     output, в цикле 9 — 60/28 = 2,14 и 2,06: второй проход перепроверяет, а
     не только читает, и почти всегда дороже первого.
4. Проверьте вердикты по последнему assistant-сообщению каждой сессии
   (`VERDICT: APPROVE|ISSUES`) — по ним восстанавливается число раундов.
5. Сравнивайте факт с прогнозом **по слагаемым**: в T-008 EXECUTE (48) и
   REVIEW-1 (23) попали в вилку, а промах целиком в FIX и REVIEW-2. Отчёт без
   разбивки создаёт ложный вывод «вилка M сломалась».

## Готовый скрипт

```bash
cd /workspace
python3 - <<'PY'
import json, glob, os, datetime, zstandard as zstd

def sessions():
    d = '.aidevteam/dsh-home/storages/session_projcache/sessions'
    for f in glob.glob(d + '/*.json'):
        rec = json.load(open(f))['record']
        yield f, rec

def steps(sid):
    p = f'.aidevteam/dsh-home/sessions/--workspace--/{sid}/session.v4.jsonl.zstd'
    evs = [json.loads(l) for l in
           zstd.ZstdDecompressor().stream_reader(open(p, 'rb')).read()
           .decode().splitlines() if l.strip()]
    steps = sum(e['type'] == 'step/start' for e in evs)
    tools = sum(e['type'] == 'tool/call' for e in evs)
    first = next((i for i, e in enumerate(evs)
                  if e['type'] == 'tool/call' and e['data']['name'] in
                  ('write', 'edit')), None)
    role = next((e['data']['content'][0]['text'][:40]
                 for e in evs if e['type'] == 'user/message'
                 and e['data'].get('source', {}).get('kind') == 'user'), '')
    return steps, tools, first, role

for f, rec in sorted(sessions(), key=lambda x: x[1]['identity']['createdAt']):
    t = rec['rows'].get('tokenUsage', {}).get('val', {}).get('totals', {})
    created = datetime.datetime.fromtimestamp(
        rec['identity']['createdAt'] / 1000, datetime.UTC).strftime('%H:%M:%S')
    sid = os.path.basename(f)[:-5]
    try:
        st, tools, first, role = steps(sid)
    except FileNotFoundError:
        st = tools = first = None; role = ''
    print(created, sid[:22], 'steps', st, 'tools', tools,
          'first_edit', first, 'in', t.get('uncachedInputTokens'),
          'out', t.get('outputTokens'), 'cache', t.get('cacheReadTokens'),
          '|', role.replace('\n', ' '))
PY
```

Скрипт печатает все сессии по времени создания; нужные строки — те, что
позже последнего `finished` в журнале. Роль видна в последнем столбце.

## Цикл 9: тестовая задача без продакшн-кода

В цикле 9 продакшн-код не менялся (одна строка версии), но стадий всё равно
пять. При разборе «куда ушли шаги» смотрите не на строки кода, а на файлы:
`test_e2e.py`, `test_readme.py`, `tests/support.py`, `README.md` и
переписанный `test_link_handler.py`. EXECUTE потратил 43,9 % вызовов до первой
записи (чтение контрактов `MESSAGE_*`, `FRONTMATTER_KEYS`,
`SECTION_HEADINGS`), а FIX правил шесть тестовых файлов. Вывод для замера:
**если в диффе мало кода — считайте стадии по сессиям, а не по diffstat.**

## Расход самого LEARN

Ретроспектива — такая же сессия и считается тем же способом (роль — по
первому `user/message` «Ретроспектива цикла…»). Она **не входит** в
`cycle-0N-spend.md`, но её стоит писать отдельной строкой: в циклах 1–6 LEARN
стоил 51–117 шагов и 51–78 тыс. output, то есть 63–117 % шага задачи и
62–96 % её output (см. `learn-cost-unaccounted.md`); цикл 7 — 99 шагов и
91,5 тыс. output. В отчёте различайте «цикл до LEARN» и «цикл с LEARN».

**Мерить LEARN после закрытия сессии.** Отчёт, написанный на середине,
занижает себя: в цикле 6 отчёт говорил 82 шага / 73,4 тыс. output, а в кэше
после последнего шага — 88 / 77,8 тыс. Пока сессия идёт, пишите «≥» и
уточняйте по `tokenUsage.totals`, когда она закрыта.

## Что писать в отчёт

Таблица на строку на стадию (developer, reviewer, при возврате — FIX и
REVIEW-2, затем итог) с колонками: время, шаги, новый input, output, cache
read, всего токенов. Дальше — обязательные производные:

- **число раундов ревью и вердикты** (`APPROVE`/`ISSUES`): от этого зависит,
  какие слагаемые вообще есть в цикле;
- доля ревью по шагам и по output (циклы 1–7: 0,40–0,89 шага ревьюера на шаг
  разработчика и 20–39 % output; цикл 7 — 0,51 и 39,5 %; цикл 8 с двумя
  проходами — 1,56 и 42,7 %);
- **FIX к EXECUTE** — 0,3–0,5 в норме, 1,15 при дефектах в новом модуле
  (T-008);
- output на шаг (T-004 — 1 134, T-005 — 842, T-006 — 799, T-007 — 488,
  T-008 — 798): показывает, была ли работа «широкой» по контексту или глубокой
  по рассуждению; падение ниже 600 — признак многомелких правок, а не
  дешёвой задачи. Считайте отдельно по стадиям: в T-008 EXECUTE — 1 021,
  FIX — 590, REVIEW-1 — 512, REVIEW-2 — 940;
- доля cache read (96–97 % объёма) — **не складывать её с новым input** при
  оценке стоимости;
- сравнение с прогнозом и с меткой размера: шаги, output, новые строки,
  критерии; отдельно — попал ли факт в вилку и в какую сторону. **Прогноз
  сравнивайте по слагаемым** (EXECUTE / REVIEW / FIX), иначе непонятно, что
  промахнулось: у T-007 обе величины попали в вилку M, а метка S — нет; у
  T-008 EXECUTE и REVIEW-1 попали, а FIX и REVIEW-2 не были предусмотрены.

## Ловушки

- **Не берите `usage` из `journal.jsonl`** — там нули, оркестратор считает
  вызовы ролей (`CallBudget`), а не токены.
- **Не путайте шаг и вызов инструмента:** в T-005 у разработчика 66 шагов и
  80 вызовов. В отчётах цикла используйте шаги, вызовы — только для разбора
  «куда ушло».
- **Не считайте сессию LEARN частью цикла** до её завершения: она появится в
  кэше, но её расход относится к ретроспективе.
- **Не сравнивайте output между циклами без числа шагов:** output на шаг
  меняется в 1,3 раза при том же объёме кода.
- **Проверяйте exit code** каждой команды: `zstd`-файл может быть ещё
  дописывается, и `json.loads` упадёт на последней строке — фильтруйте
  пустые/битые строки, как в скрипте.

## См. также

- `app/memory/facts/cycle-09-spend.md` — свежий пример отчёта с двумя
  `ISSUES` (178 шагов, 154,7 тыс. output, 1 строка продакшн-кода).
- `app/memory/facts/cycle-08-spend.md` — предыдущий пример с раундом
  исправлений (178 шагов, 142,1 тыс. output, два прохода ревью).
- `app/memory/facts/cycle-07-spend.md` — предыдущий замер (106 шагов,
  51,7 тыс. output, ревью 36 шагов).
- `app/memory/facts/cycle-06-spend.md` — замер T-006 (101 шаг,
  80,7 тыс., ревью 39).
- `app/memory/facts/cycle-05-spend.md` — замер T-005 (94 шага, 79 тыс.).
- `app/memory/facts/learn-cost-unaccounted.md` — почему LEARN считать
  отдельно и как не занизить его.
- `app/memory/facts/estimate-calibration-m.md` — как сравнивать с оценкой
  размера.
- `app/memory/facts/estimate-misses-fix-round.md` — почему прогноз без FIX
  занижен.
- `app/memory/facts/review-fix-round-cost.md` — цена возврата ревью.
- `app/memory/facts/estimate-size-s-collapsed-into-m.md` — почему «S» на
  деле M и как это видно по шагам.
- `app/skills/size-forecast/SKILL.md` — как строить прогноз до цикла.
- `app/skills/fix-round/SKILL.md` — как провести FIX и REVIEW-2.
- `app/memory/facts/composition-is-cheaper-than-integration.md` — почему
  одинаковое число шагов может стоить по-разному.

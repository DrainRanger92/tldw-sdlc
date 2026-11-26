---
name: cycle-accounting
description: Как восстановить фактический расход цикла TLDW (шаги, output, токены, время по ролям) из данных DSH-сессий, когда journal.jsonl ещё не содержит текущий цикл, и как сравнить его с оценкой размера задачи.
---

# Замер расхода цикла

Каждый LEARN в этом проекте начинает с одного и того же: `journal.jsonl`
содержит только **завершённые** циклы, а текущий в нём ещё не записан.
Процедура восстановления по кэшу сессий повторялась в циклах 1–5; шаги ниже —
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
   «Выполни задачу T-…» — developer; «Проанализируй изменения…» — reviewer;
   LEARN/analyst — остальные. Промпт содержит и статистику диффа, её удобно
   взять в отчёт.
4. Для каждой сессии: число шагов = число `step/start`; вызовов инструментов =
   число `tool/call`; токены — из `tokenUsage.totals`.
5. Сложите EXECUTE и REVIEW. **Всего токенов = uncachedInput + output +
   cacheRead** (так считались циклы 1–5). Cache write в этих замерах 0.
6. Отдельно посчитайте «до первой записи кода»: номер первого `write`/`edit`
   по коду в списке `tool/call` / общее число вызовов.
7. LEARN-сессию (текущую) в расход цикла не включайте: сравнивайте «цикл до
   LEARN», как в `cycle-0N-spend.md`.

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

## Расход самого LEARN

Ретроспектива — такая же сессия и считается тем же способом (роль — по
первому `user/message` «Ретроспектива цикла…»). Она **не входит** в
`cycle-0N-spend.md`, но её стоит писать отдельной строкой: в циклах 1–4 LEARN
стоил 51–117 шагов и 50–70 тыс. output, то есть 63–117 % шага задачи и
62–93 % её output (см. `learn-cost-unaccounted.md`). В отчёте различайте
«цикл до LEARN» и «цикл с LEARN».

## Что писать в отчёт

Таблица на три строки (developer, reviewer, итог) с колонками: время, шаги,
новый input, output, cache read, всего токенов. Дальше — обязательные
производные:

- доля ревью по шагам и по output (в циклах 1–5: 0,40–0,89 шага ревьюера на
  шаг разработчика и 25–58 % output);
- output на шаг (T-004 — 1 134, T-005 — 842): показывает, была ли работа
  «широкой» по контексту или глубокой по рассуждению;
- доля cache read (96–97 % объёма) — **не складывать её с новым input** при
  оценке стоимости;
- сравнение с прогнозом и с меткой размера: шаги, output, новые строки,
  критерии; отдельно — попал ли факт в вилку и в какую сторону.

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

- `app/memory/facts/cycle-05-spend.md` — свежий пример отчёта и цифры.
- `app/memory/facts/learn-cost-unaccounted.md` — почему LEARN считать
  отдельно.
- `app/memory/facts/estimate-calibration-m.md` — как сравнивать с оценкой
  размера.
- `app/memory/facts/composition-is-cheaper-than-integration.md` — почему
  одинаковое число шагов может стоить по-разному.

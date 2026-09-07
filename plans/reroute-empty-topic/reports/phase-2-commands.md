# Phase 2 — Items 2.1 and 2.6, run by the orchestrator

Both are commands rather than edits, so they are recorded here rather than in
a diff.

## Item 2.1 — `just precommit` over the whole tree

Run from the repository root with the cache sentinel deleted
(`tools/ghmem/tmp/.green-sentinel`), so the gate really ran rather than
reporting `✓ Cached (inputs unchanged)`:

```
============================= 283 passed in 56.12s =============================
✓ ruff check · docformatter -c · mypy · 283 passed · 47 bats · line limits
```

The runbook's wider expectation holds: `test_unroute.py`, `test_reenter.py`,
`test_requeue.py` and `test_cli_close.py` are green **unchanged** — the upsert
changed what runs under all of them and moved no behaviour. The three modules
reading the shared `routing_events.jsonl` are green too; only
`test_routing_replay.py` needed the retargeting slice 1 applied.

## Item 2.6 — the live mirror against the new invariant

Query run verbatim from the runbook, from the repository root:

```
python3 -c "
import sqlite3
c = sqlite3.connect('file:corpus/cache.sqlite?mode=ro', uri=True)
print(c.execute('select t.name from topic t where not exists '
                '(select 1 from route r where r.verdict = t.name)').fetchall())
"
```

Result: `[]` — **zero rows**. The mirror holds no `Topic` row that no `Route`
names, so it already agrees with the new reducer and nothing further is
needed. No `ghmem rebuild` was run, and `corpus/routing.jsonl` was not
written.

The query checks that direction only, which is the only half the old reducer
could have violated; it says nothing about the converse.

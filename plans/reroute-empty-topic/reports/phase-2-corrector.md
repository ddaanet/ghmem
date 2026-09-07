# Phase 2 — boundary review (whole-phase corrector)

Diff under review: `git diff 082819e..HEAD` restricted to
`docs/ghmem-decisions.md`, `docs/ghmem-design.md`,
`docs/ghmem-rejected.md`, `docs/ghmem-changelog.md` — commits 5a97c49,
18d4be1, 85b1ed9, 472802d. `.claude/handoff-*.md` and the `memory`
submodule pointer in that range were ignored as session bookkeeping.

**Verdict: pass, four fixes applied.** All four are prose
corrections in the changed regions; no claim in the diff was found
false against the code, no citation fails to resolve, and no runbook
prescription was left unmet. `just precommit` reports
`✓ Cached (inputs unchanged)` — docs are outside the gate's cache
inputs, as the runbook says.

---

## 1. Every claim against the code

I read `_apply_route_event`, `_release_topic`, `_mark_topic_dirty`,
`_apply_reenter_event` and `_apply_unroute_event`
(`tools/ghmem/src/ghmem/routing_reduce.py`), `reserve_dispatch` and
`record_routing_event` (`tools/ghmem/src/ghmem/dispatch_store.py`),
`record_verdict` (`tools/ghmem/src/ghmem/triage.py`) and
`build_status` (`tools/ghmem/src/ghmem/status.py`).

| Claim | Where | Verified against |
|---|---|---|
| `route` captures the pre-merge verdict, replaces the `Route`, then dirties-or-drops the departed topic | changelog | `_apply_route_event`: `left = previous.verdict` before `session.merge`, `_release_topic(session, left)` last |
| the invariant is "a `Topic` row exists exactly while some `Route` carries its name as a verdict other than `no-signal`" | decisions, changelog | join side skips `NO_SIGNAL`, so no `Topic` named `no-signal` is ever minted; `_release_topic` is never called with `NO_SIGNAL`; the `COUNT` is over `Route.verdict == name` |
| a route to `no-signal` from a topic's last item drops it, and nothing re-mints it | changelog | `if data["verdict"] != NO_SIGNAL: _mark_topic_dirty(...)` is skipped; the release still runs |
| a same-topic re-route dirties rather than drops, "because the replaced route still names the topic once the merge has flushed" | changelog | join-side `_mark_topic_dirty`'s `session.execute` autoflushes the merge's `UPDATE`; the `COUNT` then returns 1 and `_release_topic` takes its `remaining` branch |
| that path is reachable only through re-entry, since `record_verdict` refuses a second live verdict | changelog | `record_verdict` raises `already_routed` when `existing_route.item_updated_at >= item.updated_at`; only a route gone stale — re-entry's whole path — clears it |
| `Dispatch` keyed on `item_id`; read-then-write cannot enforce the loser learning it lost; `ON CONFLICT ... DO NOTHING` collapses check and write; the append sits between insert and commit | decisions (backfilled entry) | `reserve_dispatch`'s docstring and body, statement for statement |
| the asymmetry against `apply_routing_event`'s `dispatched` branch | decisions | `_apply_dispatched_event` is an unconditional `session.merge` |
| a get-then-add lets two workers race the `INSERT`, and because the reducer runs before the ledger append the loser's verdict never lands, dying with an opaque `IntegrityError` | decisions (upsert entry) | `record_routing_event`'s docstring is explicit that the reducer runs first inside an uncommitted transaction and the append follows; nothing there catches `IntegrityError` |
| no surface reports the orphaned doc: `build_status` reads the store and the fetch ledger, never the filesystem | decisions | `build_status` reads `all_issues_and_prs`, `routes`, `dispatched_unrouted`, `topics` and the fetch ledger only |
| `topic.assemble` refuses an unrouted name with `TopicNotRoutedError` | rejected | `topic.py:53` |

One claim did **not** survive as written, and is fix 1 below.

## 2. The weakened `_mark_topic_dirty` docstring — no overclaim, no
contradiction

The docstring now says the `session.execute()` autoflush is "a guard
rather than the thing holding this path up, on the evidence: every
route into a topic already flushes before reaching here". The
decisions entry **`_mark_topic_dirty` is a conditional upsert.**
stands entirely on the cross-process mint race and never mentions the
within-replay delete-then-recreate, so it neither overclaims past the
weakened docstring nor contradicts it. The changelog paragraph is the
same: "a get-then-add leaves a cross-process mint race open". Correct
as written; nothing to fix.

## 3. Softening parity — holds

`_release_topic`'s docstring: "Nothing here touches
`docs/design/<name>.md`. This reducer is replayed wholesale by
`rebuild_from_ledgers`, so a filesystem delete inside it would make a
rebuild destructive, and `ghmem index` keeps listing the orphaned doc
from disk."

The decisions entry: "Nothing removes `docs/design/<name>.md` — the
reducer writes SQLite and is replayed by `rebuild`, so a filesystem
delete inside it would make replay destructive."

Both drop the "a synthesized doc is prose about items that really were
routed there" justification and stand on replay-destructiveness alone.
The `ghmem index` half of the docstring's sentence is carried by the
entry's cost paragraph rather than its closing clause, which is where
Item 2.2 asks for it. Parity holds.

## 4. Citations resolve

- FR7's Rationale line cites **Emptying a topic deletes its `Topic`
  row** — matches the retitled entry verbatim, minus the trailing
  period, which is the design doc's uniform convention (compare
  **Freshness is stamped on the route** on the same line).
- The upsert entry points at the backfilled one as "the reservation
  above", which resolves against its title **A live reservation is one
  conditional insert, never a read-then-write.** The runbook asks for
  the reasoning "pointed at not restated"; it is.
- The changelog cites **Emptying a topic deletes its `Topic` row** in
  bold and names the other two entries descriptively ("backfilling the
  reservation decision behind `reserve_dispatch`", and the upsert,
  which its own third paragraph narrates). The changelog file's
  convention does not cite decision titles at all, so this is
  consistent with the file rather than short of the interface.
- `grep -rn "Unrouting a topic" docs/` returns **nothing**: the old
  title survives nowhere under `docs/`.

## 5. The dropped Open question left nothing dangling

The bullet was removed in full, `Rationale as it stands:` line
included; the `## Open questions` heading and all three remaining
bullets (NFR10's gate, the provisional constants, the `topic merge`
event) are intact. No reference to it survives anywhere in `docs/` —
the only hit for "Open question" outside section headings and topic
docs is the changelog's own "drops the Open question this settles",
which is a correct backward reference in the file whose job that is.

I also swept `docs/*.md` for statements Phase 1 falsified
(`empty dirty`, `emptied topic`, `dirty row`, `scoped to "unroute"`,
`re-rout*`). Every hit is inside the four edited regions.
`docs/ghmem-harness.md:60` ("marks the topic dirty") stays true of the
topic the item *joins* and is out of scope per the runbook.

## 6. Line wrapping — conforms

After my fixes, no line in `docs/ghmem-changelog.md`,
`docs/ghmem-rejected.md` or `docs/ghmem-decisions.md` exceeds 72
columns except `ghmem-decisions.md:212` (76), which is pre-existing and
untouched by this phase. `docs/ghmem-design.md` has no line over 74.

## 7. The changelog's corpus reading — re-run and confirmed

```
python3 -c "
import sqlite3
c = sqlite3.connect('file:corpus/cache.sqlite?mode=ro', uri=True)
print('empty topics:', c.execute('select t.name from topic t where not exists (select 1 from route r where r.verdict = t.name)').fetchall())
print('routes:', c.execute('select count(*) from route').fetchone())
print('topics:', c.execute('select count(*) from topic').fetchone())
print('singletons:', c.execute(\"select count(*) from (select verdict from route where verdict != 'no-signal' group by verdict having count(*) = 1)\").fetchone())
"
```

```
empty topics: []
routes: (95,)
topics: (71,)
singletons: (57,)
```

All four figures match the entry. The phrasing "95 routes over 71
topics, 57 of them singletons" is also the file's own established
wording — the 2026-09-02 entry uses it verbatim — so it was left
alone, even though 4 of the 95 routes are `no-signal` and name no
topic.

## Fixes applied

**Fix 1 (major) — `docs/ghmem-decisions.md`, the invariant's
enumeration asserted something false.** It read: "withdrawing the last
verdict, re-routing it to another topic, and re-routing it to
`no-signal` all empty a topic". Re-routing an item empties a topic only
when that item was the last route naming it, so the clause as written
claimed every re-route empties one. Two smaller problems rode along:
"it" took *the verdict* as its antecedent where the item is what moves,
and routing to `no-signal` was called "re-routing it to `no-signal`",
which reads as a fourth topic. Now: "withdrawing a topic's last
verdict, re-routing that item to another topic, and routing it to
`no-signal` all leave the topic empty, and all release it."

**Fix 2 (minor) — `docs/ghmem-decisions.md`, the cost paragraph's
antecedent.** It opened "The cost this knowingly takes on", with a bare
`this`. That paragraph is the only untitled paragraph in the whole
`## Design decisions` section — every other entry there is one
bold-titled paragraph — so a reader landing on it has nothing binding
it to the decision above. Opened it "The cost this rule knowingly takes
on" instead. I did **not** restructure it into the preceding paragraph
(24 lines would be the longest block in the file by 9) and did not move
it to `docs/ghmem-design.md`'s `## Limitations`, which would override
Item 2.2's stated placement.

**Fix 3 (minor) — `docs/ghmem-design.md`, FR7's clause implied
`reenter` can empty a topic.** It read "whichever of the three
mechanisms reached it". `_apply_reenter_event` frees the `Dispatch` and
dirties the route's topic; it never deletes a `Route`, so it cannot
leave a topic with no route naming it — only re-route and `unroute`
can. The "three mechanisms" framing is vacuously true but asserts
otherwise by implicature. Now "whichever mechanism reached it", which is
also the runbook's own wording. The Rationale run was rewrapped by one
word to keep the line from running short.

**Fix 4 (nit) — `docs/ghmem-changelog.md`, tense.** "it used to hold on
the `unroute` path alone and left an empty dirty row reachable" — the
second verb hangs off "used to", so it must be "leave". Fixed and the
paragraph rewrapped.

## Considered and left alone

- **The rejected entry's "an empty dirty row is not a marker but a
  worker sent at a topic no route names".** A row is not a worker; it
  is metonymy for the dispatch the row causes, and the sentence is
  unambiguous. Style, not accuracy.
- **"95 routes over 71 topics"** — see check 7. The file's own phrase.
- **The changelog's "retitles the invariant"** — strictly it retitles
  the *decision* that states the invariant. Clear in context.
- **A `## Limitations` entry in `docs/ghmem-design.md`** for the
  orphaned doc. It would fit that section's stated purpose, but Item
  2.3 does not authorize it and Item 2.2 places the cost in the
  decisions entry. Flagged, not done.
- Everything under scope OUT: no Python, test, runbook or outline file
  was read for correction, only as ground truth.

## Gate

`just precommit` from the repository root, after all four fixes:

```
✓ Cached (inputs unchanged)
```

Expected — `docs/` is outside `just green`'s cache inputs, which are
the interpreter and tool versions plus every file under `justfile`,
`pyproject.toml`, `uv.lock`, `scripts/`, `src/`, `tests/` and
`hooks/`. Phase 1's full run is recorded in
`plans/reroute-empty-topic/reports/phase-2-commands.md`.

Nothing committed. `git status --short` lists only the three files I
edited.

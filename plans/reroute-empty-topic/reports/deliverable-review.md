# Deliverable Review: reroute-empty-topic

**Date:** 2026-09-07
**Methodology:** `docs/design.md` §6.8 "Deliverable review"
**Range:** `c7e842a..b39182e` (excludes the `plans/routing-test-helpers/`
precondition commits `556c51f`..`c7e842a`, which are a separate job)
**Layer 1:** skipped — 397 insertions against the 500-line gate. Layer 2
(full per-file plus cross-cutting) ran in the main session.

## Inventory

| Type | File | +/− |
|---|---|---|
| Code | `tools/ghmem/src/ghmem/routing_reduce.py` | +57 −32 |
| Code | `tools/ghmem/src/ghmem/ledger.py` | +3 −1 |
| Test | `tools/ghmem/tests/test_routing_live.py` | +179 −16 |
| Test | `tools/ghmem/tests/test_routing_replay.py` | +52 −33 |
| Human docs | `docs/ghmem-decisions.md` | +47 −12 |
| Human docs | `docs/ghmem-changelog.md` | +40 −0 |
| Human docs | `docs/ghmem-rejected.md` | +13 −0 |
| Human docs | `docs/ghmem-design.md` | +5 −18 |
| Reference | `memory` (submodule pointer) | +1 −1 |

**Design conformance.** Every IN-scope item of `outline.md` §Boundaries and
every runbook item 1.1 and 2.1–2.6 is present and matches its written
prescription, with one deliberate post-hoc deviation (Major 2). Nothing OUT of
scope was touched: `docs/ghmem-harness.md`, `Snapshot` handling, the ledger
schema and the orphan sweep are all untouched, and no filesystem delete entered
the reducer.

**Independent verification run for this review:**

- `just ghmem green` → `✓ Cached (inputs unchanged)`; the sentinel post-dates
  `f2c68c7`, so the gate covers the final tree, not just Phase 1.
- `pytest -q` → 282 passed. Arithmetic checks out against the probe baseline:
  278 (273 green + 5 red) + 5 new − 1 deleted = 282.
- NFR9 line caps: `routing_reduce.py` 362, `test_routing_live.py` 331,
  `test_routing_replay.py` 347 — all under 400.
- Item 2.6's mirror query re-run against `corpus/cache.sqlite`: 0 orphan
  `Topic` rows, 95 routes, 71 topics, 71 distinct non-`no-signal` verdicts,
  57 singletons. The changelog's figures are accurate, not inherited from the
  outline.
- Stale-citation sweep for the retitled decision: every remaining hit for
  **Unrouting a topic's last item deletes the `Topic` row** is under `plans/`
  (frozen write-time records) or `test_unroute.py:95`, which the runbook
  explicitly left. No live cross-reference dangles.

## Critical Findings

None.

## Major Findings

### 1. The upsert leaves the identity map stale; only weak-map GC hides it

`tools/ghmem/src/ghmem/routing_reduce.py:307` — `_mark_topic_dirty` now writes
`Topic` through a Core `session.execute()`, while `_apply_close_event`
(`:185-189`) mutates the same rows through the ORM identity map.
`rebuild_from_ledgers` runs both inside **one** `Session` with a single commit
at the end (`ingest.py:65-70`), so the two views of a `Topic` row must agree
for the whole replay.

They do not. `INSERT ... ON CONFLICT DO UPDATE` issued through
`session.execute()` does **not** refresh an identity-mapped `Topic`: the row
becomes `dirty=True` in the database while the in-memory instance still reads
`dirty=False` and counts as persistent-clean, so a later `topic.dirty = False`
is a no-op change and emits no `UPDATE`.

What saves it today is unrelated to the upsert: SQLAlchemy's identity map is
**weak**, `_apply_close_event` drops its reference on return, the instance is
collected, and the next `session.get` re-emits a `SELECT` that reads the true
row. Correctness rests on garbage collection, not on the write path.

Measured, not deduced — two probes over a
`route(I_1,T) → close(T) → route(I_2,T) → close(T)` ledger replayed through
`rebuild_from_ledgers`:

| Run | Final `softwrap.dirty` |
|---|---|
| as shipped | `False` — correct |
| identical, with a `load` listener pinning every `Topic` instance | `True` — **the close silently fails to clear** |
| `.returning(Topic).execution_options(populate_existing=True)`, pinned | `False` — correct |

The three-event prefix yields `dirty=True`, confirming the sequence is really
exercised rather than short-circuiting earlier.

No test covers `route → close → route → close` on one topic in a single replay,
and neither the runbook's slice list nor `phase-1-corrector.md` §2's coverage
matrix has a row for it. The four routing/`unroute`/`reenter`/`requeue` suites
contain no replay-level `close` at all (`close` appears only in
`test_cli_close.py` and `test_topic.py`).

**Failure scenario:** anything that holds a `Topic` instance alive across two
routing events inside one replay — a future `_apply_close_event` that batches,
a caller keeping the object, a test helper, or a strong-map configuration —
makes a `close` after a re-dirty silently fail to clear `dirty`. `status` then
re-dispatches a synthesis worker at an already-synthesized topic on every run,
forever. Nothing in the suite reds. The sequence is reachable in the live
corpus now: `clipboard-behavior`, `autocompletion` and `debian-packaging` each
carry a `route` after their last `close`, one `close` event short of it.

**Why not go back to the ORM.** Every ORM form of this write is a
read-then-write — `session.get` + `add`/assign, or `session.merge`, both of
which emit a `SELECT` first — and that is exactly the cross-process mint race
the upsert exists to close, since the `sqlite3` driver opens a transaction
before a write but not before a read. SQLAlchemy exposes no dialect-agnostic
ORM upsert. The form that keeps the single race-free statement *and*
synchronizes the identity map is the ORM-enabled upsert with `RETURNING`:

```python
stmt = (
    sqlite_insert(Topic)
    .values(name=name, dirty=True)
    .on_conflict_do_update(index_elements=["name"], set_={"dirty": True})
    .returning(Topic)
    .execution_options(populate_existing=True)
)
session.scalars(stmt).one()
```

**Applied.** `_mark_topic_dirty` now ends with those two clauses and
`session.scalars(stmt).one()`, with the docstring stating the identity-map
reason as the load-bearing one and demoting the autoflush reason to the guard
the evidence says it is. The regression guard landed with it:
`test_routing_replay.py::test_a_reminted_topic_clears_on_a_later_close`
replays `route(I_1,T) → close(T) → route(I_2,T) → close(T)` through
`rebuild_from_ledgers` with every loaded `Topic` pinned by a new
`routing_helpers._pinned_topics` context manager, and asserts the topic ends
clean.

Both halves mutation-run rather than argued:

| Mutation | Result |
|---|---|
| drop `.returning(Topic).execution_options(populate_existing=True)` | **reds this test alone** — 39 others green across the routing, `unroute`, `reenter` and `requeue` suites |
| also drop the pinning | **green under the defect** — confirming `_pinned_topics` is load-bearing, not scenery |

`docs/ghmem-decisions.md`'s **`_mark_topic_dirty` is a conditional upsert.**
entry gains a paragraph for the `RETURNING`/`populate_existing` half, so the
reasoning is not left to a docstring: a Core upsert writes past the ORM, the
close that follows a re-dirty emits no `UPDATE`, and the only reason to accept
that exposure is that SQLAlchemy offers no dialect-agnostic ORM upsert.
`docs/ghmem-changelog.md` is deliberately untouched — its 2026-09-07 entry
narrates the upsert's race reasoning, which remains true, and points at the
decisions doc for the rest.

Gate after the change: `✓ ruff check · docformatter -c · mypy · 283 passed ·
47 bats · line limits`.

### 2. The three retained-test decisions are recorded only in a commit subject

`f2c68c7` ("settle the three retained-test decisions from Phase 1") deletes
`test_routing_replay.py::test_route_creates_topic_marked_dirty` outright and
rewrites two test docstrings. The commit has no body, and no report under
`plans/reroute-empty-topic/reports/` records the decisions — the newest report
is `tdd-audit.md` at 16:43, the commit lands at 16:52.

That matters because `outline.md` §"Settled decision" and `runbook.md`
Item 1.1 slice 1 both prescribe **retargeting** that test to `"wrapping"` and
argue at length for keeping it, and `docs/ghmem-changelog.md` names
`plans/reroute-empty-topic/runbook.md` as this change's frozen record.

**Failure scenario:** a reader follows the changelog to the runbook, finds a
named deliverable that does not exist in the tree, and has no trail to the
decision that removed it. The justification does exist — `phase-1-corrector.md`
§2 finds the test fully subsumed by
`test_reroute_replaces_the_verdict_and_drops_the_emptied_topic` and explicitly
declines to delete it as a scope call — but it is a *finding*, not the
resolution, and the audit (`tdd-audit.md:147-152`) records all three as left
open.

**Applied.** Each of the three bullets in `runbook.md` Item 1.1 now carries a
`**Settled after the TDD audit:**` paragraph naming the outcome and pointing at
the report that argued it, following the two `**Revised at execution:**`
markers already in the file. Slice 4's also carries a
`**Reopened by the deliverable review**` paragraph for Major 1's change to the
upsert, since that item is where the call form was prescribed.

## Minor Findings

**Docstring accuracy**

1. **Fixed during this review.** `tools/ghmem/src/ghmem/ledger.py:140-148` —
   `NO_SIGNAL`'s docstring retained "it marks an item as triaged without
   creating or dirtying any `Topic` row" and added only the *drop* case.
   Routing an item to `NO_SIGNAL` away from a topic that keeps a sibling
   reaches `_release_topic`'s `remaining` branch and **does** dirty that row,
   so the retained clause was false on that path. Rewritten to say the verdict
   creates no `Topic` of its own, and that routing to it dirties the topic the
   item leaves if another route still names it and drops it otherwise. Gate
   re-run after the edit: `✓ ruff check · docformatter -c · mypy · 282 passed ·
   47 bats · line limits`. Uncommitted.
2. `tools/ghmem/src/ghmem/routing_reduce.py:292-305` — `_mark_topic_dirty`'s
   second paragraph gives `session.execute()`'s autoflush as the reason for the
   call form ("so its autoflush emits a pending `Topic` `DELETE` ahead of the
   insert"), and the fourth then withdraws it ("A guard rather than the thing
   holding this path up"). Both are true and the corrector's Fix 1 deliberately
   put them in that order, but a reader meets the justification before the
   caveat that retracts it. Leading with "a guard" would read straight.

**Runbook drift (frozen record, no tree change implied)**

3. `runbook.md` Item 2.3 says the `## Open questions` heading keeps "both
   remaining bullets"; there are four (NFR10, the constant families, the
   `topic merge` event, the no-signal rate). The executor dropped exactly the
   one bullet specified — the count was stale when the runbook was written.
4. `runbook.md` Item 2.3 cites `tools/ghmem/tests/test_unroute.py:136` for the
   old-wording docstring it leaves alone; it is at `:95`.

**Coverage**

5. `test_route_to_no_signal_drops_the_emptied_topic`'s second assertion
   (`session.get(Topic, NO_SIGNAL) is None`) passes under essentially every
   implementation in the plausible space — acknowledged in
   `phase-1-corrector.md` as a join-side guard, and the first assertion carries
   the discrimination, so this is noted rather than objected to. The path that
   *is* uncovered is no-signal-with-a-surviving-sibling, which the corrector
   argued should stay uncovered (observationally identical to the old code) —
   sound, but it is precisely the path Minor 1's docstring got wrong.

## Gap Analysis

| Design requirement | Status | Reference |
|---|---|---|
| `_apply_route_event` captures `left` pre-merge | Covered | `routing_reduce.py:127` |
| `_release_topic` called post-merge, post join-side dirty | Covered | `routing_reduce.py:139-141` |
| Skip release when `left` is `None` or `NO_SIGNAL` | Covered | `routing_reduce.py:139` |
| `_release_topic` docstring: drop the `unroute` scoping, keep replay-destructiveness, soften the doc clause | Covered | `routing_reduce.py:237-249` |
| `_release_topic`: autoflush comment reworded, concurrency argument added | Covered | `routing_reduce.py:259-271` |
| `_mark_topic_dirty` becomes a `sqlite_insert` upsert via `session.execute()` | Covered as specified; the call form has a consequence the spec did not consider | `routing_reduce.py:307-312`, Major 1 |
| `ledger.py` `NO_SIGNAL` docstring clause | Covered; was inaccurate, fixed in this review | Minor 1 |
| FR4/FR6 end-to-end test (`status` dirty set stays assemblable) | Covered | `test_routing_live.py:177` |
| `route → no-signal` drops the emptied topic | Covered | `test_routing_live.py:310` |
| Replay: re-route drops the emptied topic, renamed | Covered | `test_routing_replay.py:55` |
| Live: re-route of last item drops the row, replacing the old test | Covered | `test_routing_live.py:152` |
| Retarget `test_route_creates_topic_marked_dirty` to `"wrapping"` | **Deviated** — retargeted in Phase 1, deleted in `f2c68c7` | Major 2 |
| Shrink both hardcoded topic sets to `{"wrapping"}`, no added `is None` | Covered | `test_routing_replay.py:301`, `test_routing_live.py:148` |
| Surviving-sibling guard + mutation run | Covered, mutation recorded in docstring | `test_routing_live.py:211` |
| Same-topic re-route guard + mutation run | Covered; mutation reported inert, honestly | `test_routing_live.py:252` |
| Emptied-and-re-minted-in-one-replay guard + mutation run | Covered; mutation reported inert, honestly | `test_routing_replay.py:305` |
| Item 2.1 — `just precommit` over the whole tree (NFR8) | Covered; gate re-verified against the final tree | this review |
| Item 2.2 — retitle the decision, generalize, record the cost | Covered | `ghmem-decisions.md:120-139` |
| Item 2.2 — backfill `reserve_dispatch`'s decision, placed **before** the upsert entry | Covered | `ghmem-decisions.md:141-156` |
| Item 2.2 — separate upsert decision entry | Covered | `ghmem-decisions.md:158-169` |
| Item 2.3 — drop the first Open question in full | Covered | `ghmem-design.md:247` (bullet removed above it) |
| Item 2.3 — FR7 topic-lifecycle clause + repointed Rationale | Covered | `ghmem-design.md:89-92` |
| Item 2.4 — rejected entry, placed after the `record_verdict` entry | Covered | `ghmem-rejected.md:64-73` |
| Item 2.5 — dated changelog entry closing with the runbook path | Covered; corpus figures re-verified | `ghmem-changelog.md:116-155` |
| Item 2.6 — mirror invariant query | Covered; 0 rows, re-run for this review | this review |
| OUT: `docs/ghmem-harness.md`, `Snapshot`, ledger schema, orphan sweep, filesystem delete | Respected | — |

No unspecified deliverables. The `memory` submodule bump
(`sqlalchemy-uow-hides-mutations.md`) records the two inert mutations and is
excess in the acceptable sense.

## Summary

**0 critical · 2 major · 5 minor.**

The change conforms to the outline and runbook essentially line by line,
including the parts that are easy to skip: the pre-merge/post-merge ordering
with its stated reason, the softened `docs/design/<name>.md` clause made
identically in the docstring and the decision entry, the `reserve_dispatch`
backfill placed before the entry that cites it, and the corpus reading taken at
write time rather than copied. Both mutations that turned out not to
discriminate are recorded as inert in the test docstrings rather than
engineered into a red — the honest outcome, and the one the runbook asked for.

Neither Major finding was a defect visible today, and neither is a conformance
gap — both sat just outside what the plan set out to check. Major 1 was a
consequence of the call form the runbook prescribed: the upsert is race-free as
intended, but it desynchronized the ORM identity map, and the replay stayed
correct only because that map is weak. Major 2 was a hole in the record: the
decision that removed a prescribed test was traceable only through a commit
subject, from a runbook the changelog names as the frozen record.

**All three findings acted on** — Major 1 fixed with a mutation-run regression
guard, Major 2 recorded in the runbook, Minor 1's docstring corrected. Minors
2–5 are left as read: 2 is a paragraph order the corrector chose deliberately,
3 and 4 are stale counts in a frozen record, 5 is a coverage judgement the
corrector argued and I agree with.

Everything above is **uncommitted**: `ledger.py`, `routing_reduce.py`,
`routing_helpers.py`, `test_routing_replay.py`, `runbook.md` and this report.

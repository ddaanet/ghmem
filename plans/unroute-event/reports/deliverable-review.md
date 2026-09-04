# Deliverable Review: unroute event

**Date:** 2026-09-03
**Range:** `cba5073`..`17ad255`
**Baseline:** `plans/unroute-event/requirements.md` (FR-1..FR-6,
NFR-1..NFR-3, C-1..C-6, Q-1..Q-4)
**Methodology:** `edify:deliverable-review` — Layer 1 delegated
(`deliverable-review-code.md`), Layer 2 cross-cutting in the main
session. Every promoted finding was re-verified here against the code.

## Inventory

| type | file | lines | verdict |
| --- | --- | --- | --- |
| code | `src/ghmem/routing_reduce.py` | 328 (new) | Sound |
| code | `src/ghmem/ingest.py` | 111 (−234) | Sound; stale prose |
| code | `src/ghmem/cli.py` | +37 | Sound; one error-message defect |
| code | `src/ghmem/ledger.py` | +22 | Sound |
| code | `src/ghmem/dispatch_store.py` | 1 | Sound |
| test | `tests/test_unroute.py` | 305 (new) | Sound; four clarity nits |
| test | `tests/test_cli_unroute.py` | 178 (new) | Sound; one loose assertion |
| test | `tests/ghmem-only.bats` | +13 | Sound — ALLOW/DENY correctly paired |
| prose | `plans/unroute-event/requirements.md` | 314 | Strongest document in either slice |
| prose | `plans/unroute-event/classification.md` | 91 | Clean |

**Design conformance.** Twelve of the thirteen baseline items are
covered; FR-6 is the exception and is an operational step, not code.
No Critical findings. **Eighteen mutations of the new branches and
their dependencies: 17 killed by pytest, 1 by mypy, 0 survivors.** No
vacuous test was found, and both vacuity shapes C-3 names by hand are
actively defended by fixtures that carry the discrimination — the
idempotence test asserts a row that must *vanish*, and the dirty-topic
test cleans the topic before asserting the transition.

## Critical Findings

None.

## Major Findings

**M1 — FR-6 has not been performed.** `corpus/routing.jsonl` holds 0
`unroute` events (103 `route`, 95 `dispatched`, 6 `snapshot`, 5
`close`); sessions `BcbBJeUg` (12) and `MZlH9vbE` (10) still hold the
22 routes intact. Neither acceptance criterion is met. The shipped
code enables the step and the commit message does not claim it, so
this is not a code defect — but it is a baseline requirement, and
`docs/ghmem-changelog.md:119–123` **states it as already done**, which
is the Critical in the sibling report. Correct the changelog or run
the batch.

**M2 — the `ingest.py` split left six stale inbound prose references
(NFR-2).** NFR-2's claim that the seam is clean holds for the imports:
`ghmem.ingest` exports only `rebuild_from_ledgers` and
`validate_raw_node`, `store.py`/`sync_core.py` reach exactly those,
`dispatch_store.py`/`ingest.py` import `apply_routing_event` from
`routing_reduce`, nothing is duplicated and no compat alias was left.
The prose did not follow:

- `triage.py:306` and `exceptions.py:147` name
  `ingest.apply_routing_event` — a dotted path that no longer
  resolves; a reader following it finds an 111-line module with no
  reducer.
- `exceptions.py:8` and `:16` — the module docstring's
  where-does-each-exception-belong map places
  `UnknownRoutingEventError` and `InvalidItemUpdatedAtError` under
  "ledger replay (`ingest.py`)". Both are raised in
  `routing_reduce.py` now. That docstring exists to be a routing
  table, so a wrong entry costs exactly what it was written to save.
- `exceptions.py:92` and `ledger.py:214` — "`ingest.py`'s parse
  guard"; the guard is `routing_reduce._parse_item_updated_at`.

**Do not sweep blind:** `models.py:9` and `:136` credit `ingest.py`
with raw-node parsing that has lived in `raw_nodes.py` since before
this change — pre-existing drift, out of this slice's scope.
`ledger.py:86`, `:99` and `status.py:3` still read correctly.

**M3 — Q-2's asymmetry is correctly implemented but leaves a reachable
failure the living design doc does not record.** The code matches the
decision exactly: `_release_topic` has one caller,
`_apply_unroute_event:233`; `_apply_route_event` and
`_apply_reenter_event` still call `_mark_topic_dirty` directly, so
`route` did not pick up the delete by accident, and the five existing
pins in `test_routing_replay.py` and `test_routing_live.py` are
untouched. The consequence is that the invariant
`docs/ghmem-design.md:504` states flatly — "A topic row exists exactly
while some route names it" — holds on the `unroute` path only.
**Failure scenario:** re-route the last item out of topic T → T stays
in `status`'s dirty set with zero routed items → a synthesis worker
dispatched from that dirty list hits `TopicNotRoutedError`
(`topic.py:53`), which is precisely the breakage FR-4 exists to
prevent. `requirements.md` Q-2 is explicit that this is a live open
question; the design doc's condensed version is not. Recorded in full
in the sibling report (its M1/M2). No corpus route has ever emptied a
topic (0 empty `Topic` rows across 95 routes), so this is latent, not
active.

## Minor Findings

**Error signaling**
- `cli.py:123` — `click.echo(f"ghmem unroute: {exc}")` prints
  **`ghmem unroute: 99`**, the bare `str(LookupError(99))`, verified
  by running the command. `IssueOrPRNotFoundError` defines no
  `__init__`. Three screens away, `show` (`cli.py:207`) and
  `discussion` (`:221`) print `ghmem show: no issue or pull request
  numbered #99`. Match them — which needs the failing number, so catch
  inside the comprehension or re-raise with it.
- `cli.py:128` — `Unrouted {len(item_ids)} item(s)` counts numbers
  *named*, not verdicts *withdrawn*, so a repeat run reports the same
  count as the first. Deliberate, pinned by
  `test_cli_unroute.py:173`, and FR-2 asks only for "the count", so it
  conforms. Noted because an operator re-running a batch gets no
  signal that nothing changed.

**Tests**
- `test_unroute.py:253` — `UNROUTE_NEVER_ROUTED` is used in
  `test_live_unroute_matches_a_cold_rebuild` where `I_3` *is* routed
  (to `"wrapping"` at `:248`) and its unroute is the one that empties
  the topic. The constant name tells a reader the opposite of what the
  line does.
- `test_unroute.py:271–273` — the FR-5 non-emptiness guard asserts
  `live[0]` (Route) and `live[2]` (Topic) but not `live[1]`
  (Dispatch), leaving the Dispatch third of the comparison the one
  shape that could go empty-on-both-sides unnoticed. It is non-empty
  in fact; add `assert live[1]`.
- `test_unroute.py:256–269` — the three `select(...)` calls carry no
  `order_by`, so live-vs-rebuilt equality leans on SQLite's rowid scan
  order agreeing across two different insert/delete histories. True
  today; a future fixture inserting after a delete could reuse a freed
  rowid and produce a false red. The sibling `store.routes()` orders
  by `item_id`.
- `test_unroute.py:205` — `assert session.get(Route, GraphQLId("I_3"))
  is None` is vacuous on its own: `I_3` was never routed, so it passes
  with the whole `unroute` branch deleted. The test's real content is
  the no-raise plus the rebuild at `:206`, and M7/M8 confirm those
  discriminate. Worth a comment or dropping the line.
- `test_cli_unroute.py:134` — `assert "99" in output` passes on any
  message containing those digits. Tighten to the full line once the
  message above is fixed.

**Documentation**
- `routing_reduce.py:236–257` — `_release_topic`'s docstring explains
  at length why `docs/design/<name>.md` is left alone but says nothing
  about `Snapshot` rows, which also survive a dropped `Topic`. The
  behaviour is safe (`close_topic`, `dispatch_store.py:323`,
  recomputes the digest, finds no match and returns `cleared=False`
  without writing) but is reasoned from the code, not covered by a
  test.

## Gap Analysis

| requirement | status | evidence |
| --- | --- | --- |
| FR-1 event deletes `Route` + `Dispatch` | covered | `routing_reduce.py:204–233`; both deletes killed by M1/M2. All three freshness gates re-read and each **passes on the absence**, no comparison relaxed: `heat_query.py:66–77` `~exists(...)`, `stale_routes` joins Route, `record_verdict:330` guards `is not None`. Idempotent on both rows. |
| FR-2 `ghmem unroute` command | covered | `cli.py:94–128`; numbers resolved by the same lookup as `show`/`triage`/`route`. Ordering verified, not just presence — the comprehension at `:119–121` completes before the append loop at `:125`, and M9 (lazy interleaved resolve) is killed by a test asserting `routing.jsonl` byte-identical. |
| FR-3 vacated topic dirtied, or dropped if empty | covered | `_release_topic`; M3, M4, M11, M12 all killed. |
| FR-4 emptying a topic must not break synthesis | covered | `test_status_dirty_set_stays_assemblable_after_emptying_a_topic` runs the real `build_status` → `assemble` loop; an emptied name in either tuple fails it. |
| FR-5 replay and live write agree | covered | `test_live_unroute_matches_a_cold_rebuild`, with an explicit non-emptiness guard on two of three shapes (Minor above). |
| FR-6 the 22-item batch is re-triaged | **missing** | 0 `unroute` events; 22 routes intact. M1. |
| NFR-1 ledger append-only | covered | `record_routing_event` only appends; a test asserts the pre-existing `route` lines survive. No delete or rewrite path in the diff. |
| NFR-2 every source file < 400 lines | covered | `check_line_limits.sh` exit 0; `routing_reduce.py` 328, `ingest.py` 111, max in tree 388. Imports clean, no alias, no duplicated symbol — prose stale (M2). |
| NFR-3 `just precommit` passes | covered (ghmem leg) | sentinel deleted, cold `just green`: `✓ ruff check · docformatter -c · mypy · 278 passed · 47 bats · line limits`. Repo-root `precommit` also carries the memory gate, not re-run here. |
| C-1 tag in the `RoutingEvent` union | covered — **and the gate does see it** | M18 removed `UnrouteEvent` from the union: pytest survived (278 passed) exactly as C-1 predicts, but **mypy killed it**, because `cli.py:126` annotates the literal `event: RoutingEvent`. C-1's premise that `just green` cannot see the gap is now false for this call shape. |
| C-2 registered in `_ROUTING_BY_TYPE` | covered | `routing_reduce.py:321`; M7 → 13 failures. |
| C-3 red = failed assertion; two named vacuity shapes | covered | Both shapes defended: the idempotence test asserts the surviving route list is `[I_2]` (a row that must vanish), and the dirty test sets `dirty = False` and commits before asserting the transition. The red-phase history is not recoverable from the artifact; the mutation sweep is the post-hoc equivalent. |
| C-4 mutation-test the reducer branch | covered | 18 mutants, 0 survivors. |
| C-5 containment hook re-verified against a real command | covered | `ghmem-only.bats:33–43` pairs `ghmem unroute 1445 3795 3783` → ALLOW with `ghmem unroute 1445; rm -rf /` → DENY(2) plus non-empty stderr, so a guard broken closed cannot pass. 39/39 ok. |
| C-6 docstring summaries ≤ 80 chars | covered | AST sweep over the five touched files; `docformatter -c` clean. |
| Out of scope respected | covered | No `topic merge`, no packet delta, no rubric change, `record_verdict`'s freshness check untouched, no ledger rewriting. |
| Q-1..Q-4 | all verified | Q-1: `nargs=-1, required=True`, and the hook admits either form (verified by running it). Q-2: one caller, re-route pins untouched. Q-3: `not_dispatched` guard at `triage.py:327–329`; the hook already permits `requeue`/`rebuild`. Q-4: the `Route` delete precedes any verdict inspection; only the release step is `NO_SIGNAL`-guarded, and M5 is killed by a test that leaves a second `NO_SIGNAL` route standing so the mutant mints a `"no-signal"` Topic row rather than silently no-op-ing. |

## Summary

**0 Critical · 3 Major · 8 Minor.**

The code slice is in good shape. The reducer split lands on the seam
`ingest.py`'s own docstring drew, every source file is under the cap,
the three freshness gates were re-read rather than taken on the
requirements doc's word, and the ordering claims in FR-2 were checked
as ordering rather than as presence. The test suite survives eighteen
mutations including four the requirements document itself predicted
would slip through — the two named vacuity shapes are defended by
fixtures that carry the discrimination, which is the thing a finished
artifact usually cannot demonstrate.

One baseline claim turns out to be false in the project's favour: C-1
asserts the gate cannot see a missing union member, and mypy catches
it, because the CLI annotates its event literal. Worth correcting in
the requirements doc so the next event kind is not written under a
belief in a gap that has closed for this call shape.

The three Majors are all repair-in-place: perform FR-6 (or stop the
changelog claiming it), sweep the six stale `ingest.py` prose
references, and record the surviving `route`-path empty-topic hole
where a reader of the living design doc will meet it.

Layer 1 detail: `deliverable-review-code.md`.

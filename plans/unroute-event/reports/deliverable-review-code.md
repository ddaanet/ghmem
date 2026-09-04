# Deliverable Review (Layer 1, code+test): unroute event

Reviewed at commit `17ad255` against `plans/unroute-event/requirements.md`
(FR-1..FR-6, NFR-1..NFR-3, C-1..C-6, Q-1..Q-4) and
`plans/unroute-event/classification.md`.

Headline: the reducer, the CLI and the tests conform. Eighteen mutations
were run against the new branches and their dependencies; **seventeen were
killed**, the eighteenth by mypy rather than pytest. No vacuous test was
found among the fifteen new ones — every named C-3 risk is defended by a
test that fails when the line it names is removed. The two findings that
matter are both outside the reducer: FR-6 has not been performed, and the
`ingest.py` split left five inbound docstring references pointing at
symbols that moved.

## Inventory

| file | lines | type | verdict |
| --- | --- | --- | --- |
| `tools/ghmem/src/ghmem/routing_reduce.py` | 328 | code (new) | conforms |
| `tools/ghmem/src/ghmem/ingest.py` | 111 | code (reduced) | conforms; inbound refs stale |
| `tools/ghmem/src/ghmem/cli.py` | 350 | code | conforms; weak error message |
| `tools/ghmem/src/ghmem/ledger.py` | 330 | code | conforms (C-1 satisfied) |
| `tools/ghmem/src/ghmem/dispatch_store.py` | 355 | code | conforms (import repoint only) |
| `tools/ghmem/tests/test_unroute.py` | 305 | test (new, 9 cases) | conforms; 3 minors |
| `tools/ghmem/tests/test_cli_unroute.py` | 178 | test (new, 6 cases) | conforms; 1 minor |
| `tools/ghmem/tests/ghmem-only.bats` | 257 | test (+2 cases) | conforms (C-5 satisfied) |

## Critical

None.

## Major

**1. FR-6 has not been performed — `corpus/routing.jsonl` holds zero
`unroute` events.**

`corpus/routing.jsonl` (repo root): 103 `route`, 95 `dispatched`, 6
`snapshot`, 5 `close`, **0 `unroute`**. Session `BcbBJeUg` still holds 12
routes and `MZlH9vbE` 10 — exactly the 22 items FR-6 names, all still
carrying the verdicts this event exists to withdraw. Neither acceptance
criterion ("all 22 carry a `Route` written by a session other than those
two"; "the corpus's singleton-topic count falls") is met.

This is an operational step the shipped code enables rather than a defect
in the code, and the commit message does not claim it. It is reported here
because FR-6 is in the conformance baseline; whether the deliverable is
"done" with it outstanding is the cross-project layer's call.

**2. The `ingest.py` split left five inbound references naming a module
that no longer holds what they point at (NFR-2).**

NFR-2 asserts the seam is clean and no compatibility alias was left behind.
Both hold for the *imports* — `ghmem.ingest` exports only
`rebuild_from_ledgers` and `validate_raw_node`, `store.py`/`sync_core.py`
reach exactly those, `dispatch_store.py`/`ingest.py` import
`apply_routing_event` from `routing_reduce`, and nothing is duplicated. The
prose did not follow:

- `src/ghmem/triage.py:306` — "`ingest.apply_routing_event` already
  supports a live re-route". `ghmem.ingest.apply_routing_event` does not
  exist; a reader following it finds an 111-line module with no reducer.
- `src/ghmem/exceptions.py:147` — same dead dotted path, in
  `TriageSessionError`'s docstring.
- `src/ghmem/exceptions.py:8` and `:16` — the module docstring's
  where-does-each-exception-belong map places `UnknownRoutingEventError`
  and `InvalidItemUpdatedAtError` in "ledger replay (`ingest.py`)". Both
  are now raised in `routing_reduce.py` (`:111` and `:173`/`:175`). This
  docstring exists to be a routing table, so a wrong entry costs exactly
  what it was written to save.
- `src/ghmem/exceptions.py:92` — "`ingest.py`'s parse guard raises this";
  the guard is `routing_reduce._parse_item_updated_at`.
- `src/ghmem/ledger.py:214` — "`ingest.py`'s parse guard rejects a naive
  or unparseable value"; same guard.

Two further hits are **pre-existing drift, not caused by this commit**, and
should not be swept in blind: `models.py:9` and `models.py:136` credit
`ingest.py` with raw-node parsing that has lived in `raw_nodes.py` since
before this change. `status.py:3` ("the routing tables `ingest.py` replays
into") still reads correctly — `ingest.rebuild_from_ledgers` is the replay
driver.

## Minor

**Tests — clarity and specificity**

- `tests/test_unroute.py:253` — `UNROUTE_NEVER_ROUTED` is used in
  `test_live_unroute_matches_a_cold_rebuild`, where `I_3` *is* routed (to
  `"wrapping"`, at `:248`) and its unroute is the one that empties a topic.
  The name tells a reader the opposite of what the line does. Either a
  second constant or a local literal.
- `tests/test_unroute.py:271-273` — the FR-5 non-emptiness guard asserts
  `live[0]` (Route) and `live[2]` (Topic) but not `live[1]` (Dispatch), so
  the Dispatch third of the comparison is the one shape that could go
  all-empty-both-sides unnoticed. It is non-empty in fact (`I_2`'s row
  survives); add `assert live[1]`.
- `tests/test_unroute.py:256-269` — `select(Route)` / `select(Dispatch)` /
  `select(Topic)` carry no `order_by`, so the live-vs-rebuilt equality
  leans on SQLite's rowid scan order agreeing across two different
  insert/delete histories. It does today, and the sibling `store.routes()`
  orders by `item_id`; a future fixture that inserts after a delete could
  reuse a freed rowid and produce a false red. Add `.order_by(...)`.
- `tests/test_unroute.py:205` — `assert session.get(Route, GraphQLId("I_3"))
  is None` is vacuous: `I_3` was never routed, so it passes with the whole
  `unroute` branch deleted. The test's real content is "recording raises
  nothing" plus the rebuild at `:206`, and mutations M7/M8 confirm those do
  discriminate. Worth a comment saying so, or dropping the line.
- `tests/test_cli_unroute.py:134` — `assert "99" in output` matches a bare
  substring; it passes on any message containing those digits. Assert the
  full line once the message is fixed (below).

**Code — error signaling**

- `src/ghmem/cli.py:123` — `click.echo(f"ghmem unroute: {exc}", err=True)`
  prints **`ghmem unroute: 99`**, verified by running the command.
  `IssueOrPRNotFoundError` has no `__init__`, so `str(exc)` is the bare
  number. Three screens away in the same file, `show` (`cli.py:207`) and
  `discussion` (`:221`) print `ghmem show: no issue or pull request
  numbered #99`. Match them: `f"ghmem unroute: no issue or pull request
  numbered #{number}"` — which needs the failing number, so catch inside
  the comprehension or re-raise with it.

**Code — reporting**

- `src/ghmem/cli.py:128` — `Unrouted {len(item_ids)} item(s)` counts
  numbers *named*, not verdicts *withdrawn*: a repeat run over an
  already-unrouted batch reports the same count as the first. This is
  deliberate and pinned (`test_cli_unroute.py:173` asserts exactly it), and
  FR-2 says only "prints the count", so it conforms. Noted because an
  operator re-running a batch gets no signal that nothing changed.

**Code — documentation gap**

- `src/ghmem/routing_reduce.py:236-257` — `_release_topic`'s docstring
  explains at length why `docs/design/<name>.md` is left alone but says
  nothing about `Snapshot` rows, which also survive a dropped `Topic`. The
  behaviour is safe: `close_topic` (`dispatch_store.py:323`) recomputes the
  live digest, finds it does not match the stored one, and returns
  `cleared=False` without writing. Reasoned from the code; no test covers a
  close against a topic dropped mid-synthesis.

## Requirement coverage

| requirement | status | evidence |
| --- | --- | --- |
| FR-1 event deletes `Route` + `Dispatch` | covered | `routing_reduce.py:204-233`; `UnrouteEvent` in the union (`ledger.py:328`). Both deletes killed by M1/M2. Gates re-read: `heat_query.py:66-77` is `~exists(Route.item_id == item.id AND Route.item_updated_at >= item.updated_at)` — no Route row ⇒ EXISTS false ⇒ item returned, i.e. it passes on the absence; `dispatch_store.stale_routes:203-212` is `select(Route).join(...)` — no row, no candidate; `triage.record_verdict:330-332` guards `existing_route is not None and …`. **No comparison was relaxed.** Idempotence: `route is None → return`, `dispatch is not None` guard. |
| FR-2 `ghmem unroute` command | covered | `cli.py:94-128`. Numbers via `store.get_issue_or_pr` (same lookup as `show`/`triage`/`route`). Ordering verified, not just presence: the whole comprehension at `:119-121` completes before the append loop at `:125`, and M9 (resolve lazily, interleaved) is killed by `test_unroute_rejects_an_unknown_number_before_appending`, which asserts `routing.jsonl` byte-identical. Double-run: `test_unroute_twice_is_idempotent_and_rebuilds_the_same`. |
| FR-3 vacated topic dirtied, or dropped if empty | covered | `_release_topic` (`routing_reduce.py:236-269`). M3 (never drop), M4 (always drop), M11 (dirty without the emptiness test), M12 (never dirty) all killed. |
| FR-4 emptying a topic must not break synthesis | covered | `test_status_dirty_set_stays_assemblable_after_emptying_a_topic` runs the real `build_status` → `assemble` loop and asserts `dirty == ("wrapping",)` and `topics == ("wrapping",)` — an emptied name in either tuple fails it. Killed M3, M6, M11, M13. |
| FR-5 replay and live write agree | covered | `test_live_unroute_matches_a_cold_rebuild`; non-emptiness asserted for Route and Topic (see Minor for Dispatch). Killed by M1 and M4. |
| FR-6 the 22-item batch is re-triaged | **missing** | 0 `unroute` events in `corpus/routing.jsonl`; `BcbBJeUg` 12 + `MZlH9vbE` 10 routes intact. Major 1. |
| NFR-1 ledger stays append-only | covered | `record_routing_event` only appends (`dispatch_store.py:60-63`); `test_unroute_appends_one_event_per_item` asserts the two pre-existing `route` lines survive. No delete/rewrite path anywhere in the diff. |
| NFR-2 every source file under 400 lines | covered | `bash scripts/check_line_limits.sh` → exit 0. `routing_reduce.py` 328, `ingest.py` 111, `cli.py` 350, `ledger.py` 330, `dispatch_store.py` 355. Seam clean for imports and free of aliases and duplicated symbols; prose refs stale — Major 2. |
| NFR-3 `just precommit` passes | covered (ghmem leg) | Sentinel deleted and `just green` re-run from cold: `✓ ruff check · docformatter -c · mypy · 278 passed · 47 bats · line limits`. The repo-root `just precommit` also carries the memory gate, not run here. |
| C-1 tag in the `RoutingEvent` union | covered, **and the gate does see it** | `ledger.py:300-328`. M18 removed `UnrouteEvent` from the union: pytest **survived** (278 passed) exactly as C-1 predicts, but **mypy killed it** — `cli.py:126` annotates the literal `event: RoutingEvent`, so the omission surfaces as an assignment error. `test_unroute.py:48-50` annotating its constants `UnrouteEvent` reinforces this. |
| C-2 registered in `_ROUTING_BY_TYPE` | covered | `routing_reduce.py:321`. M7 removes it → 13 failures with `UnknownRoutingEventError`. |
| C-3 TDD red = failed assertion; two named vacuity shapes | covered | Both named shapes are defended. "Append twice, rebuild, same rows" would survive the `Route` delete being removed — but M1 kills `test_unroute_twice_is_idempotent_and_rebuilds_the_same` anyway, because it asserts the surviving route list is `[I_2]` (a row that must *vanish*), not just live==rebuilt. `topic.dirty is True` from birth state is defended: `test_unroute_dirties_a_topic_that_keeps_items:125-127` sets `dirty = False` and commits first, and M12 (release without dirtying) kills it. The red-phase history itself is not recoverable from the finished artifact; this is the post-hoc equivalent. |
| C-4 mutation-test the reducer branch | covered | 18 mutants run in this review (see log); 17 killed by pytest, 1 by mypy, 0 survivors. |
| C-5 containment hook re-verified against a real command | covered | `tests/ghmem-only.bats:33-43` pairs `ghmem unroute 1445 3795 3783` → ALLOW with `ghmem unroute 1445; rm -rf /` → DENY(2) plus non-empty stderr, so a guard broken closed cannot pass. `bats tests/ghmem-only.bats` → 39/39 ok. |
| C-6 docstring summaries ≤ 80 chars | covered | AST sweep over the five touched files: no first docstring line exceeds 80. `docformatter -c` clean inside `just green`. |

Out-of-scope list respected: no `topic merge` event, no packet-delta work,
no rubric change, `record_verdict`'s freshness check untouched
(`triage.py:331` unchanged in the diff), no ledger rewriting.

Q-2 asymmetry confirmed exactly as stated: `_release_topic` has **one**
caller, `_apply_unroute_event:233`. `_apply_route_event:130,141` and
`_apply_reenter_event:201` still call `_mark_topic_dirty` directly, so
`route` did not pick up the delete by accident; the re-route pins at
`test_routing_replay.py:87-108` and `test_routing_live.py:166-186` are
untouched by the diff and pass. Q-4 confirmed: the `Route` delete precedes
any verdict inspection (`:231` before `:232`), and only the release step is
guarded by `left != NO_SIGNAL`; M5 (drop the guard) is killed by
`test_unroute_of_a_no_signal_route_touches_no_topic`, which deliberately
leaves a second `NO_SIGNAL` route standing so the mutant mints a
`"no-signal"` Topic row rather than silently no-op-ing.

## Verification log

All commands from `/Users/david/code/micro/tools/ghmem` with
`PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH"`.

| command | result |
| --- | --- |
| `pytest tests/test_unroute.py tests/test_cli_unroute.py -q` | 15 passed |
| `pytest -q` (baseline) | 278 passed in 25.2 s |
| `bash scripts/check_line_limits.sh` | exit 0 |
| `bats tests/ghmem-only.bats` | 39/39 ok |
| `rm tmp/.green-sentinel && just green` | `✓ ruff check · docformatter -c · mypy · 278 passed · 47 bats · line limits` |
| AST sweep of docstring first lines, 5 files | none > 80 chars |
| `ghmem unroute 1 99` against a two-route tmp corpus | exit 1, stderr `ghmem unroute: 99`, stdout empty |
| `ghmem show 99`, same corpus | stderr `ghmem show: no issue or pull request numbered #99` |
| `python -c` over `corpus/routing.jsonl` | `route 103, dispatched 95, snapshot 6, close 5`; 0 unroute; `BcbBJeUg` 12, `MZlH9vbE` 10 |

**Mutation sweep.** Each mutant was written to the source file, the full
`pytest -q` suite run, and the file restored from an in-memory copy in a
`finally` block. `git status --porcelain tools/ghmem` was empty after every
batch and is empty now.

| # | mutation | verdict | first killers |
| --- | --- | --- | --- |
| M1 | drop `session.delete(route)` | KILLED | 8 failed, incl. `test_unroute_deletes_the_route_row`, `..._twice_is_idempotent_and_rebuilds_the_same` |
| M2 | drop the `Dispatch` delete from `_apply_unroute_event` | KILLED | 3 failed, incl. `test_unroute_deletes_the_dispatch_row`, `..._returns_to_the_untriaged_queue` |
| M3 | `if remaining:` → `if True:` (never drop the topic) | KILLED | `..._drops_the_topic_row`, `test_status_dirty_set_stays_assemblable...` |
| M4 | `if remaining:` → `if False:` (always drop the topic) | KILLED | `..._dirties_a_topic_that_keeps_items`, `test_live_unroute_matches_a_cold_rebuild` |
| M5 | drop the `left != NO_SIGNAL` guard | KILLED | `..._of_a_no_signal_route_touches_no_topic` |
| M6 | count *all* routes, not `Route.verdict == name` | KILLED | `test_status_dirty_set_stays_assemblable...` |
| M7 | remove `"unroute"` from `_ROUTING_BY_TYPE` | KILLED | 13 failed, `UnknownRoutingEventError` |
| M8 | remove the `"unroute"` branch from `apply_routing_event` | KILLED | 13 failed |
| M9 | CLI resolves lazily, interleaved with appends | KILLED | `..._rejects_an_unknown_number_before_appending` |
| M10 | CLI exits 0 on an unknown number | KILLED | same |
| M11 | `_mark_topic_dirty` instead of `_release_topic` | KILLED | `..._drops_the_topic_row`, `test_status_dirty_set...` |
| M12 | `_release_topic` returns without dirtying | KILLED | `..._dirties_a_topic_that_keeps_items` |
| M13 | `unroute` skips the topic-release step entirely | KILLED | 3 failed |
| M14 | early `return` before both deletes | skipped — pattern also matches `_apply_reenter_event` (subsumed by M1+M2) |
| M15 | `record_routing_event`'s Session with `autoflush=False` | KILLED | `..._drops_the_topic_row`, `test_live_unroute_matches_a_cold_rebuild` — so `_release_topic`'s documented autoflush dependency (`routing_reduce.py:258-260`) is pinned by tests, not merely commented |
| M16 | `rebuild_from_ledgers`'s Session with `autoflush=False` | KILLED | 20 failed, 12 errors |
| M17 | drop `required=True` from the `NUMBERS` argument (Q-1) | KILLED | `..._requires_at_least_one_number` |
| M18 | remove `UnrouteEvent` from the `RoutingEvent` union (C-1) | **SURVIVED pytest** (278 passed), KILLED by mypy | `cli.py:126: error: Incompatible types in assignment` |

Working tree left as found: `git status --porcelain tools/ghmem` is empty.
The only side effect is `tools/ghmem/tmp/.green-sentinel`, deleted to force
a cold gate run and rewritten by that run's pass (it is git-ignored).

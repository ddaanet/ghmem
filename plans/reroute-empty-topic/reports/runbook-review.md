# Runbook Review: reroute-empty-topic

**Artifact**: `plans/reroute-empty-topic/runbook.md`
**Design**: `plans/reroute-empty-topic/outline.md`
**Requirements**: `docs/ghmem-design.md` — FR4, FR6, FR7, NFR5, NFR8
**Date**: 2026-09-07T12:26:55+02:00
**Mode**: review + fix-all

## Summary

The runbook decomposes the outline faithfully, and its two deliberately settled
questions — test placement, and the honest naming of the non-discriminating
slices — both hold up under challenge; neither is flagged. Two structural
problems were not caught by the outline and are the substance of this review:
FR4 and FR6 were claimed as covered with no assertion anywhere exercising them,
and the five-slice structure prescribed red phases for slices that a single
atomic implementation had already greened. Both are fixed. Everything else is
line-reference drift, one misleading docstring the change falsifies, and one
unrunnable command.

**Overall Assessment**: Ready

## Requirements Coverage

| Requirement | Phase | Items | Coverage | Notes |
|---|---|---|---|---|
| FR4 — one living document per topic | 1 | 1.1 | Complete *(was Missing)* | No slice asserted it before this review; now pinned by slice 1's `test_status_dirty_set_stays_assemblable_after_a_reroute` |
| FR6 — the pipeline is incremental | 1 | 1.1 | Complete *(was Missing)* | Same test — the dirty set as a usable synthesis worklist |
| FR7 — a verdict is correctable | 1, 2 | 1.1, 2.2, 2.3, 2.4, 2.5 | Complete | Reducer change plus the four-document prose edit |
| NFR5 — append-only routing ledger | 1, 2 | 1.1, 2.2, 2.5, 2.6 | Complete | Upsert closes the mint race; 2.6 guards `corpus/routing.jsonl` |
| NFR8 — the toolchain gate | 2 | 2.1 | Complete | Notes cell was empty; filled |

## Review Findings

### Critical Issues

1. **FR4 and FR6 were claimed as covered with nothing asserting them**
   - Location: Requirements mapping table; Item 1.1 slices
   - Problem: the table's own Notes named the FR4/FR6 breakage precisely — "the
     empty dirty row `status` offers and `topic.assemble` refuses with
     `TopicNotRoutedError`" — yet all five slices asserted `Topic` rows and
     nothing touched `build_status` or `assemble`. The exact `unroute`
     counterpart already exists at `tools/ghmem/tests/test_unroute.py:279`,
     `test_status_dirty_set_stays_assemblable_after_emptying_a_topic`, whose own
     docstring calls itself "FR-4 end to end". The recall artifact's
     `outside-in-tdd` entry asks for exactly this: the first slice pins the
     observable contract through the production path, not the private helper.
   - Fix: added `test_status_dirty_set_stays_assemblable_after_a_reroute` to
     slice 1 — route `I_1` alone to `"softwrap"`, `I_2` and `I_3` to
     `"wrapping"`, re-route `I_1` to `"wrapping"`; assert `dirty == ("wrapping",)`,
     `topics == ("wrapping",)`, and that `assemble` raises nothing for each. It
     reds against unchanged code on both halves, so it is genuine red-phase
     evidence, not a guard.
   - **Status**: FIXED

### Major Issues

2. **Slices 1 and 2 prescribed two red phases against one implementation**
   - Location: Item 1.1, former slices 1 and 2
   - Problem: both were labelled "reds against unchanged code", which is true
     of *unchanged* code and false in slice order. One `_release_topic` call
     produces every observable in both. Once slice 1's implementation lands,
     slice 2's test is green on its first run — the "green at first is not
     evidence" trap `memory/ddaanet/genuine-red-not-missing-sut.md` names, and
     one wasted `/orchestrate` red dispatch reporting no red. The only way to
     keep them separate would be to prescribe an artificial intermediate
     implementation guarded on `data["verdict"] != NO_SIGNAL`, which nobody
     writes naturally and which is TDD theatre.
   - Fix: merged into one slice 1, the red batch, with both cases and the new
     FR4/FR6 test as named sub-bullets and a stated rule that its red phase is
     one run over all seven tests. Guards renumbered 2–4; the "Correction to the
     outline" section's pointer to "Slice 5" repointed to slice 4.
   - **Status**: FIXED

3. **Slice 4's (now 3) discrimination claim did not hold at the observable it asserted**
   - Location: Item 1.1, former slice 4, `test_same_topic_reroute_keeps_the_topic_dirty`
   - Problem: the runbook justified the test as discriminating "a release
     implemented as delete-then-recreate of the `Route` row ... where the pending
     `DELETE` can wipe the row the merge just wrote" — but the only assertion was
     on the `Topic` row. The topic assertion cannot fail: the join-side
     `_mark_topic_dirty(data["verdict"])` dirties `"softwrap"` under every
     implementation in the plausible space, including one that never reaches
     `_release_topic`. Cleaning the topic first defeats the birth-state trap but
     not this one — the observable is over-determined by a second writer. No
     single-point mutation was found that reds it. The claim was also unprobed,
     in a runbook whose own headline correction came from probing the analogous
     claim on slice 5.
   - Fix: renamed to `test_same_topic_reroute_keeps_the_topic_and_the_route` and
     added the `Route`-row assertion, which is the observable the stated failure
     actually moves; required the second `route` event to carry a different
     session and note so the merge is observable rather than a replayed no-op;
     rewrote the justification to say why the topic half alone cannot fail.
   - **Status**: FIXED

4. **`ghmem/ledger.py`'s `NO_SIGNAL` docstring is falsified in spirit and not in scope**
   - Location: Item 1.1 (semantic propagation)
   - Problem: `src/ghmem/ledger.py:139-143` says the reserved verdict "marks an
     item as triaged without creating or dirtying any `Topic` row". Slice 1's
     no-signal case is a `route` **to** `NO_SIGNAL` that *deletes* one. The
     sentence stays literally true and becomes misleading at exactly the point
     the change is least obvious. It was the only hit in the semantic sweep
     (`rg` over `empty dirty|emptied|TopicNotRoutedError|Topic row`, excluding
     `plans/` and frozen slice specs) that the runbook did not already own.
   - Fix: added as a fourth bullet to Item 1.1 with the target path, and to its
     `Interfaces:` block — deliberately in the behaviour item rather than a doc
     item, so it moves in the same commit as the behaviour.
   - **Status**: FIXED

5. **Item 2.6's landing check was not runnable**
   - Location: Item 2.6
   - Problem: the item gave a bare SQL block and "query it with", naming no
     command. There is **no `sqlite3` binary on this box** (`command -v sqlite3`
     is empty), so the item as written strands its executor. It is also a write
     hazard: nothing said the connection must be read-only against the live
     mirror.
   - Fix: replaced with a verbatim-runnable `python3 -c` block using
     `sqlite3.connect('file:corpus/cache.sqlite?mode=ro', uri=True)`. Verified
     against the live mirror during this review: the query parses, the `topic`
     and `route` tables exist with those column names, and it returns rows. The
     runbook's instruction to run it at landing rather than inherit a reading
     stands, so no answer is recorded here or in the runbook.
   - **Status**: FIXED

6. **Item 2.1 declared no dependency on Item 1.1**
   - Location: Item 2.1
   - Problem: the gate item exists to validate Phase 1's code and is the only
     Phase 2 item with no `Depends on:` line, while 2.2–2.6 all carry one.
   - Fix: added `Depends on: Item 1.1`.
   - **Status**: FIXED

### Minor Issues

7. **Docstring summary length off by two.** The runbook (and the outline) said
   `Replace an item's verdict, releasing the topic it leaves.` is "62 characters
   with indent". Measured: 57 characters of prose, 64 with the four-space indent
   and the opening `"""`. The conclusion — inside the 80-character ceiling
   docformatter's 81–88 dead zone imposes — is unaffected. Corrected to 64 with
   the counting rule stated. **Status**: FIXED

8. **Two line references off by one.** `docs/ghmem-design.md`'s FR7 paragraph
   starts at `:82`, not `:83`; the first Open question runs `:250-264`, not
   `:250-263` — `:263` is where the "Rationale as it stands:" line opens, and it
   wraps onto `:264`. Both corrected, with a note that the bullet ends at 264 so
   the deletion is not truncated. **Status**: FIXED

9. **The `_release_topic` position relative to the join-side dirtying was
   prescribed as load-bearing and is not.** Item 1.1 gave three ordering
   constraints under the sentence "Both orderings are load-bearing", then
   justified two. Every ordering of `_release_topic` against the join-side
   `_mark_topic_dirty` produces the same rows, including in the same-topic case.
   Reworded to say which two are load-bearing and that the third is fixed only so
   two executors write the same code. **Status**: FIXED

10. **Item 2.1's suite list omitted the plausible fixture casualties.** It named
    `test_unroute.py`, `test_reenter.py`, `test_requeue.py` and
    `test_cli_close.py` — the suites the *upsert* runs under — but not
    `test_status.py` and `test_dispatch_store.py`, which are the ones exposed to
    the fixture's changed replay result. Both are in fact safe (`test_status.py`
    reads its own `status_routing.jsonl`, which carries no re-route;
    `test_dispatch_store.py` asserts only `dispatched_unrouted() == [I_3]`), and
    saying so turns the probe's "5 red" into a checkable claim. Added, with the
    reason each is safe. The gate's cache inputs were also stated as `src/` and
    `tests/` only; corrected to the full set from `CLAUDE.md`. **Status**: FIXED

11. **Item 2.5 named no date and mis-stated the entry form.** The changelog's
    convention is a top-level list item (`- **YYYY-MM-DD — <title>.**`), not a
    bare bold line, and nothing said which date. Added both — dated the day the
    change lands, via `date -I`. **Status**: FIXED

12. **Slice 4 (was 5) had no stated construction mechanism.** It lands in
    `test_routing_replay.py`, which has no event builders, while the settled
    placement decision covers `test_routing_live.py` only — so the executor was
    left to invent one. Added: build a `Ledger[RoutingEvent]` under `tmp_path`
    and `append` four inline `route` events, no `dispatched` events (the reducer
    routes an item with no prior dispatch, as `test_reroute_dirties_the_topic_it_leaves`
    already relies on), then one `Store.open` and one `rebuild_from_ledgers`.
    Also added the instruction to actually *run* the discriminating mutation —
    apply the non-autoflushing upsert, confirm this test alone reds, revert —
    since the claim is the test's whole justification and the runbook's own
    headline correction came from an unrun claim of the same shape.
    **Status**: FIXED

13. **`test_live_writes_match_a_cold_rebuild`'s added assertion was placed
    ambiguously.** "Taken on the live side before the rebuild comparison" admits
    two readings in a test whose assertions all sit after the rebuild. Pinned to
    the first `_session` block, alongside the `live` tuple capture.
    **Status**: FIXED

14. **`test_unroute.py:136` echoes the old decision title.** Its docstring opens
    "Unrouting a topic's last item deletes the Topic row entirely." — prose
    describing `unroute`'s own unchanged behaviour, not a citation of the
    decision. Item 2.3's rename sweep now names it explicitly as staying, so an
    executor grepping the old title does not touch it. The runbook's claim that
    `docs/ghmem-design.md:90` is the only live citation of the old title is
    correct; the three hits under `plans/ghmem-design-migration/reports/` are
    frozen. **Status**: FIXED

15. **`docs/ghmem-harness.md` was neither edited nor excluded.** Its `ghmem
    route` entry says the command "marks the topic dirty", now an understatement.
    Left unedited — the `unroute` entry carries no topic-lifecycle clause either,
    so adding one to `route` alone would put the two command contracts out of
    step, and that lifecycle lives in `docs/ghmem-decisions.md`, which Item 2.2
    owns. Recorded as an explicit Out-of-scope entry with that reasoning rather
    than left silent. **Status**: FIXED

16. **Empty Notes cell for NFR8** in the requirements table. Filled.
    **Status**: FIXED

## Weighed and not flagged

- **Test placement (duplicate the builders rather than extract
  `routing_helpers.py`).** The reasoning holds. Three of the five routing test
  files that share `_live_store`/`_session` sit outside this job's boundaries
  (`test_reenter.py`, `test_requeue.py`, `test_unroute.py`), and a partial
  extraction leaves the duplication standing while adding an import layer.
  Verified that both builders now have consumers: the FR4/FR6 test needs
  `_dispatch`, slices 1–2 need multi-item `_route`. Before this review's
  addition, `_dispatch` would have shipped unused — the one thing the placement
  decision got wrong, and it is fixed by the same edit that fixes finding 1.
  The named follow-up survives in Out of scope.

- **Slices 2–4 as green-only guards.** Honestly named in every case, and each
  now carries an explicit "no red phase". Slice 2's concession that the
  `_release_topic` mutation reds `test_unroute_dirties_a_topic_that_keeps_items`
  too, so it discriminates nothing that test does not, is exactly right and
  worth keeping as written. Slice 4's probe correction — the emptied-then-
  re-minted scenario stays green under the get-then-add helper, because
  `session.get` autoflushes the pending `DELETE` — was re-read against the recall
  artifact and stands. The one guard whose stated discrimination did not hold is
  finding 3.

- **The SQL/command code block in Item 2.6.** The runbook format bars code
  blocks in items. Kept, deliberately, and labelled as such in the item: it is a
  command to run verbatim, not an implementation to copy, and the ddaanet
  convention that commands printed for execution must be verbatim-runnable
  outweighs the format rule here. It is the only code block in the runbook.

## Phase structure, dependencies, growth

- Two phases, six items, no forward dependencies, no cycles. Phase 2's gate item
  correctly precedes the doc items — the doc edits fall outside `just green`'s
  cache inputs, so nothing after 2.1 re-triggers it. That reasoning was implicit
  in the outline and is now stated in Item 2.1.
- Item counts: Phase 1 has one item (4 slices), Phase 2 has six. The
  concentration is real but correct — the change is one atomic reducer edit and
  its evidence is one red batch.
- Growth measured against the gate's 400-line cap
  (`tools/ghmem/scripts/check_line_limits.sh`, over `src` and `tests`):
  `routing_reduce.py` 337, `test_routing_replay.py` 340, `test_routing_live.py`
  186. The first two have ~55 lines of headroom each and both are projected to
  land inside it. Stated in Item 1.1 so the executor does not discover the cap
  from a gate failure. `docs/ghmem-design.md` is at 399 against NFR9's 400-line
  budget and Item 2.3 is a net deletion of ~13 lines, which improves it.
- The runbook itself is 486 lines, past NFR9's budget. Stated in its opening
  paragraph with the reason rather than absorbed, per NFR9's own escape clause;
  my additions were trimmed where they could be without losing the finding.

## Design Alignment

Every outline deliverable maps to an item: `routing_reduce.py` (1.1), the five
red tests and the new coverage (1.1 slices), the four documents (2.2–2.5), the
gate (2.1) and the landing check (2.6). The runbook's two documented departures
from the outline — the settled placement question and the probe correction to
the upsert's "only deterministic test" — are both stated in their own sections
and both hold. `ghmem/ledger.py` (finding 4) is the one deliverable neither
document had.

Fixture claims re-derived from source rather than inherited: `routing_events.jsonl`
does re-route `I_1` from `"softwrap"` to `"wrapping"` at its last event carrying
the same `item_updated_at`, so the settled `test_route_creates_topic_marked_dirty`
retarget and its surviving second assertion are both correct; the fixture is read
by exactly three test files; `dispatch_store.py:20` is the `sqlite_insert` import;
`docs/ghmem-decisions.md:120` is the decision to retitle; `test_unroute.py:20-45`
and `:109-111` are the builders and the birth-state note.

# TDD audit — Phase 1, Item 1.1 (`reroute-empty-topic`)

Scope: the runbook's one `tdd` item, `routing_reduce.py`'s four slices
(`runbook.md:158-171` and its four numbered sub-sections). Verified against
`plans/reroute-empty-topic/reports/item-1-1-s1-{red,test-review,green,code-review}.md`,
`item-1-1-s2-4-guards.md`, `phase-1-corrector.md`, and git (`556c51f..a2361d1`).
Phase 2 read only for the two fixes it independently reconfirmed; not
audited.

**Verdict: the phase followed the runbook's prescribed shape correctly, with
one real TDD finding (already surfaced and half-fixed by the corrector,
covered in full below) and no process violations.**

## Slice 1 — the red batch

RED, test review, GREEN, code review all ran as separate dispatches, each
with its own commit or report. This is the only slice with red-phase
obligations, and they were met:

- **7 tests exercised, 6 reds, 1 expected green** — every red fails on a
  shown `assert` line (actual vs. expected), none on collection or import
  error. The one green
  (`test_routing_replay.py::test_route_creates_topic_marked_dirty`,
  retargeted) was investigated rather than forced red: the RED report traced
  why unchanged code already satisfies it (the join-side dirtying it reads is
  untouched by this change), the test-review dispatch independently probed
  the *other* side too — that it stays green after the fix, for the same
  reason — before accepting it. This matches the runbook's own text, which
  is the only slice-1 bullet not claiming a red for its test.
- **Scope discipline held**: `git diff --stat -- tools/ghmem/src/` was empty
  at both RED and test-review handback; only the two test files and the two
  report files moved.
- **GREEN** landed the four prescribed edits in one commit (`921494b`)
  carrying implementation and the already-red tests together, all seven
  target tests passing plus the full 280-test suite green, `just precommit`
  passing. One widening beyond the runbook's four bullets (a docstring fix to
  `apply_routing_event`, same file, no test depends on it) — in scope, not a
  test-driven addition, correctly disclosed as such.
- **Code review** independently re-derived both load-bearing orderings from
  emitted SQL (not from reading) and ran two mutations
  (`_release_topic` before merge; `left` read after merge), each redding the
  same six tests and none touching the seventh — positive evidence the tests
  discriminate what the runbook says they must. One accuracy fix to a
  docstring (misattributed autoflush site), no assertion changed.

No finding here. This slice is the strongest-evidenced part of the job:
every claim in a report is backed by a shown transcript or an in-process
probe, not asserted.

## Slices 2–4 — guards and the mutation matrix

Correctly run with no red phase, exactly as the runbook prescribes and
`item-1-1-s2-4-guards.md` states up front (`runbook.md:158-165`: "none reds
against unchanged code, so none gets a red phase, stated per slice and not
an omission"). Commit shape matches the runbook's slice list one-to-one:

- `ef67714` (test only) / `06ec547` (test only) / `aafb0ae` (test only) — the
  three guard tests, each a separate commit, none touching `src/`.
- `466d818` (test only) — docstring updates recording the mutation results
  *inside* the test files, once the matrix had run.
- `a9d2766` — the guards/matrix report file itself.

This is the one place the execution shape departs from a straight
"write test, run mutation, commit" per slice — the mutation matrix was run
as a batch across all three guards after all three were written, then the
results were written back into all three docstrings in one commit. That
batching is not a violation: the runbook already frames the three guards as
a set ("must be run against it" as one instruction covering all three), and
nothing was committed between the three test-writing commits that could
mask a result — `git diff --stat` on `466d818` touches only the two test
files' docstrings, no assertion.

**The mutation matrix did what it claims, with two exceptions the report
itself flags and the phase-1 corrector confirms as real findings rather
than execution mistakes:**

| Mutation | Claim | Verified |
|---|---|---|
| M2 (route-side release skips dirty on `remaining` branch) | reds slice 2's test alone | Reproduced: `test_reroute_keeps_a_topic_that_keeps_items` fails on a shown `dirty: False != True` diff; slice 3, slice 4, and `test_unroute_dirties_a_topic_that_keeps_items` all stay green |
| M3 (delete-then-recreate release) | should red slice 3's `Route`-row assertion | **Does not red under any construction tried.** Both plausible readings of the runbook's prose were built and run, plus the full routing suite (29 tests) under one of them — nothing reds |
| M4 (upsert issued on the connection) | should red slice 4 | **Does not red**, confirmed across routing + `unroute` + `reenter` + `requeue` (40 tests) |

M4's non-discrimination was pre-announced by the runbook itself
("Revised at execution: expect this mutation may not red") and by slice 1's
own code review, which traced the intervening `session.merge` autoflush
before slice 4 was even written. Running it anyway and reporting the actual
result, rather than treating the pre-announcement as license to skip the
run, is correct process — the runbook explicitly asked for the run over the
prediction.

M3's non-discrimination was **not** pre-announced and is the audit's one
substantive finding, addressed next.

## The M3/M4 finding: a guard test whose discriminating defect cannot occur

`phase-1-corrector.md` §5 goes beyond confirming the s2-4 report's raw
mutation results — it traces *why* both M3 and M4 fail to discriminate, and
the reason is structural rather than "wrong test": SQLAlchemy's unit of work
collapses a same-primary-key delete-and-re-add within one flush into a
single `UPDATE`, emitting neither a `DELETE` nor an `INSERT`, for both the
`Topic` and the `Route` table, under both readings of what "written as
delete-then-recreate" could mean. The runbook's own hypothesis — "the
pending `DELETE` can wipe or collide with the row the merge just wrote" —
describes a failure mode this ORM does not produce through its normal API;
the corrector shows (§5, "M4 — is `session.execute()`'s autoflush ever
load-bearing?", points 1–3) that reaching a real wipe requires going
around the unit of work entirely (issuing SQL on the raw connection), which
is exactly M4's construction and not M3's. So M3 and M4 turn out to be the
same mechanism tested twice: one that is real but is never reachable from
any ledger-driven call path in this reducer.

This is a genuine TDD defect in the *runbook's* prescription, not in its
execution: a mutation named to prove a guard test discriminates a specific
defect, where that defect is provably unreachable through the ORM being
tested. Both agents who touched it (`item-1-1-s2-4-guards.md` and
`phase-1-corrector.md`) handled it correctly — they ran the mutation rather
than assuming the runbook's prose, reported the negative result plainly,
traced the mechanism to ground rather than leaving it as an unexplained
non-red, corrected only the false *mechanism* claim in each test's
docstring (not the assertions), and explicitly declined to delete or rename
either test on their own initiative. That last part matters: an unrun or
silently-accepted discrimination claim is the exact failure mode the runbook
itself calls out having happened once already in this job ("An unrun
discrimination claim of exactly this shape was already wrong once here",
`runbook.md:166`) — here it was caught, not repeated.

**Net effect on the two guard tests, as they stand in the tree today:**

- `test_same_topic_reroute_keeps_the_topic_and_the_route` (slice 3) — its
  `Route`-row assertion is not falsifiable by any mutation constructed so
  far; its `Topic`-row assertion is separately known to be
  over-determined (dirties under "every implementation in the plausible
  space", per the runbook's own text). The test is not vacuous — it is the
  only coverage in the suite that a same-topic re-route reaches
  `_release_topic`'s `remaining` branch and that a live re-entry into the
  same topic behaves at all — but the specific *discrimination claim* the
  runbook wrote for it is now demonstrated false, and the docstring has
  been corrected to say so rather than to restate the false mechanism.
- `test_a_topic_reminted_after_being_emptied_in_one_replay_survives`
  (slice 4) — same shape: real new coverage of the emptied-then-re-minted
  replay path, but the thing it was named for (discriminating the
  upsert's autoflush) is proven structurally unreachable, per corrector §5's
  three-point closure (merge's unconditional autoflush; `reenter` cannot
  reach the hazard; `_release_topic`'s own `remaining` branch is safe by
  construction).

Per this audit's brief, no recommendation is made on keeping, dropping, or
renaming either test — that decision, and the third one
(`test_route_creates_topic_marked_dirty`, which `phase-1-corrector.md` §2
independently found is now fully subsumed by
`test_reroute_replaces_the_verdict_and_drops_the_emptied_topic`), are named
in the reports as open and left untouched in the tree, exactly as the brief
requires.

## Commit discipline

All eight Phase-1 commits carry a matchable `Item 1.1/k` marker in the
subject (`556c51f` predates the numbered slices — it is the precondition
extraction — and is correctly outside this item). Emoji prefixes are the
`commit-msg` hook's rewrite of the conventional-commit type, not a deviation
— confirmed by the report text describing exactly this for `921494b`. Each
slice's test-writing and result-recording commits are cleanly separated from
report-recording commits (`ffdf24c`, `928ab0d`, `40f7330`, `0fc7098`,
`a9d2766` are pure `plans/` additions). No commit mixes `src/` and `plans/`
changes. The one phase-boundary commit, `3c2cefa`, correctly carries both
`phase-1-corrector.md` and the two source/test fixes it made, together.

## Test integrity

No assertion was weakened, deleted, or added to force a green anywhere in
the phase. Every fix applied by a review dispatch (test-review's one
docstring rewrite; code-review's one comment rewrite; the corrector's two
docstring rewrites) is textually a prose/mechanism correction — confirmed
by reading the current file contents against the reports' quoted diffs, and
by `git show 3c2cefa` for the corrector's pair, which touches only
docstring/comment lines in `routing_reduce.py` and
`test_routing_live.py`. The `green-is-not-evidence` guards the runbook
calls for (born-dirty traps, non-vacuous set comparisons) were checked by
both the code review and the phase corrector and found intact.

## Where this audit's checks were probe-based rather than re-run

The mutation transcripts, SQL traces, and probe scripts quoted in
`item-1-1-s2-4-guards.md` and `phase-1-corrector.md` were not independently
re-executed for this audit — they were cross-checked against the current
tree (test docstrings, commit diffs, `git log`) rather than rerun from
scratch. Nothing found in that cross-check contradicts what the reports
claim.

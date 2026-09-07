# Recall artifact — reroute-empty-topic

Memory entries selected at the `/runbook` implementation-recall checkpoint,
kept to what changes a planning decision. Execution-level detail reaches
executors through dispatch prompts, not this file.

## Selected at step 1 (implementation patterns)

- `memory/ddaanet/green-is-not-evidence.md` — the governing entry for this
  plan. Three of the five red tests are named in it by shape ("satisfied by
  birth state", "two derived states compared", "idempotence standing in for
  teardown"), and this change shrinks the hardcoded topic set that is their
  only non-vacuity guard. Also the source of the rule the runbook applies to
  the three non-discriminating slices: never leave a test whose name claims
  coverage it does not have.
- `memory/ddaanet/genuine-red-not-missing-sut.md` — red is a failed
  assertion. Here the SUT already exists, so red comes from retargeting
  existing assertions; the runbook records which slices red against unchanged
  code and which are mutation-detectable guards instead.
- `memory/ghmem-project.md` — docformatter 1.7.7, and the 81–88 character
  docstring-summary dead zone that constrains the one summary line this change
  rewrites. Also: schema changes carry no back-compat, and `corpus/routing.jsonl`
  is the one file a schema change cannot undo.
- `memory/ddaanet/plan-contracts-not-full-code.md` — the runbook states
  contracts and tests in prose; no implementation code blocks.
- `memory/ddaanet/outside-in-tdd.md` — architecture is settled here, so the
  first slice pins the observable contract through the production write path
  (`record_routing_event` / `rebuild_from_ledgers`), not the private helper.

## Added at step 4 (post-explore)

Exploration surfaced a four-document prose edit and a set of docstrings whose
justifications this change falsifies.

- `memory/ddaanet/design-doc-no-situational-state.md` — why the dropped Open
  question's "zero empty `Topic` rows (2026-09-03)" observation goes with it
  rather than being restamped, and why the landing check in Phase 4 states the
  query rather than its current answer.
- `memory/ddaanet/no-doc-history-references.md` — the replaced docstring
  paragraphs are rewritten as positive statements of the surviving rule, not as
  "this used to say X". Standalone rejections (the `docs/ghmem-rejected.md`
  entry) are decisions and stay.
- `memory/ddaanet/remove-cleanly-no-vestigial.md` — the docstring paragraphs
  whose reasoning this change falsifies are deleted, not reworded; the
  `dirty = False` cleaning step is dropped from the tests whose assertion
  becomes `is None`, where it no longer guards anything.

## Probe findings recorded here because they changed the plan

Run during this checkpoint against a reverted working copy:

- The outline's red set reproduces exactly: 5 failed, 273 passed.
- **The within-one-replay-session re-mint scenario does not red against the
  current get-then-add `_mark_topic_dirty`.** Probed directly: replaying
  `route I_1→softwrap, route I_2→wrapping, route I_1→wrapping,
  route I_2→softwrap` in one session leaves both topic rows present under the
  unchanged helper, because `session.get` autoflushes the pending `DELETE`
  before the recreate. The outline's claim that this is "the only deterministic
  test of the upsert" is therefore wrong; the runbook names slice 5 for what it
  actually discriminates instead.

# Simplification Report

**Runbook:** `plans/reroute-empty-topic/runbook.md`
**Design:** `plans/reroute-empty-topic/outline.md`
**Also read:** `plans/reroute-empty-topic/reports/runbook-review.md`,
`plans/reroute-empty-topic/recall-artifact.md`
**Date:** 2026-09-07

## Summary

- Lines before: 486. Lines after: **459** — still 59 over NFR9's 400-line
  budget. The main prize was not won; §"Why 400 was not reached" says what the
  remaining 59 would have to cost.
- Items before: 7 (1.1, 2.1–2.6). Items after: 7. **No items were merged** —
  see §"Patterns not consolidated".
- Consolidated: 5 prose patterns, plus one merged pair of slice-1 test bullets.
- Requirements mapping table, phase structure, item numbering, every
  `Requirements:` line, every `Depends on:` line, both `Interfaces:` blocks and
  the verbatim `python3 -c` block are unchanged in substance.

## Consolidations applied

### 1. The two header sections merged into one

- **Type:** identical-pattern (same reasoning restated at two levels)
- **Merged:** `## Questions the outline left open, settled here` (34 lines) and
  `## Correction to the outline, from a probe run at the recall checkpoint`
  (11 lines) → `## Settled here, against the outline` (29 lines).
- **Rationale:** the named redundancy. Both sections restated reasoning that
  also lives in the slices and items they govern. Each open question now has
  exactly one statement:
  - *Test placement* stays in the header (Out of scope and slice 4 both point
    back at it), tightened; the `routing_helpers.py` rejection keeps all three
    out-of-boundary file names.
  - *FR7's topic-lifecycle clause* is now one pointer line in the header, with
    the full reasoning moved into **Item 2.3**, where the edit is made. The
    header previously carried 8 lines of it and Item 2.3 carried the
    instruction without the reason; the two are now one statement in one place.
  - *The probe correction* keeps its own paragraph (mechanism included: the
    scenario is green because `session.get` autoflushes the pending `DELETE`),
    because slice 4 states the consequence but not the mechanism.

### 2. `test_rebuild_is_idempotent` + `test_live_writes_match_a_cold_rebuild`

- **Type:** identical-pattern
- **Merged:** two slice-1 bullets → one.
- **Rationale:** the same two edits to each test (expected set becomes
  `{"wrapping"}`, add `assert session.get(Topic, "softwrap") is None`) with the
  same non-vacuity justification stated twice. The merged bullet keeps the
  cold-rebuild test's placement instruction (inside the first `_session` block,
  alongside the `live` tuple capture, before the rebuild) and names both
  derived-state comparisons.

### 3. The `docs/design/<name>.md` softening, stated once

- **Type:** sequential addition (one edit written into two artifacts)
- **Merged:** Item 1.1's `_release_topic` bullet no longer re-argues why the
  "remains true prose" clause is falsified; it drops the clause and points at
  Item 2.2, which already said "they are one edit stated twice" and which
  records the accepted cost in full (doc left on disk, `ghmem index` still
  listing it, no dirty row, strictly worse than the `unroute` case, no surface
  reports it).

### 4. Item 2.1's two prose blocks

- **Type:** same-target batching
- **Merged:** the "suites that must come back green" paragraph and the
  "plausible fixture casualties" paragraph → one. Both list suites the gate
  must return green with the reason each is safe; the split implied two
  different checks. The full cache-input enumeration (review finding 10) is
  untouched.

### 5. Out of scope: the filesystem-deletion bullet

- **Type:** identical-pattern
- **Merged:** its three-line replay-destructiveness argument, which is stated
  verbatim in Item 1.1's `Interfaces:` block as the shared prose contract, is
  now a pointer to that block. The exclusion itself stays listed.

### 6. Line-level compression, no claim dropped

Item 1.1's four edit bullets, slices 2–4, Items 2.2–2.6 and the remaining Out
of scope bullets were rewritten tighter. Every identifier, path, line
reference, count and test name in the pre-pass runbook is still present —
checked mechanically against a 40-token list (all test names, `:139-143`,
`:250-264`, `:82-93`, `:120`, `337`/`340` lines, `64 characters`, `81–88`,
`5 failed, 273 passed`, `95 routes`, `date -I`, `mode=ro`, the fixture note
string, and so on).

## Preserved verbatim in substance, as required

- **Slice 1 is the only red batch.** Header: "the red batch is seven tests, not
  five". Slices preamble: "slice 1 is the red batch and its red phase is one run
  over all seven tests, not one per observation site", with the reason splitting
  it manufactures an already-green red.
- **Slices 2–4 are green-only guards with no red phase**, each named for what it
  discriminates: slice 2 says outright it "discriminates nothing an existing test
  does not" and names the narrower thing it adds; slice 3 names the
  delete-then-recreate `Route`-row failure and says why the topic half alone
  cannot fail; slice 4 says "no red phase, probe-confirmed green", names the
  non-autoflushing upsert as what it discriminates, and keeps the instruction to
  run that mutation.
- Every `Requirements:` and `Depends on:` line, both `Interfaces:` blocks, and
  the `python3 -c` block (byte-identical, including `mode=ro`).

## Patterns not consolidated

- **Items 2.2–2.5 (four doc edits) were not merged into one item.** They are
  four different artifacts with a real dependency chain (2.3 and 2.4 depend on
  2.2; 2.5 depends on 2.2, 2.3 and 2.4), and merging them would delete three
  `Requirements:` lines and three `Depends on:` lines, which the task forbids.
  Item 2.2 already batches the only same-artifact pair.
- **Slice 1's per-test red justifications** ("against unchanged code X fails")
  read as repetition but are the red-phase evidence for seven distinct
  assertions; removing any one would leave a test in the batch unjustified.
- **Item 1.1's concurrency argument and Item 2.2's mint-race argument.** They
  argue different things into different artifacts (a docstring and a decision
  entry) and each is the text an executor writes. Compressing either to a
  pointer would thin the deliverable, not the runbook.
- **Reflowing to recover lines was tried and abandoned.** The file is already
  wrapped at 80 columns; a greedy refill at width 80 produced *more* lines, not
  fewer. Formatting carries no slack. (The pass did fix ~48 lines that my edits
  had pushed to 81–82 characters, and every code span now sits on one line.)

## Why 400 was not reached

After the redundancy above is gone, what remains is instruction and
justification the review deliberately added: the FR4/FR6 test that fixes
finding 1, the merged red batch that fixes finding 2, slice 3's `Route`-row
assertion (finding 3), the `ledger.py` docstring clause (finding 4), the
runnable query (finding 5), slice 4's construction mechanism (finding 12). The
59 lines still over budget would have to come out of those. Three candidates,
with what each costs:

1. Slices preamble, ~4 lines — the argument for *why* slice 1 is not split.
   Dropping it invites the next reviewer to re-raise review finding 2.
2. Item 2.2's mint-race argument, ~6 lines — replaceable by a pointer to the
   outline's "The mint race the change would otherwise widen", at the cost of
   the executor writing the decision entry from a second document.
3. Item 1.1's ordering arguments and the concurrency paragraph, ~10 lines —
   these are the docstring text itself.

None is a redundancy; each is a trade. The runbook's opening paragraph
therefore still states the overrun under NFR9's escape clause, rewritten to
one sentence noting the consolidation pass has already run.

## Requirements mapping

No changes — table untouched, all five requirements (FR4, FR6, FR7, NFR5, NFR8)
still mapped to the same items, and every item named in the table still exists
with the same number.

## Validation

- Phase structure intact: two phases, `## Phase 1 … (type: tdd)`,
  `## Phase 2 … (type: inline)`.
- Item numbering sequential: 1.1; 2.1–2.6. Slices renumbered nowhere — 1–4 as
  the review left them.
- No forward dependencies introduced: 2.1, 2.2, 2.6 → 1.1; 2.3, 2.4 → 2.2;
  2.5 → 2.2, 2.3, 2.4.
- Cross-references checked after the header merge: Out of scope's "per the
  placement decision above" and slice 4's "the placement decision above covers
  `test_routing_live.py` only" both still resolve.

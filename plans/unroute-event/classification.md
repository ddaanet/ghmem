# Triage — post-hoc design record for the unroute event

Invocation covered two items (explicit bundling: the second was folded
in by the `/edify:design` arguments). Classified individually.

## Item 1 — post-hoc design record for the shipped `unroute` event

**Requirements-clarity gate**

- Requirements source: `plans/unroute-event/requirements.md`
- Completeness: each FR has a concrete mechanism — Y (6 FRs, each with
  acceptance criteria); each NFR has a measurable criterion — Y (400-line
  cap via `scripts/check_line_limits.sh`, `just precommit`)
- Routing: proceed to triage

**Classification**

- Classification: Simple
- Implementation certainty: High — the code is shipped, green and
  mutation-tested; the doc records what exists rather than proposing it
- Requirement stability: High — the four open questions were answered
  and written into `requirements.md` before the build
- Behavioral code check: No. Prose artifact only; no functions, logic
  paths or conditional branches
- Work type: Investigation — produces a record, not a capability
- Artifact destination: investigation
- Evidence: `requirements.md` (FR-1..FR-6, C-1..C-6, Decisions Q-1..Q-4);
  the shipped diff; `memory/ddaanet/design-doc-writing.md`;
  `memory/ddaanet/plan-contracts-not-full-code.md`

**Routing: blocked on Item 2's Fork A.** Under the recommended shape this
item does not produce a standalone dated slice spec at all — the unroute
design becomes a section of the living design doc plus one changelog
entry. Where that doc lives decides this item's container, so it waits.

## Item 2 — migrate the living design doc to the six-section structure

**Requirements-clarity gate**

- Requirements source: user request + `memory/ddaanet/design-doc-writing.md`
- Completeness: each FR has a concrete mechanism — **N**. The named
  source file does not exist, the target path is unspecified, and the
  fate of the five dated slice specs and ten plans is unspecified
- Routing: **blocked on two decisions** (Forks A and B below), not
  `/requirements` — the target structure is fully specified by the
  memory; only container and scope are open

**Classification**

- Classification: Complex
- Implementation certainty: High — the six-section target and the
  record-vs-current-truth split are specified in detail
- Requirement stability: **Low** — see the gate above
- Behavioral code check: No. Prose artifact only
- Work type: Investigation
- Artifact destination: investigation
- Evidence: `docs/` inventory (four distinct subjects, not one project);
  `docs/design/` already occupied by ghmem's generated topic docs;
  `git log` on the root spec versus the dated slice specs

## Grounded findings that shape both items

- **`docs/design.md` does not exist.** No file by that name anywhere in
  the repo.
- **The living ghmem design doc was
  `docs/superpowers/specs/2026-07-09-ghmem-design.md`** at the time of
  this triage, and has since been migrated to `docs/ghmem-design.md`.
  Git showed it corrected whenever a slice made a claim in it false —
  `76f3a0d` ("correct two claims B2 made false"), `92d1283`
  (2026-09-02). The five dated slice specs are revised only inside
  their own slice window and freeze after.
- **`docs/design/` is ghmem's generated output** — `INDEX.md` plus five
  synthesized topic docs. A `docs/design.md` beside it would collide
  confusingly.
- **This repo hosts several subjects**, not one: the colwrap plugin (the
  repo's stated goal), ghmem, the popup layer, the display-test system.
  `design-doc-writing`'s "single living doc for the project" assumes one
  project per repo, and says so itself: "The rule is one living doc, not
  one filename; check where the project actually keeps it before
  creating a second."

## Forks — both since resolved

**Fork A — where the living ghmem design doc lives.** Resolved:
`docs/ghmem-design.md`, beside the other subject docs, dropping the
misleading date. `docs/design.md` was rejected for colliding with
`docs/design/`, which is itself slated to become `skills/`.

**Fork B — what happens to the five dated slice specs and ten plans.**
Resolved: they stay frozen as write-time records, and
`docs/ghmem-changelog.md` indexes them.

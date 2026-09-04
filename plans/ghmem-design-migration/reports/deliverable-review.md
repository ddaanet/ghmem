# Deliverable Review: ghmem design-doc migration

**Date:** 2026-09-03
**Range:** `f3d1276` (with `cba5073`, `17ad255` for the co-landed prose)
**Methodology:** `edify:deliverable-review` — Layer 1 delegated
(`deliverable-review-prose.md`), Layer 2 cross-cutting in the main
session. The two layers read the artifacts independently; findings
below are the union, deduplicated, with every claim re-verified in the
main session before it was promoted.

## Inventory

| file | lines | type | verdict |
| --- | --- | --- | --- |
| `docs/ghmem-design.md` | 656 | living design doc | Major issues — 3 false CLI claims, 2 unsupported NFRs, heavy self-duplication, violates its own NFR9 |
| `docs/ghmem-changelog.md` | 123 | history index | **Critical** — asserts a re-triage the ledger shows never ran |
| `tools/ghmem/README.md` | 106 | tool README | **Critical** — two stale status claims; 7 of 13 commands undocumented |
| `plans/ghmem-design-migration/report.md` | 169 | migration report | Major — its verification grep does not reproduce; 10/10 drift claims themselves verified |
| `plans/ghmem-design-migration/reduction-report.md` | 203 | reduction record | Minor — line citations off by 1–2 throughout |
| 9 files under `docs/superpowers/` | 10 ref lines | repointed references | Clean |
| `docs/superpowers/specs/2026-07-09-ghmem-design.md` | −496 | deleted | Correctly retired; no dangling inbound link |

**Design conformance.** The migration's stated goal — a living design
doc plus a changelog, with the ten drift items corrected and every
inbound reference repointed — is met on the mechanical half. All ten
drift corrections verified. Reference integrity is genuinely clean:
the three surviving mentions of the old path are a deliberate
historical statement (`classification.md:66–67`) and two
self-references. The failures are in the newly authored content: the
FR/NFR set states five things about the system that are not true, and
the changelog states one thing about the world that is not true.

## Critical Findings

**C1 — `docs/ghmem-changelog.md:119–123` asserts a re-triage that never
happened.** The entry says the 22 items of sessions `BcbBJeUg` and
`MZlH9vbE` "are unrouted and re-triaged". Verified false in the main
session: `grep -c '"unroute"' corpus/routing.jsonl` → `0`; the routing
ledger holds only `route` (103), `dispatched` (95), `snapshot` (6) and
`close` (5) events, and both sessions still hold their full route sets
(12 + 10 = 22). The same paragraph cites 95/71/57 as the *before*
state, and those are also the numbers right now, so a reader gets no
signal that anything is outstanding. **Failure scenario:** the
changelog is the durable record of what landed; the next session
reads it, believes the singleton problem was addressed, and skips the
pending FR-6 work. Either reframe the paragraph as planned, or run the
batch before the entry claims it.

**C2 — `tools/ghmem/README.md:17–19` says the dispatch subproject "is
in progress".** It shipped: the changelog itself records Parts A, B1,
B2, C1, C2 and D, and `cli.py` exposes `triage`, `route`, `topic`,
`close`, `index`, `requeue` and `unroute`. The README's Commands
section (`:41–65`) documents 6 of the 13 commands. This file was
touched by `f3d1276`, so the staleness was in scope.

**C3 — `tools/ghmem/README.md:86–89` calls the heat weights
"provisional — calibrated against the real corpus in the validation
subproject".** Contradicted by `heat.py:8–12` ("Calibrated 2026-07-25
… and retained") and by the design doc. This is drift item 8 of the
migration report — corrected in the design doc, left uncorrected in
the README the same commit edited.

**C4 — `docs/ghmem-design.md:238` documents a `stuck` count `ghmem
status` does not emit.** Verified: `grep -rn "stuck" tools/ghmem/src`
returns nothing, and `render_status` (`status.py:68–78`) emits
`total triaged routed no_signal pending dispatched`. The doc names an
output that does not exist and omits `total`. An eleventh drift item,
inherited verbatim from the deleted spec, that the ten-item sweep
missed.

**C5 — `docs/ghmem-design.md:422–424` claims every dispatch is tagged
`fit`/`diff_cut`/`oversized` "in the packet and in the ledger".** In
the ledger, yes. In the packet, no: `_render_packet` emits a
`packet-note:` line only when a note is passed, the `fit` path passes
none (`triage.py:129`), and the two notes that are emitted are prose
(`_DIFF_CUT_NOTE`, `_OVERSIZED_NOTE`), not those tokens. The B2 spec
states the truth explicitly. Twelfth uncorrected drift item, again
inherited verbatim.

## Major Findings

**M1 — the design doc asserts a global invariant the code holds on one
path only.** "Unrouting a topic's last item deletes the `Topic` row"
(`:504–513`) opens "A topic row exists exactly while some route names
it." Verified false: `_release_topic` (`routing_reduce.py:232`) is
reachable only from `_apply_unroute_event`; `_apply_route_event` calls
`_mark_topic_dirty` directly, so re-routing a topic's last item still
leaves an empty dirty `Topic` row. **Failure scenario:** re-route the
last item out of topic T → T stays in `status`'s dirty set → a
synthesis worker dispatched from that list hits `TopicNotRoutedError`
(`topic.py:53`). The paragraph discloses that the step is scoped to
`unroute` but never that the invariant consequently does not hold, so
a reader concludes an empty `Topic` row is unrepresentable. It is not.
`requirements.md` Q-2 is honest about this; the design doc's condensed
version is not.

**M2 — the living design doc has no Limitations and no Open questions
section**, though Architecture §2 mandates exactly FR / NFR /
Limitations / Open questions / History for every topic doc ghmem
generates. M1's asymmetry, C1's outstanding batch, NFR10's unrun gate
and the pending `topic merge` event have nowhere to live in it, so
each survives only in a plan directory. The doc holds its own outputs
to a structure it does not meet.

**M3 — FR2/NFR3 claim a mechanical no-GitHub guarantee the hook does
not deliver.** `ghmem-only.sh` is a command-*shape* allowlist, so
`ghmem sync` passes it and reaches the API. The doc's own rejected
alternative (`:601–612`) enumerates `requeue` and `rebuild` as the
permitted mutating commands and omits `sync`, the one that matters for
FR2. A reader deciding whether an agent needs a credential-free
environment will conclude the hook suffices; FR2 in fact rests on the
agent prompt, which is what NFR3 says containment does not rest on.

**M4 — NFR7's standing-thresholds claim is unsupported.**
`standing.py:20–22` still carries `PROVISIONAL — calibrated in
subproject 4` on all three thresholds (verified), and
`docs/ghmem-heat-weights.md` covers heat weights only. NFR7 as written
also contradicts the "Formula constants are calibrated" decision on
the heat weights themselves: NFR7 says they "are derived by a recorded
analysis", the decision says the provisional vector was **retained**
because nothing fitted ranked better. Separately, `BATCH_ITEMS`,
`BATCH_CHARS` and `DIFF_BODY_MAX_CHARS` are still provisional and the
living doc records neither their values nor that status, where the
deleted spec did. A living doc asserting calibration is done retires a
real open item silently.

**M5 — FR3's "The note reappears in the item index `ghmem topic`
emits" is false for `no-signal`.** `routed_items` excludes
`NO_SIGNAL` (`dispatch_store.py:264`, `:352`), so a `no-signal` note
appears in no rendered output. FR3's paragraph puts the sentence
directly after the `no-signal` clause, so it reads as covering both.

**M6 — two irreconcilable PR counts in one document.** FR1 (`:20`)
says 1,245 PRs (2026-07-09); the diff-measurement paragraph (`:412`)
says 1,299 (2026-07-10). The corpus holds 1,302, so 1,299 is right and
FR1's figure is ~4.5% low. Both were inherited, but the restructure
put them in one document with no reconciliation.

**M7 — five of nineteen requirements are verbatim restatements of
design decisions below them**, and this is the largest single
contributor to the 656-line length:

| requirement | duplicated by |
| --- | --- |
| FR9 `:64–67` | "Backfill: full coverage, heat-ranked order" `:390–392` |
| NFR2 `:93–95` | "12-factor split" `:394–397` |
| NFR3 `:97–100` | "Triage and synthesize are custom agents" `:435–438` |
| NFR4 `:103–105` | "Triage agents pull their own items" `:466–484` |
| NFR7 `:116–118` | "Formula constants are calibrated" `:515–517` |

FR7's `unroute` content is additionally restated by the architecture
bullet (`:218–229`), the emptied-topic decision (`:504–513`) and four
Rejected-alternatives entries — and again in the changelog. This is
how M4's false calibration claim got past review twice: the same claim
appears in two places and neither prompted a check.

**M8 — the document violates its own NFR9 and states the cap
absolutely.** NFR9: "400 lines is the cap on any human-reviewed
artifact, plans and PRs alike; anything longer is split. Sole
exception: PRs that merely reorganize code with no behavioral change."
`docs/ghmem-design.md` is 656 lines and is not such a PR. Either
restate NFR9 as the soft reader-context budget the project actually
applies, or reduce the doc. See "The size question" below.

**M9 — NFR10's ground-truth gate has no recorded run.** NFR10 frames
the wrapindent comparison against `docs/softwrapcolumn-vs-wrapindent.md`
as the precondition for trusting the pipeline. Meanwhile 95 items are
routed and five topic docs are synthesized (`docs/design/` holds
autocompletion, clipboard-behavior, debian-packaging,
file-tree-sidebar, lsp-support — no wrap topic), and the changelog's
Slice records list has no subproject-4 entry. Either the gate ran and
is unrecorded, or the pipeline is being trusted ahead of its own
stated gate. A living design doc should mark an unmet NFR as unmet.

**M10 — the changelog breaks its own stated contract on its newest
entry.** The file opens "each entry below points at the spec or plan
written at the time". The 2026-09-02 `unroute` entry points at
nothing; `plans/unroute-event/requirements.md` is never named. It is
also formatted as an H2 below the "Slice records" bullet list rather
than as a bullet in it, so the file now carries two entry formats.

**M11 — two design changes in this window have no changelog entry at
all:** the packet topic list (`92d1283`), which changed the triage
packet contract and is described in Architecture §1, and the
design-doc migration itself (`f3d1276`). The changelog's stated scope
is "one entry per dated record of a change to ghmem's design".

**M12 — `plans/ghmem-design-migration/report.md:152–158`: the
verification grep does not reproduce.** The report prints the command
and says "Returns nothing." Run verbatim it returns three files
(`handoff-task.md`, the report itself, `classification.md`). None is a
broken link, so reference integrity is genuinely fine — but the stated
evidence is the class of claim a later session leans on without
re-running. The same paragraph's "Thirteen files were patched" also
miscounts: `git show f3d1276 --stat` shows 9 files under
`docs/superpowers/` plus the README, and the `plans/unroute-event/`
files were not patched. The commit message inherits the miscount.

## Minor Findings

**Requirement-statement quality (`docs/ghmem-design.md`)**
- `:52` FR7 is titled "three distinct ways" and names four mechanisms,
  the fourth (`requeue`) explicitly not a verdict correction.
- `:120–128` NFR8 bundles ~10 independent toolchain choices into one
  requirement; not singular.
- `:31` FR3 "triage assigns every item a verdict" is an end state, not
  a property currently true: 95 of the corpus's 3,877 issues and PRs
  carry a verdict.
- `:43–46` FR5's "structural guard" overstates a prompt-level rule;
  nothing in the harness enforces citation backing.
- `:39` and `:286` — FR4 and Architecture §2 both spell out the
  FR/NFR/Limitations/Open questions/History structure; §2 suffices.
- FR4 asserts `INDEX.md` "is never agent-edited" as a system property,
  but the guarantee is a prompt line
  (`.claude/agents/ghmem-synthesize.md:85`).
  `scope-docs-design.sh` scopes writes to `docs/design/`, which
  contains `INDEX.md`, so a Write to it passes the hook.

**Accuracy**
- `:243–245` the `ghmem topic` item-index field list omits the comment
  count `topic.py:110` renders.
- `:238` the status listing omits `total`.
- `:217`, `:457–464` — the doc never records that `_read_note`
  (`cli.py:285–286`) rejects a multi-line note, though the whole
  heredoc apparatus exists to carry prose.
- FR1's scale figures are stamped 2026-07-09 and have drifted (corpus
  now 2,575 issues / 1,302 PRs / 3,529 commits / 245 discussions).
  Stamped, so not wrong — but a living doc can read these off the
  corpus.
- `tools/ghmem/README.md:8` "`docs/ghmem-design.md` at the repository
  root" reads as though the file sits at the root; it is in `docs/`.
  Inherited phrasing from the line it replaced.

**Citations and figures**
- `report.md:139` "No line exceeds 72 characters" is off by 4 lines
  (max is 76).
- `reduction-report.md:5` "1,890 lines down to 88";
  `wc -l memory/ghmem-project.md` reports 90. The 3,999-byte figure is
  exact.
- `reduction-report.md:50–66` line-range citations into the design doc
  run 1–2 lines low throughout, consistent with being written against
  a slightly earlier draft. Each is a pointer a later session follows.
- `reduction-report.md:50` packs twelve command names into one
  304-character table cell.

## Gap Analysis

| requirement | status | evidence |
| --- | --- | --- |
| Old spec retired, no stub | covered | `f3d1276`; 496 lines deleted |
| All inbound references repointed | covered | 10 ref lines across 9 files; grep clean |
| Ten drift items corrected | covered | all ten spot-verified against the deleted spec and current code |
| Design doc carries only current state | covered | no draft archaeology found; genuine alternative-rejections correctly kept |
| Changelog carries only history | **partial** | M10, M11; the `unroute` entry restates design (M7) and asserts unfinished work as done (C1) |
| Newly authored FR1–FR9 | **partial** | FR2 (M3), FR3 (M5), FR4 (minor), FR7 (minor); FR1 figure stale (M6) |
| Newly authored NFR1–NFR10 | **partial** | NFR7 unsupported (M4), NFR9 self-violated (M8), NFR10 unmet and unmarked (M9) |
| Architecture matches the CLI | **partial** | C4, C5; the `routing_reduce.py` split introduced **no** drift — Architecture describes commands, not modules |
| README consistent with the doc it now points at | **missing** | C2, C3 |

## Verified clean

Recorded so a later pass does not re-litigate them.

- **Every corpus figure checks out exactly** against
  `corpus/cache.sqlite`: 95 routes, 71 topics, 57 singletons, 4
  `no-signal`, 0 empty `Topic` rows, 22 routes across the two named
  sessions (12 + 10). Also verified: the 1,802-character topic list,
  `BATCH_CHARS = 200_000` (`budget.py:17`), the 36.5% base rate traced
  to `docs/ghmem-heat-weights.md`, and the `ingest.py` 111 /
  `routing_reduce.py` 328 / `dispatch_store.py` 355 line counts.
- **Reference integrity is clean.** No dangling link to the old spec.
- **Hard wrapping is honest, not crammed.** `ghmem-design.md` max 76
  columns, 4 lines over 72, 0 over 80. The changelog is at 72 exactly.
  `reduction-report.md`'s 36 over-80 lines are all table rows.
- **The changelog/design split is clean** apart from the `unroute`
  entry. No self-referential draft archaeology anywhere in the design
  doc. The thirteen genuine rejections of design alternatives
  (`:576–651`) are decisions with standing rationale — **do not cut
  them** in any reduction pass.
- **`requirements.md` is the strongest document in the set.** Q-1..Q-4
  all check out against the code and the corpus, including the
  hook-permits-either-form claim (verified by running the hook) and
  the zero-empty-`Topic`-rows claim. Its FR-6 is unmet, which is
  correct for a requirements document stating intent — and is the same
  fact C1 wrongly reports as done.

## The size question

`Design decisions` runs `:373–573` (201 lines); `Rejected alternatives`
`:574–651` (78). 279 together, leaving ~377 — the seam
`report.md:148–150` names.

**Recommendation: do not take that seam; deduplicate instead.** The
split would separate duplicated content rather than remove it (M7), so
a reader would then have to check two documents to know whether the
copies agree — and this review found two copies already carrying the
same false claim (M4), unnoticed precisely because they duplicate.
Removing the five duplicated requirements and folding the architecture
bullets' inline rationale into the decisions they repeat is worth
roughly 100–150 lines, and unlike a split it removes the drift surface
instead of doubling it. If a split is taken anyway the named seam is
the right one — but do the deduplication first either way, because it
is where the review burden actually comes from.

This supersedes the "keep it whole" recommendation on file in the task
frame only on its reasoning, not its conclusion: the doc stays one
file, and it gets shorter by losing duplication rather than by moving
rationale away from what it justifies.

## Summary

**5 Critical · 12 Major · 15 Minor.**

The mechanical half of the migration is sound and verifiable: the old
spec is retired cleanly, every reference resolves, all ten claimed
drift corrections hold, the wrapping is honest and every corpus figure
in every document is exact.

The newly authored requirements are where the defects are, and they
divide in two. Five are statements about the CLI that are simply false
(C4, C5, M3, M5, and FR1's PR count) — three of them inherited
verbatim from the deleted spec, which means the ten-item drift sweep
found ten of at least thirteen. The rest are requirements that assert
a property the system does not have (M1's invariant, M4's calibration,
M9's validation gate) or that the document itself breaks (M8). C1 is
the one finding about the world rather than the documents, and it is
the most consequential: the changelog reports finished a batch that
has not started.

**Two things are worth doing before anything else:** correct C1, since
a false completion record in the durable history is what a later
session acts on; and put a Limitations / Open questions section into
the design doc (M2), because M1, M4, M9 and the pending batch all
exist today with nowhere in the living doc to be recorded.

Layer 1 detail: `deliverable-review-prose.md`.

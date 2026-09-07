# ghmem changelog

One entry per dated record of a change to ghmem's design. The living
design is `docs/ghmem-design.md`; each entry below points at the spec,
plan or commit written at the time, which stays frozen as the
write-time record of its own reasoning.

## 2026-07-09 — design approved, split into four subprojects

The work was split into four subprojects, each planned **just-in-time
and from scratch** in a fresh session: a subproject's plan is written
only after its predecessor has landed, derived from the design plus
the actual code and nothing else.

1. **corpus** — scaffold, direnv-activated venv, fetch and routing
   ledgers with their SQLite mirror, `sync` including PR diffs,
   standing derivation, `show`.
2. **dispatch** — `heat`, session-budgeted `triage`/`route` with
   reclaim, `status`, `topic`/`close`, `index`.
3. **agents** — the two agent definitions, the allowlist hook, the
   `docs/design/` scaffold.
4. **validation** — the wrapindent ground-truth gate, formula
   calibration, the backfill runbook.

Each plan opens with a design-fidelity audit of its slice and is
contract-style: interface contracts, test lists and genuinely tricky
algorithms, not full code listings — Sonnet implementers under the
toolchain gates and one-tier-up review fill in the rest — and the
human reviews the plan itself.

## Slice records

- **2026-07-11 — corpus Part 1, the store.** Ledgers, the SQLite
  mirror and the write seam.
  `docs/superpowers/plans/2026-07-11-ghmem-corpus-store.md`
- **2026-07-11 — corpus Part 2, the read commands.** `show`, the
  standing derivation and the rendered thread format.
  `docs/superpowers/plans/2026-07-11-ghmem-corpus-read.md`
- **2026-07-11 — corpus Part 3, sync.** GraphQL fetch, nested cursors,
  resumability and the REST diff pass.
  `docs/superpowers/plans/2026-07-11-ghmem-corpus-sync.md`
- **2026-07-15 — corpus, discussions.** `ghmem discussion` and the
  disjoint discussion number sequence.
  `docs/superpowers/plans/2026-07-15-ghmem-corpus-discussions.md`
- **2026-07-17 — dispatch Part A.** `heat`, the routing-ledger
  foundation and `status`.
  `docs/superpowers/plans/2026-07-17-ghmem-dispatch-heat-status.md`
- **2026-07-21 — dispatch Part B1, the triage session loop.** The
  queue query, session tokens, the metered `triage`/`route` pair.
  `docs/superpowers/plans/2026-07-21-ghmem-dispatch-triage.md`
- **2026-07-22 — dispatch Part B2, solo-item fit.** The serve gate's
  first-item exception and the `fit`/`diff_cut`/`oversized` dispatch
  tags.
  `docs/superpowers/specs/2026-07-22-ghmem-b2-solo-item-fit-design.md`,
  `docs/superpowers/plans/2026-07-22-ghmem-dispatch-b2-fit.md`
- **2026-07-23 — Part C1, `ghmem index`.** INDEX.md regenerated from
  the topic docs' frontmatter plus the routing mirror, with
  corpus-resolved citation numbers.
  `docs/superpowers/specs/2026-07-23-ghmem-c1-index-design.md`
- **2026-07-24 — Part C2, `ghmem topic` and `ghmem close`.** Snapshot
  tokens and the close-out compare-and-set.
  `docs/superpowers/specs/2026-07-24-ghmem-c2-topic-close-design.md`,
  `docs/superpowers/plans/2026-07-24-ghmem-c2-topic-close.md`
- **2026-07-24 — subproject 3, the agentic layer.** The two agent
  definitions, the containment allowlist hook and the heredoc note
  protocol. `docs/superpowers/specs/2026-07-24-ghmem-agents-design.md`
- **2026-07-25 — heat-weight calibration.** A stratified read-only
  sample fit against the provisional weights, which were retained: no
  fitted vector ranked better, and the fitted one lost under
  cross-validation. `docs/ghmem-heat-weights.md`, and the tail sample
  that motivated it, `docs/ghmem-tail-sample.md`
- **2026-08-11 — dispatch Part D, re-entry on corpus change.** Routes
  carry the item's `updated_at`, `ghmem rebuild` appends a `reenter`
  event for every route the corpus has moved past.
  `docs/superpowers/specs/2026-08-11-ghmem-d-reentry-design.md`,
  `docs/superpowers/plans/2026-08-11-ghmem-d-reentry.md`
- **2026-09-02 — the triage packet carries the topic list.** Every
  packet names every topic minted so far on a `topics:` line, and the
  line sits inside the budgeted body rather than the free header, so
  an oversized first item's diff allowance is computed over a floor
  that already carries it. The triage agent had been told to check a
  list no packet ever carried, and two concurrent sessions duly minted
  two fresh clipboard topic names beside a `clipboard-behavior` that
  already existed. No separate spec or plan: commit `92d1283` is the
  write-time record, and it also records why the
  delta-since-last-serve form was deferred.
- **2026-09-02 — the `unroute` event.** `ghmem unroute NUMBER...`
  withdraws a verdict that was reached *wrongly*, where `rebuild`'s
  `reenter` covers one the corpus has merely moved past. A new
  `unroute` member of the routing-event union carries the item's id;
  its reducer branch deletes the item's `Route` row along with its
  `Dispatch` row, and deletes a `Topic` row the withdrawal leaves
  empty. `plans/unroute-event/requirements.md`

  The batch it was built for is the motivating case, not a landed
  one: the 22 items routed by sessions `BcbBJeUg` (12) and `MZlH9vbE`
  (10) were triaged before the packet carried a topic list, and are
  to be unrouted and re-triaged by workers that see every topic
  minted so far. The corpus stood at 95 routes over 71 topics, 57 of
  them singletons, when the command landed. Whether the batch has
  since run is a question for the ledger — `grep -c '"unroute"'
  corpus/routing.jsonl` from the repository root answered `0` when
  this entry was written.
- **2026-09-03 — `docs/ghmem-design.md` becomes the living design
  doc.** The design left
  `docs/superpowers/specs/2026-07-09-ghmem-design.md`, a dated
  slice-spec name in a directory whose other entries freeze after
  their slice, so its being corrected on every slice read as an
  anomaly rather than the rule. This file took over indexing the
  frozen slice records it used to absorb. The doc was restructured
  into requirements, architecture, decisions and rejected
  alternatives; ten places where it had drifted from the code were
  corrected; and FR1–FR9 and NFR1–NFR10 were newly authored, the
  source having had no requirements section at all.
  `plans/ghmem-design-migration/report.md`
- **2026-09-07 — `route` deletes a topic it empties.** The reducer's
  `route` branch now releases the topic the item leaves, converging on
  `unroute`: it captures the pre-merge verdict, replaces the `Route`,
  and then dirties the departed topic if another route still names it
  or drops its `Topic` row if none does. The invariant that a `Topic`
  row exists exactly while some `Route` carries its name as a verdict
  other than `no-signal` therefore holds unconditionally, where it
  used to hold on the `unroute` path alone and left an empty dirty row
  reachable — a name `status` would dispatch a synthesis worker at and
  `topic.assemble` would then refuse with `TopicNotRoutedError`.

  The one-line framing hides two consequences worth stating. A route
  to `no-signal` from a topic's last item drops that topic too:
  `no-signal` names no topic, so nothing re-mints it. And a same-topic
  re-route — reachable only through re-entry, since `record_verdict`
  refuses a second live verdict — dirties rather than drops, because
  the replaced route still names the topic once the merge has flushed.

  `_mark_topic_dirty` became a conditional upsert in the same change.
  A get-then-add leaves a cross-process mint race open, and that
  window used to close permanently once a topic existed; a topic that
  can now be emptied and dropped reopens it every time one is, so the
  fix belongs with the rule that drops it rather than after.

  The sibling docs moved with it: `docs/ghmem-decisions.md` retitles
  the invariant to **Emptying a topic deletes its `Topic` row** and
  gains two entries, one of them backfilling the reservation decision
  behind `reserve_dispatch` that the docs had recorded nowhere;
  `docs/ghmem-design.md` drops the Open question this settles and
  gives FR7 a topic-lifecycle clause; `docs/ghmem-rejected.md` records
  the overruled argument for leaving a re-routed topic's emptied row
  dirty. The cost taken on knowingly is an orphaned
  `docs/design/<name>.md` — nothing detects one, and the sweep that
  would is a named follow-up rather than part of this change.

  The live mirror was checked against the new rule at write time and
  already agreed: 95 routes over 71 topics, 57 of them singletons, and
  no `Topic` row that no `Route` names — so no `rebuild` was needed.
  `plans/reroute-empty-topic/runbook.md`

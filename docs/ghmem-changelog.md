# ghmem changelog

One entry per dated record of a change to ghmem's design. The living
design is `docs/ghmem-design.md`; each entry below points at the spec
or plan written at the time, which stays frozen as the write-time
record of its own reasoning.

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

## 2026-09-02 — the `unroute` event

`ghmem unroute NUMBER...` withdraws a verdict that was reached
*wrongly*, where `reenter` covers one the corpus has merely moved
past. A new `unroute` member of the routing-event union carries the
item's id, and its reducer branch deletes both the item's `Route` row
and its `Dispatch` row.

Deleting the route is what distinguishes it from `reenter`, which
deletes only the dispatch. Three independent freshness gates each
exclude an item carrying a fresh route — the untriaged-heat query
never serves it, `stale_routes` never reenters it, and
`record_verdict` raises `already_routed` on it — and with no route row
at all each gate passes on the absence rather than on a relaxed
comparison. No existing guard was weakened.

Decisions taken with it:

- **Positional issue/PR numbers, variadic, at least one required.**
  The command names items the way `show`, `triage` and `route` do. A
  batch's session identity is a property of the correction job, not of
  the command; a `--session` selector stays additive if a second
  caller ever appears.
- **The reducer deletes a `Topic` row the unroute leaves empty.** A
  topic row exists exactly while some route carries its name, so
  `status` stops listing an emptied topic and `topic.assemble` is
  never dispatched for a name it would refuse. The step is scoped to
  `unroute`; `route` keeps its behavior, since a re-routed item is
  still in the corpus under another name and the emptied topic's doc
  has a rewrite to receive. Nothing removes `docs/design/<name>.md`: a
  filesystem delete inside a replayed reducer would make `rebuild`
  destructive.
- **No containment change.** `unroute` clears the dispatch along with
  the route, so a worker cannot use it to escape a rejected verdict —
  its only way back is the metered queue. Singling it out in a hook
  that already permits `requeue` and `rebuild` would be theatre.
- **Every verdict is treated alike, `no-signal` included.** Refusing
  to unroute `no-signal` would make it the one verdict that can never
  be corrected, and at 4 of 95 routes against a 36.5% calibrated base
  rate it is the population most likely to need correcting.

The batch it was built for: the 22 items routed by sessions `BcbBJeUg`
and `MZlH9vbE`, triaged before the packet carried a topic list, are
unrouted and re-triaged by workers that now see every topic minted so
far. The corpus stood at 95 routes over 71 topics, 57 of them
singletons, when the change was made.

# ghmem design decisions

Why each mechanism in `docs/ghmem-design.md` has the shape it has.
Read it before changing one: a requirement or a command contract that
looks arbitrary has its argument here. What was considered and refused
is in `docs/ghmem-rejected.md`; the command contracts themselves are in
`docs/ghmem-harness.md`.

## Design decisions

**Doc form: hybrid, native-memory shaped.** `INDEX.md` is the recall
surface and the topic docs hold the debate history — the same
index-plus-detail-files pattern as Claude's memory system, so agents
load cheaply and drill down only when relevant, while to a human it
reads as an ordinary catalog. An index line is a retrieval hook rather
than a title: "wrapindent stalled: option-count aversion +
indent-vs-wrap axis confusion → topic doc", not "PR #3107
discussion".

**Naming: `docs/design/`, flat, "topic doc".** What an upstream
contribution would naturally be called, and flat because ~40–60 topic
files need no subdirectory.

**Heat-ranked backfill order.** Hottest first is what makes the run
abortable at any point with the best material banked, which a
chronological or arbitrary order would not be.

**12-factor split.** A model that never has to count, page or resolve
a race cannot get those wrong, and every one of them is testable
without a model in the loop — which is why fetch, store, render,
rank, meter and ledger all sit in the CLI and agents only judge.

**PR diffs are corpus data.** The code change is as informative as the
discussion around it: it shows what was actually accepted or rejected,
not just what was argued. `sync` fetches each PR's diff alongside its
threads, `show` renders it after the discussion, and diff text counts
toward the triage packet budget like any other content. Measured
2026-07-10 against the live API, when the corpus held 1,299 PRs: the
median PR touches 16 lines, p99 ≈ 1.6k, and only 10 PRs exceed 2k
changed lines — a tail of vendor and generated-file churn (#1392 "Go
modules" is +228k lines) whose diff bodies carry no decision signal
beyond the file list, so budget-fit truncation loses nothing.

**A diff body is the only cuttable part of a packet**, and it is cut on
two independent grounds. `DIFF_BODY_MAX_CHARS` is a safety valve on
`show` itself, so a single vendor dump cannot swamp a terminal or a
context window, and it applies whether or not a batch is in progress.
The batch budget cuts further, and only for a session's first item —
which is always served, whatever its size, because the guarantee worth
having is that the queue advances, not that every packet fits. Thread
text is never cut and the file list always survives, so where even those
exceed the budget alone the item is served with no diff body at all.
Every dispatch records which of the three happened — `fit`,
`diff_cut`, `oversized` — in its **ledger event**, so the budget can
be calibrated against that tag. Those tokens are ledger vocabulary and
do not reach the packet: a truncated packet carries a prose
`packet-note:` line naming what was cut, and a packet that fit carries
no note at all.

**Model tiering.** Sonnet for triage, which is bulk classification;
Opus for topic-doc synthesis, where prose quality is the product.

**Triage and synthesize are custom agents, not a skill and a prompt.**
`.claude/agents/` is file-watched, so editing a definition takes
effect on the next spawn with no restart, and `model`, `tools` and
`hooks` are frontmatter fields — which is what lets containment be
attached to the agent rather than to the session that spawns it.

**The allowlist is a positive character class over the whole command
string.** None of ``; & | $ ` < > ( )`` or newline is in the permitted
class, so chaining, substitution, pipes and redirects are excluded by
construction rather than enumerated. One shape is permitted past it
whole: a heredoc whose delimiter is quoted, which is how a note
reaches `route`. Quoted means bash expands nothing in the body, so an
arbitrary body is data; the only thing that could turn it back into
instructions is the delimiter line appearing early, which the hook
forbids. The delimiter is held to letters, digits and underscores —
single quotes would in fact make any byte but the quote itself inert,
but that safety would be a proof about quoting rather than something
visible in the pattern, and nobody needs a `$` in a delimiter. The
writer picking a delimiter their own note does not contain is the same
guarantee a fixed word would give, without excluding a word the note
may legitimately use. The synthesize agent carries a second hook
scoping its Write and Edit calls to `docs/design/`.

**A note is prose, so it travels on stdin alone.** Prose carries the
characters a shell reads as instructions and a quoting mistake corrupts
a note silently — as one already has — so the quoted heredoc above
is the one call form, and it is the safe one. Every verdict requires a
note, `no-signal` included: deciding an item encodes no decision is as
much a judgement as routing it, and the note is what makes that
judgement reviewable.

**Triage agents pull their own items; the CLI meters the batch.** The
orchestrator spawns a triage agent with no item content, so item text
never appears in orchestrator context, and one command per item means
the agent spends about one turn on each. Why the budget has two limbs:
fixed spawn overhead (~10–15k tokens) amortizes over the batch, so
per-item cost bottoms out around 5–12 items under prompt-cache
pricing, while the quality ceiling — context rot — scales with
accumulated *tokens*, not item count, and heat-ordered triage hands out
the largest threads first. Hence a character budget, not an item cap
alone. Packet size is metered in **characters**, the cheap proxy for
tokens: lines vary wildly in length, especially in GitHub-flavored
markdown, and ~4 chars/token is close enough to calibrate against. Exact
counting via the Anthropic token-counting API is free but needs an API
key and a network hop — a possible later precision upgrade.

**Only about a third of closed PRs cleanly encode a design decision.**
That is the literature's central warning, and it is why `no-signal` is
a first-class verdict a triage agent is told to reach comfortably.
Blanket ingestion hallucinates rationale.

**Freshness is stamped on the route.** Each verdict records the item's
`updated_at` at the time it was reached, which is what lets `rebuild`
tell an item the corpus has moved past from one that is simply routed.
That distinction is what makes re-entry automatic, and what keeps
`reenter` and `unroute` from collapsing into one event.

**Emptying a topic deletes its `Topic` row.** An emptied topic would
otherwise sit in `status`'s dirty set as a name `ghmem topic` then
refuses with `TopicNotRoutedError`, stalling the synthesis loop on a
name the corpus itself handed out. The invariant "a `Topic` row exists
exactly while some `Route` carries its name as a verdict other than
`no-signal`" therefore holds unconditionally: withdrawing a topic's
last verdict, re-routing that item to another topic, and routing it to
`no-signal` all leave the topic empty, and all release it. Nothing
removes `docs/design/<name>.md` — the reducer writes SQLite and is
replayed by `rebuild`, so a filesystem delete inside it would make
replay destructive.

The cost this rule knowingly takes on is an orphaned doc. A topic
emptied by a re-route leaves `docs/design/<name>.md` on disk, `ghmem
index` still lists it, and no dirty row carries the rewrite the moved
item would have earned it — strictly worse than the `unroute` case
this generalizes, where the item returns to the queue carrying no
verdict, so the doc is merely unaccompanied and a later re-triage may
re-mint the topic. No surface reports either state: `build_status`
reads the store and the fetch ledger, never the filesystem.

**A live reservation is one conditional insert, never a
read-then-write.** `Dispatch` is keyed on `item_id` alone, so two
triage sessions can race for the same item, and the loser must *learn*
it lost rather than silently overwrite the winner. A read-then-write
cannot enforce that: the `sqlite3` driver opens a transaction before a
write but not before a read, so both sessions can read "free" and both
write. `INSERT ... ON CONFLICT (item_id) DO NOTHING` collapses the
check and the write into SQLite's single-writer transaction, leaving
no window between "checked" and "written". The design-significant part
is the asymmetry: the insert is conditional on the live path while
`apply_routing_event`'s `dispatched` branch is an unconditional
`session.merge`, because replay re-applies events that already
happened and must never decline one on a technicality, whereas the
live path *is* the technicality. The ledger append sits between the
insert and the commit inside the same transaction, so SQLite's writer
lock serializes it as a side effect of the insert.

**`_mark_topic_dirty` is a conditional upsert.** Same reasoning as the
reservation above, pointed at rather than restated: a get-then-add
lets two workers routing different items into the same
not-yet-existing topic both read `None`, both queue the `INSERT`, and
the loser fail the primary key on flush. Because the reducer runs
before the ledger append, the loser's verdict never lands at all,
dying with an opaque `IntegrityError` rather than a
`TriageSessionError`. That window used to close permanently once a
topic existed; now that a topic can be emptied and dropped, it reopens
every time one is, which is why the fix belongs with the rule above
rather than after it.

**Calibration normalizes before it weights.** A calibration that does
not normalize each metric's scale before it touches a weight tunes the
wrong knob: a weight does not set a metric's influence when the
metrics differ ~40× in range. Reactions reach 432 across the corpus
while related-item counts reach 19, so at the shipped weights the
metric weighted highest (related items, 3.0) contributes 14% of the
top scores and the one weighted lowest (reactions, 0.5) contributes
33%. Sparsity compounds it — most items score zero on most metrics.
Ground-truth checks sit alongside the distributions: dmaluka and
JoeKar must derive as maintainers in the right eras, and known-hot
threads like #93 and #3127 must rank in the top percentile.
Reweighting reorders topic-name minting, which `record_verdict` makes
permanent, so the bar for changing a weight is a demonstrated gain and
not a better point estimate — which is why the 2026-07-25 fit ended
in retaining the provisional vector.

**Topic-doc structure: FR / NFR / Limitations / Open questions /
History.** My human partner's format from prior private use, adapted
with descriptive-requirement semantics and an Open-questions section,
and chosen over a pure decision-record shape for its numbered-anchor
citability.

**Contributor standing is computed, time-varying data.** A
maintainer's comment and a random user's identical comment are
different facts, and standing changes over a decade — zyedidia's
authority fading, dmaluka rising to de-facto maintainer around 2022.
GitHub's `author_association` reflects fetch-time association, not
posting-time, so the harness derives `standing(user, date)` — owner,
maintainer, established contributor, newcomer — from the corpus
itself: merge authority before that date (`merged_by` is the hardest
signal), master commit authorship, and own-PRs-accepted count.

**`author_association` is a hint, never authority.** The fetch-time
value is free in the GraphQL query, so `sync` stores it per item and
per comment and renderers show it as a secondary hint on recent items
only — where fetch-time ≈ posting-time, which is exactly the
incremental-update case — never on old ones.

**Incremental synthesis reloads full topic context**, with no delta
machinery. Measured (2026-07-09, live API): micro sees ~8.5k tokens of
new comment text per month across ~22 commented threads, and a full
hot-topic cluster is only ~12k tokens — mega-threads like #93 are
~8–11k, not 50k+. Naive full reload is ≈ 80–150k Opus input tokens per
month against ≈ 60k for delta-only, an immaterial difference that does
not justify the complexity, and full context yields better rewrites.
Revisit only for repos with ~100× micro's traffic.

**One topic per synthesis spawn, no batching.** A topic's context
(~12k tokens for a hot cluster) plus a full rewritten document already
dwarfs the fixed spawn overhead that motivates triage batching, and
prose quality — the point of the Opus tier — is what
accumulated-context rot degrades first.

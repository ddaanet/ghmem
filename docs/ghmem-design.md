# ghmem — reified institutional memory for micro

ghmem turns micro's GitHub history into maintained design documents.
Recurring debates — option-count aversion, clipboard behavior,
highlighter architecture, wrap semantics — get re-litigated in issues
because the outcome of every past debate lives only in a closed thread
nobody re-reads. ghmem fetches that history into a local corpus, has
agents route each item to a topic, and synthesizes one living document
per topic under `docs/design/`: published on GitHub now, offerable
upstream later, and kept incrementally updated.

Its own change history is indexed in `docs/ghmem-changelog.md`.

## Functional requirements

**FR1 — the corpus holds the whole decision record.** `ghmem sync`
fetches every issue, pull request (body, comments, review threads,
review comments, merge metadata, diff), discussion, and the master
commit log into a local store. Scale, verified against the live repo
(2026-07-09): 2,569 issues (862 open), 1,245 PRs (864 merged, 212
closed-unmerged), 3,524 commits over 10.3 years, 243 Discussions.
Every signal the pipeline needs is retrievable — closed-PR review
threads (verified on wrapindent PR #3107), issue↔PR cross-references
via the timeline API, reactions, and date-qualified search for
incremental cursors.

**FR2 — agents read the corpus, never GitHub.** `show`, `discussion`,
`heat`, `status` and `topic` render from the local store. No agent
holds a GitHub credential or issues an API call.

**FR3 — triage assigns every item a verdict.** A verdict is a topic
name plus a routing note — one or two sentences naming the decision
signal seen, who decided what on what grounds — or the reserved
verdict `no-signal`, which also carries a note. The note reappears in
the item index `ghmem topic` emits.

**FR4 — synthesis writes one living document per topic.** Each
`docs/design/<topic>.md` carries YAML frontmatter with a `description:`
hook line and a fixed body structure: FR / NFR / Limitations / Open
questions / History. `docs/design/INDEX.md` is regenerated from those
docs and is never agent-edited.

**FR5 — every requirement in a topic doc is citation-backed.** FRs,
NFRs and limitations cite the issues, PRs or commits behind them; a
requirement with no citable maintainer behavior does not get written.
This is the structural guard against hallucinated rationale.

**FR6 — the pipeline is incremental.** The same `ghmem sync` is the
update path: it fetches only the delta past its cursor. Routing marks
topics dirty, and a run re-synthesizes the dirty set alone.

**FR7 — a verdict can be corrected in three distinct ways.**
Re-routing supersedes a verdict with a better one. `ghmem unroute`
withdraws a verdict reached wrongly, returning the item to the queue.
`ghmem rebuild` appends a `reenter` event for every item whose GitHub
thread has moved past its recorded verdict, so the next cycle
re-judges it. `ghmem requeue` separately frees items a crashed session
dispatched but never routed.

**FR8 — contributor standing is rendered, never inferred.** Every
author in every rendered output carries the standing they held at
posting time, derived from the corpus.

**FR9 — backfill covers the whole corpus in heat order.** Hottest
topics get their docs first, the cold tail is triaged last, and the
run is abortable at any point with the best material banked.
Skeleton-first is the schedule; full coverage is the end state.

### Scope boundaries

- No hosted service, no embeddings or RAG index, no chatbot.
- No mining of Discord, Gitter or mailing lists — that design chatter
  is unretrievable, and it is an accepted blind spot rather than a
  gap to close.
- GitHub Projects data needs a `read:project` token scope and is
  skipped; the wiki would need a separate `.wiki.git` clone to check
  and is presumed unused.
- No automatic upstream PR of the docs. Upstreaming is a later,
  manual decision.
- Topic docs are not exhaustive changelogs. They record decisions and
  debates, not history for its own sake.

## Non-functional requirements

**NFR1 — fetching stays inside GitHub's rate limits.** Full per-item
REST enrichment would be ~7–10k calls against a 5k/hr core limit, so
bulk fetch is GraphQL with nested comment/review/timeline batching.
PR diff text is not exposed via GraphQL, so `sync` takes it from the
REST diff endpoint — ~1 call per PR, ~1.3k total, well inside the
budget. The search endpoint (30/min) serves incremental cursors only,
never enumeration.

**NFR2 — all deterministic work lives in the harness.** Fetch, store,
render, rank, meter and ledger are CLI concerns. Agents only judge:
route items to topics, and write prose.

**NFR3 — the agents' data path is mechanically contained.** A
PreToolUse allowlist hook on each agent definition permits `ghmem`
invocations and denies everything else, so containment does not rest
on the agent following its prompt.

**NFR4 — the CLI meters the batch; a model never counts.** Budget
constants are the CLI's configuration, not orchestrator input, and are
enforced CLI-side against ledger events. The agent loops until the CLI
says the batch or the queue is done.

**NFR5 — the ledgers are append-only and the routing ledger is
committed.** Nothing is edited out: a correction is another append.
`routing.jsonl` is the system's own memory and the one corpus file a
re-sync cannot regenerate.

**NFR6 — backfill fits a Max5 subscription.** The whole corpus is
~3–4M tokens of raw text; one-pass Sonnet triage ≈ 5M input tokens and
Opus synthesis ≈ 3–6M more, which fits across a few 5-hour windows.

**NFR7 — heuristic constants are calibrated, never hip-shot.** Heat
weights and standing thresholds ship as provisional values and are
derived by a recorded analysis over the real corpus.

**NFR8 — the toolchain is held to the strictest bar.** A uv-managed
project, click CLI, pytest (+pytest-mock), `ruff select = ["ALL"]`
with a curated ignore list, `mypy strict` + `extra_checks`,
docformatter, and a justfile `green` recipe. Python floor is **>=3.14**
— its type-annotation improvements make stringified forward references
unneeded and undesirable, and this is developer tooling with no
backward compatibility owed. GitHub access is **githubkit** (typed,
active, built-in retry), with the token from `gh auth token` or
`$GITHUB_TOKEN`.

**NFR9 — 400 lines is the cap on any human-reviewed artifact**, plans
and PRs alike; anything longer is split. Sole exception: PRs that
merely reorganize code with no behavioral change.

**NFR10 — the pipeline is validated against ground truth before it is
trusted.** It runs on the wrapindent cluster (#3107, #2374 and their
cross-references) and the produced topic doc is compared against
`docs/softwrapcolumn-vs-wrapindent.md`, which was validated by hand.
The comparison is purely **semantic** — freshly generated output has
no reason to be structurally similar to a hand-written doc, so a
textual diff is useless; the gate judges whether each required signal
is present and citation-backed. Missing the option-count aversion, the
indent-vs-wrap axis distinction, or the stall rationale means the
prompts are not ready. Triage validation runs against **distractors**:
a mixed queue of ~15–20 items around the cluster, cross-references plus
contemporaneous unrelated bug reports and support questions, where most
items should come back no-signal or other-topic. Routing only the
wrapindent cluster against an empty topic list would make the match
trivial and prove nothing about accuracy. Harness-level, `sync`
resumability and cursor correctness are testable against recorded
fixtures, and `heat`/`triage`/`route`/`status` are pure functions over
the local store, testable without network.

## Architecture

### 1. Harness — `tools/ghmem`

Python/uv, click, githubkit, SQLAlchemy/SQLite. No agent ever calls
the GitHub API; agents read what `ghmem` commands emit from the local
corpus.

- `ghmem sync` — GraphQL-batched fetch (githubkit; explicit nested
  cursors) of all issues, PRs (including review threads and review
  comments, `mergedBy`/`mergedAt`, and fetch-time `author_association`
  as the hint column) and discussions, plus the commit log from the
  local `micro/` clone, into the fetch ledger. PR diff text comes from
  the REST diff endpoint. Cursor on `updated_at`; resumable mid-run;
  rate-limit aware, tracking GraphQL point cost and sleeping or
  exiting cleanly on exhaustion. The same command is the incremental
  update: it fetches only the delta past the cursor.
- `ghmem rebuild` — replays both ledgers into SQLite, then appends a
  `reenter` event for every route whose item's corpus activity has
  moved past the `updated_at` stamped on the verdict, freeing its
  dispatch so it rejoins the triage queue. Because it writes to the
  ledger, it runs between orchestration runs, never mid-session.
- `ghmem show <number>` — one thread rendered as compact markdown:
  body, comments, review threads, the PR diff (truncated when
  outsized, full file list always shown), cross-references, reactions,
  linked commits. The agent-facing read primitive. Authors are
  annotated with their standing at time of posting (e.g. `dmaluka
  (maintainer since ~2022; 180 merges at time of comment)`) from a
  derived standing table; `triage` and `topic` output carries the same
  annotations.
- `ghmem discussion <number>` — the read primitive for GitHub
  Discussions, which carry their own number sequence disjoint from
  issues and PRs (hence a distinct command, not a `show` mode: number
  `5` can name both an issue and a discussion). Renders body, then
  comments interleaved with their one-level replies, the accepted
  answer marked, and reactions — no diff, reviews, merge line or
  cross-references. Discussion activity is display-only and does not
  feed the standing derivation.
- `ghmem heat` — deterministic ranking over the corpus as CSV, hottest
  first: comment count, reaction count, distinct participants,
  cross-reference degree, and recurrence span (years between first and
  last activity). CSV because its only external consumer is analysis,
  which reads the whole distribution and its per-metric columns; the
  in-process consumer is `triage`, which takes the hottest untriaged
  item.
- `ghmem triage` — opens a metered batch session: mints a session
  token and yields the next untriaged item in heat order as a complete
  packet — a `session:` line, a `packet-chars:` header, the id, a
  `topics:` line naming every topic minted so far, and the item's
  `show` rendering. The topic line sits inside the budgeted body, so
  an oversized first item's diff allowance is computed over a floor
  that already carries it. The CLI meters items and characters served
  per token through ledger events, and the budget gate runs **before**
  serving: if the next item's packet would exceed the remaining
  budget, the batch ends with that item still queued — empty stdout,
  `batch complete: budget exhausted` on stderr, exit 0. Triage-complete
  has the same shape (`triage complete: 0 pending`); exit codes are
  reserved for actual errors, and an expected outcome must not read as
  a failure. Yielding records a `dispatched` ledger event, so
  concurrent sessions hand out distinct items.
- `ghmem route <token> <number> <verdict>` — records the verdict in
  the ledger, marks the topic dirty, resolves the dispatch, and
  immediately yields the next packet under the same session (or the
  terminal message): one command per item, so the agent sits in no
  deterministic step. The note arrives on stdin, and only on stdin.
- `ghmem unroute <number>...` — withdraws each named item's verdict,
  returning it to the triage queue. It deletes the item's route and
  its outstanding dispatch together, because three gates each exclude
  an item carrying a fresh route and only the row's absence clears all
  three at once without relaxing any of them. Nothing is edited out of
  the ledger — the superseded `route` event stays, session and note
  included. Every number is resolved against the corpus before any
  event is appended, so a typo late in a batch leaves no half-applied
  correction; a number carrying no verdict is not an error, which is
  what makes re-running a batch a no-op. An operator command, not a
  session one: it takes no token, and a freed item rejoins the queue
  rather than becoming routable on the spot.
- `ghmem requeue` — returns every dispatched-but-unrouted item to the
  queue at once. Run at orchestration-run start, before any agent is
  spawned: serving a stranded item mid-session would hand out a packet
  with no session to route it. It requeues, prints the count, and
  serves nothing.
- `ghmem status` — coverage summary: counts of triaged, routed,
  no-signal, pending, dispatched and stuck items, the full list of
  known topics (so triage can consult it before minting a
  near-duplicate), the dirty subset, and the sync cursor position.
- `ghmem topic <name>` — assembles the synthesis context for one
  topic: an item index — one entry per routed item, a deterministic
  header rendered from the corpus (title, state, dates, author with
  standing, participant and reaction counts, cross-references; for PRs
  the diffstat) plus the **routing note** recorded at triage —
  followed by the **full thread text, diff included, of every routed
  item**. The context is headed by a **snapshot token** minted for this
  assembly and recorded in the ledger with a content digest over the
  topic's routed set.
- `ghmem close <token>` — records synthesis completion, mirroring how
  `route` answers `triage`. Compare-and-set against the token's
  digest: the dirty flag clears only if the topic's routed set is
  unchanged, so routes that arrived between assembly and close-out
  leave the topic dirty for the next run rather than being silently
  discarded. Successful close-out regenerates `INDEX.md`.
- `ghmem index` — regenerates `docs/design/INDEX.md` from the topic
  docs' frontmatter `description:` lines, ordered alphabetically by
  topic name. Alphabetical gives stable diffs; heat ordering would
  churn the whole file on every sync. Each line ends with the
  issue/PR numbers its doc cites, heat-ordered and capped at five —
  the full set stays in the doc, since an index line is a hook and not
  a bibliography. INDEX.md is harness-generated, never agent-edited.

**Storage.** Two append-only JSONL ledgers, mirrored into a derived
SQLite cache for querying. The mirror is defined and accessed through
SQLAlchemy 2.0 typed declarative models (`Mapped`/`mapped_column`,
`DeclarativeBase`), so schema, upserts and row-loading come from the
declarative layer rather than hand-rolled DDL and cursor code. The
**fetch ledger** (`corpus/fetch.jsonl`) records every raw item as
`sync` fetches it — `raw_node`, `cursor` and `fetch_failed` events —
so rebuilding SQLite is a pure local load with no API round-trip. The
corpus is GitHub-regenerable in principle, but that is hours of
rate-limited fetching, and the ledger also feeds test fixtures. It is
gitignored for size. The **routing ledger**
(`corpus/routing.jsonl`) — `dispatched`, `route`, `requeue`,
`reenter`, `unroute`, `snapshot` and `close` events — is committed: it
is the system's own memory and what makes runs reproducible. SQLite
(`corpus/cache.sqlite`) is regenerable from the two ledgers and is
gitignored.

### 2. Artifact — `docs/design/`

- `INDEX.md` — the recall surface, **autogenerated** by `ghmem index`
  from the topic docs' frontmatter, alphabetical by topic.
- `<topic>.md` (flat, beside INDEX.md) — per-theme living documents.
  YAML frontmatter carries `description:`, the hook-style index line
  for this topic. The body has a fixed section structure: **FR / NFR /
  Limitations / Open questions / History**.
  - FRs and NFRs are *descriptive*, not aspirational: requirements the
    project has demonstrably enforced, extracted from maintainer
    statements and merge/reject behavior (clipboard FR "must work over
    ssh without X forwarding (#93)"; project-wide NFR "option-count
    parsimony (#2374, #3107)"). Numbered for stable citation
    (`softwrap.NFR2`) from INDEX.md and future sessions.
  - **Limitations**: accepted trade-offs and known constraints of the
    current design — what it deliberately does not do and the cost the
    project chose to live with. NOT a graveyard of rejected
    approaches: a rejection that encodes a norm becomes an NFR, and
    the debate record for a rejected or stalled approach lives in
    History.
  - **Open questions**: live, unresolved debates and their current
    front line — who holds what position, where it stalled. Keeps the
    most re-litigation-prone material out of History.
  - **History**: compressed chronological debate digest that the other
    sections cite into. Rejected and stalled approaches are recorded
    here with their rejection rationale; rejected and stalled PRs are
    first-class evidence.
  - Norm topics — cross-cutting philosophy like options parsimony —
    are legitimately all-NFR. Empty sections are allowed, and the
    synthesis prompt must not invent filler to populate them.

### 3. Agentic layer — two custom agents

Both are `.claude/agents/` definitions with pinned models and their
full procedure text inline in the definition body; the directory is
file-watched, so an edit takes effect on the next spawn with no
restart. Each carries a frontmatter PreToolUse allowlist hook, and
their only data path is `ghmem` commands over the local corpus.

- **ghmem-triage (Sonnet).** Input: none — no item content, no budget
  numbers. It opens a session with `ghmem triage`, which mints the
  token and serves the first packet, then loops: judge the packet,
  answer with one `ghmem route`, receive the next packet, until the
  CLI reports batch complete or triage complete. Its final report
  leads with the terminal status — `batch complete` (items remain:
  spawn another batch) or `triage complete` (stop) — followed by the
  id → verdict lines, so the orchestrator branches on the first line.
  It may propose new topics, consulting the packet's own `topics:`
  line first to avoid near-duplicates. "No-signal" is a comfortable
  default verdict, not a failure: most items encode no decision.
- **ghmem-synthesize (Opus).** Input: one topic's context from `ghmem
  topic`, with its snapshot token. Output: create or update that topic
  doc — including its frontmatter `description:` hook line — then
  `ghmem close <token>`; the harness regenerates INDEX.md. A stale
  token, meaning activity landed mid-synthesis, is a normal race
  rather than an error to escalate: the same agent re-assembles from
  the fresh snapshot, rewrites the doc and retries, stopping only
  after two consecutive stale tokens. Standing rules with teeth:
  FR/NFR/Limitation entries require maintainer-grade backing — a
  statement by someone with standing at that time, or actual
  merge/reject behavior — and user chorus is demand evidence, never
  policy; History and Open questions attribute positions with
  standing, since a live debate's front line is where people with
  merge power currently stand. Update unit is one topic, so
  incremental runs touch only dirty topics.
- **The incremental loop** is `ghmem sync` → `ghmem rebuild` → `ghmem
  requeue` → triage the delta → synthesize dirty topics. It is fully
  deterministic — agents report terminal status lines precisely so the
  orchestrator needs no judgment — so orchestration can be a
  skill-driven session now and an Agent-SDK script later, running on
  subscription auth and removing the orchestrator model from the loop.
  Scripting it is the natural endgame, not a prerequisite.

### 4. Failure handling

- Rate-limit exhaustion mid-sync: persist the cursor, exit with a
  resume hint, never lose fetched data.
- GraphQL partial failures (a single bad node): log, skip, record the
  id in the fetch ledger as fetch-failed for retry.
- Token scope gaps (Projects): detect, warn once, skip.
- Triage disagreement over time: `route` is append-only in history and
  the latest verdict wins; re-routing marks both the old and the new
  topic dirty. A verdict judged wrong rather than superseded is
  withdrawn by `ghmem unroute` instead — still an append, never an
  edit — which returns the item to the queue for a fresh session.
- Topic close-out race: the digest compare-and-set in `ghmem close`
  leaves the topic dirty rather than discarding newer input, and the
  synthesize agent treats it as a retry.
- Stranded triage dispatch: a `dispatched` event with no route, from
  an agent that crashed mid-item, is surfaced by `status` and
  reclaimed by `ghmem requeue` at the start of the next orchestration
  run — explicit reclaim instead of timeouts, so a slow-but-live
  session is never raced.

## Design decisions

**Doc form: hybrid, native-memory shaped.** `INDEX.md` is the recall
surface — one hook-style line per settled question or live debate,
linking into topic docs — and the topic docs hold the debate history.
That is the same index-plus-detail-files pattern as Claude's memory
system, so agents load cheaply and drill down only when relevant; to a
human it reads as an ordinary catalog.

**Naming: `docs/design/`, flat, "topic doc".** What an upstream
contribution would naturally be called, and flat because ~40–60 topic
files need no subdirectory.

**Index entries are retrieval hooks, not titles.** "wrapindent
stalled: option-count aversion + indent-vs-wrap axis confusion → topic
doc" rather than "PR #3107 discussion".

**Backfill: full coverage, heat-ranked order.** Hottest topics get
their docs first and the cold tail is triaged last, so the run is
abortable at any point with the best material banked.

**12-factor split.** All deterministic work — fetch, store, render,
rank, ledger — lives in the CLI harness; agents only judge. A model
that never has to count, page or resolve a race cannot get those
wrong, and every one of them is testable without a model in the loop.

**PR diffs are corpus data.** The code change is as informative as the
discussion around it: it shows what was actually accepted or rejected,
not just what was argued. `sync` fetches each PR's diff alongside its
threads, `show` renders it after the discussion, and diff text counts
toward the triage packet budget like any other content. A diff body is
the only cuttable part of a packet — thread text is never cut and the
file list always survives — and it is cut on two independent grounds.
`DIFF_BODY_MAX_CHARS` is a safety valve on `show` itself, so a single
vendor dump cannot swamp a terminal or a context window, and it
applies whether or not a batch is in progress. The batch budget cuts
further, and only for a session's first item, when that item alone
exceeds a whole batch. Measured (2026-07-10, live API): the median PR
touches 16 lines, p99 ≈ 1.6k, and only 10 of 1,299 PRs exceed 2k
changed lines — a tail of vendor and generated-file churn (#1392 "Go
modules" is +228k lines) whose diff bodies carry no decision signal
beyond the file list, so budget-fit truncation loses nothing.

**A session's first item is always served, whatever its size.** The
guarantee that buys is that the queue advances — no single item can
deadlock it — not that every packet fits. If a solo packet exceeds a
whole batch budget, its diff body is cut to whatever the budget has
left over the parts that cannot be cut; where even those exceed the
budget on their own, the item is served with no diff body at all.
Every dispatch is tagged with which of the three happened — `fit`,
`diff_cut`, `oversized` — in the packet and in the ledger, so the
budget can be calibrated against that tag.

**Model tiering.** Sonnet for triage, which is bulk classification;
Opus for topic-doc synthesis, where prose quality is the product.

**Triage and synthesize are custom agents.** The two definitions,
`.claude/agents/ghmem-triage.md` and
`.claude/agents/ghmem-synthesize.md`,
carry `model`, `tools` and `hooks` frontmatter with the full procedure
text inline in the definition body: `.claude/agents/` is file-watched,
so editing a definition takes effect on the next spawn with no
restart. The no-GitHub guardrail is enforced mechanically, as an
**allowlist**: a PreToolUse hook in each agent's frontmatter
authorizes only `ghmem` invocations and denies everything else with a
structured reason.

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

**The routing note arrives on stdin, and only on stdin.** A note is
prose, prose carries the characters a shell reads as instructions, and
a quoting mistake corrupts the note silently — as one already has. A
quoted heredoc expands nothing at all, so there is exactly one call
form and it is the safe one. Every verdict requires a note,
`no-signal` included: deciding an item encodes no decision is as much
a judgement as routing it, and the note is what makes that judgement
reviewable.

**Triage agents pull their own items; the CLI meters the batch.** The
orchestrator spawns a triage agent with no item content, so item text
never appears in orchestrator context. `ghmem triage` mints a session
token and returns it with the first packet; from then on a single
`ghmem route` records each verdict *and* yields the next packet, so
the agent spends about one turn per item and sits in no deterministic
step. The batch budget — items and accumulated packet size — is the
CLI's own configuration, metered per token via ledger events and
enforced CLI-side: a model cannot be trusted to count, so it never
does. Fixed spawn overhead (~10–15k tokens) amortizes over the batch,
so per-item cost bottoms out around 5–12 items under prompt-cache
pricing, while the quality ceiling — context rot — scales with
accumulated *tokens*, not item count, and heat-ordered triage hands
out the largest threads first. Hence a character budget, not an item
cap alone. Packet size is metered in **characters**, the cheap proxy
for tokens: lines vary wildly in length, especially in GitHub-flavored
markdown, and ~4 chars/token is close enough to calibrate against.
Exact counting via the Anthropic token-counting API is free but needs
an API key and a network hop — a possible later precision upgrade.

**Only about a third of closed PRs cleanly encode a design decision.**
That is the literature's central warning, and it is why `no-signal` is
a first-class verdict a triage agent is told to reach comfortably.
Blanket ingestion hallucinates rationale.

**Crash recovery is explicit, not timed.** `status` surfaces
dispatched-but-unrouted items and `ghmem requeue` returns them to the
queue, run at orchestration-run start before any agent is spawned. No
timeout, so no race with a slow-but-live session.

**Freshness is stamped on the route.** Each verdict records the item's
`updated_at` at the time it was reached, which is what lets `rebuild`
tell an item the corpus has moved past from one that is simply routed.
That distinction is what makes re-entry automatic and keeps the
correction paths separate: `reenter` frees the dispatch of an item
whose thread changed, `unroute` deletes the route of an item whose
verdict was wrong.

**Unrouting a topic's last item deletes the `Topic` row.** A topic row
exists exactly while some route names it, and an emptied one would sit
in `status`'s dirty set as a name `ghmem topic` then refuses. The step
is scoped to `unroute`: a re-routed item is still in the corpus under
another name, so the emptied topic's doc has a rewrite to receive,
while a withdrawn verdict leaves the name standing for nothing.
Nothing removes `docs/design/<name>.md` — the reducer writes SQLite
and is replayed by `rebuild`, so a filesystem delete inside it would
make replay destructive, and a synthesized doc is prose about items
that really were routed there.

**Formula constants are calibrated, never hip-shot.** Heat weights and
standing thresholds ship provisional and are derived by a recorded
analysis over the real corpus: signal distributions and percentiles,
plus ground-truth checks (dmaluka and JoeKar must derive as
maintainers in the right eras; known-hot threads like #93 and #3127
must rank in the top percentile). That calibration must normalize each
metric's scale before it touches a weight, or it tunes the wrong knob:
a weight does not set a metric's influence when the metrics differ
~40× in range. Reactions reach 432 across the corpus while
related-item counts reach 19, so at the shipped weights the metric
weighted highest (related items, 3.0) contributes 14% of the top
scores and the one weighted lowest (reactions, 0.5) contributes 33%.
Sparsity compounds it — most items score zero on most metrics. The
weights the corpus runs under are the provisional vector, retained:
fitting against 198 labels produced nothing that ranked better, and
the fitted vector lost under cross-validation. The reasoning is in
`docs/ghmem-heat-weights.md`. Reweighting reorders topic-name minting,
which `record_verdict` makes permanent, so the bar for changing one is
a demonstrated gain, not a better point estimate.

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
Rendered outputs annotate every author inline; agents read standing,
never infer it.

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

## Rejected alternatives

**An existing tool.** Nothing on the market fills the need. The
commercial tools — DeepWiki, Swimm, dosu, kapa — document *current
code*, not decision history. Unblocked is closest in spirit but is a
closed hosted index with no exportable docs. Among research
prototypes, DRMiner (ASE 2024, Jira-only, ~65% F1) proves that LLM
design-rationale mining works, and CommitDistill (GitHub-native,
deterministic extraction) is the closest architectural match but
outputs a JSON store rather than prose and does not treat rejected PRs
as first-class. No tool combines GitHub-native, rejected-PR-aware and
incremental-into-versioned-markdown.

**Shelling out to `gh api graphql`.** `--paginate` handles only a
single top-level connection, so nested comment and review pagination
would be hand-rolled either way. githubkit gives typing and retry for
the same work.

**A companion procedure skill for the agents.** A registered skill
injects its description into every session's context and is skipped
silently if it is preloaded and then missing. The procedure text lives
inline in each agent definition instead.

**A deny-pattern hook.** Deny lists both over-block and are trivially
slipped. The allowlist admits a known-good shape and refuses
everything else.

**Blocking `ghmem unroute` in the containment hook.** The escalation
that would motivate it does not exist: `unroute` clears the dispatch
along with the route, so a worker that unroutes an item it was
rejected on immediately fails `record_verdict`'s not-dispatched guard,
and its only way back is to be served the item again through the
metered queue. The residual — a confused worker returning other
sessions' items to the queue — is not a new class of exposure, since
the hook is a command-*shape* allowlist that already permits `ghmem
requeue` and `ghmem rebuild`, both corpus-wide mutating operator
commands. Deciding which subcommands a caller may run needs authority
vocabulary the hook does not have: it sees one command string, and one
copy of it contains both agents.

**A `--session` selector on `unroute`.** Session identity is a
property of a particular correction job, not of the command; the
numbers are derivable once with a query over the routing mirror, and
the command stays in ghmem's one vocabulary of issue/PR numbers.
Adding the selector later is additive, removing it would not be.

**Refusing to unroute a `no-signal` verdict.** That would make it the
one verdict that can never be corrected, and it is the population most
likely to need correcting.

**Relaxing `record_verdict` to accept a live re-route.** The freshness
check is what distinguishes a live re-route from a reentered item's
re-triage. Clearing the route reaches the same goal without touching
that distinction.

**Dispatch timeouts for crash recovery.** A timeout races a
slow-but-live session. Explicit reclaim between orchestration runs
cannot.

**Digest-only tiers in the synthesis context.** The standing rules
need who-said-what verbatim — a two-sentence note cannot back an FR
citation — and triage has already filtered: the routed set is about a
third of the corpus, so full source across all topics is ~1–1.5M input
tokens, inside the backfill envelope.

**Delta machinery for incremental synthesis.** The measured saving is
immaterial against micro's traffic, and full reload yields better
rewrites.

**GH Archive for historical `author_association`.** Historical values
are recoverable only for ~2018 to October 2025 — the field appeared in
event payloads around 2018 and GitHub removed it in October 2025 — and
access means a BigQuery dependency with unverified scan costs.
Computed standing covers the whole decade uniformly.

**Heat-ordered `INDEX.md`.** Heat ordering would churn the whole file
on every sync; alphabetical ordering keeps diffs stable and reviewable.

## Changelog

`docs/ghmem-changelog.md` indexes every dated record of a change to
this design, each pointing at the slice spec or plan written at the
time.

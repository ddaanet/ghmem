# ghmem — reified institutional memory for micro

Date: 2026-07-09
Status: approved (design), pending implementation plan

## Problem

The micro project has no institutional memory. Recurring debates
(option-count aversion, clipboard behavior, highlighter architecture,
wrap semantics) are re-litigated in GitHub issues because the outcomes
of past debates live only in closed threads nobody re-reads. This
project reifies that memory as maintained design documents in this
repo's `docs/design/`, published on GitHub now and offerable upstream
later, and keeps them incrementally updated.

## Research findings (2026-07-09)

**Prior art: nothing fills the need.** Commercial tools (DeepWiki,
Swimm, dosu, kapa) document *current code*, not decision history.
Unblocked (SaaS) is closest in spirit but is a closed hosted index with
no exportable docs. Research prototypes: DRMiner (ASE 2024, Jira-only,
~65% F1) proves LLM design-rationale mining works; CommitDistill
(GitHub-native deterministic extraction) is the closest architectural
match but outputs a JSON store, not prose, and does not treat rejected
PRs as first-class. No tool combines {GitHub-native, rejected-PR-aware,
incremental-into-versioned-markdown}. Key literature warning: only
roughly a third of closed PRs cleanly encode a design decision —
blanket ingestion hallucinates rationale, so triage needs a comfortable
"no-signal" verdict.

**Data-source assumptions verified against the live repo.**
micro-editor/micro has 2,569 issues (862 open), 1,245 PRs (864 merged,
212 closed-unmerged), 3,524 commits over 10.3 years, and 243 GitHub
Discussions. All needed signals are retrievable via `gh`: closed-PR
review threads (verified on wrapindent PR #3107), issue↔PR
cross-references via the timeline API, reactions, and date-qualified
search for incremental cursors. Constraints: full per-item REST
enrichment is ~7–10k calls against a 5k/hr core limit, so bulk fetch
must use GraphQL batching (nested comments/reviews/timeline per query);
the search endpoint (30/min) is for incremental cursors only, never
enumeration.

**Accepted blind spots:** Discord/Gitter design chatter is
unretrievable; GitHub Projects data needs a `read:project` token scope
(skipped for now); the wiki requires a one-time separate
`.wiki.git` clone to check (likely unused).

## Decisions

- **Doc form: hybrid, native-memory shaped.** `INDEX.md` is the recall
  surface (one hook-style line per settled question or live debate,
  linking into topic docs), topic docs hold the debate history. Same
  index-plus-detail-files pattern as Claude's memory system, so agents
  load cheaply and drill down only when relevant; to a human it reads
  as an ordinary catalog.
- **Naming: `docs/design/`, flat, "topic doc".** The artifact is
  `docs/design/INDEX.md` plus flat `docs/design/<topic>.md` files —
  what an upstream contribution would naturally be called, and flat
  because ~40–60 topic files need no subdirectory.
- **Index entries are retrieval hooks, not titles.** "wrapindent
  stalled: option-count aversion + indent-vs-wrap axis confusion →
  topic doc" rather than "PR #3107 discussion".
- **Backfill: full coverage, heat-ranked order.** Whole corpus is
  ~3–4M tokens of raw text; one-pass Sonnet triage ≈ 5M input tokens,
  Opus synthesis ≈ 3–6M more. Fits a Max5 plan across a few 5-hour
  windows. Hottest topics get their docs first; the cold tail is
  triaged last; the run is abortable at any point with the best
  material banked. Skeleton-first is the schedule, full coverage the
  end state.
- **12-factor split.** All deterministic work (fetch, store, render,
  rank, ledger) lives in a CLI harness. Agents only judge: route items
  to topics, and write prose.
- **PR diffs are corpus data.** The code change is as informative as
  the accompanying discussion — it shows what was actually accepted or
  rejected, not just argued. `sync` fetches each PR's diff alongside
  its threads; `show` renders it after the discussion, and diff text
  counts toward the triage packet budget like any other content. A diff
  body is the only cuttable part of a packet — thread text is never cut
  and the file list always survives — and it is cut on two independent
  grounds. `DIFF_BODY_MAX_CHARS` is a safety valve on `show` itself, so
  a single vendor dump cannot swamp a terminal or a context window; it
  applies whether or not any batch is in progress. The batch budget cuts
  further, and only for a session's first item, when that item alone
  exceeds a whole batch — see the serve gate under `ghmem triage`.
  Measured (2026-07-10, live API): the median PR
  touches 16 lines, p99 ≈ 1.6k, and only 10 of 1,299 PRs exceed 2k
  changed lines — a tail of vendor/generated-file churn (#1392 "Go
  modules" is +228k lines) whose diff bodies carry no decision signal
  beyond the file list, so budget-fit truncation loses nothing.
- **Toolchain: strict Python standards; dependencies welcome.** A
  large code drop is held to the strictest bar: uv-managed project,
  click CLI, pytest (+pytest-mock), `ruff select = ["ALL"]` with a
  curated ignore list, `mypy strict` + `extra_checks`, docformatter, a
  justfile `green` recipe. Planners and implementers take the concrete
  config from the author's sibling `edify` project
  (`/Users/david/code/edify/pyproject.toml` and its justfile). Python
  floor is **>=3.14** (its type-annotation
  improvements make stringified forward references unneeded and
  undesirable; this is developer tooling, no backward compatibility
  owed). GitHub access via **githubkit** (typed, active,
  built-in retry), token from `gh auth token` or `$GITHUB_TOKEN`.
  Shelling out to `gh api graphql` was rejected: `--paginate` handles
  only a single top-level connection, so nested comment/review
  pagination would be hand-rolled either way.
- **Model tiering.** Sonnet for triage (bulk classification), Opus for
  topic-doc synthesis (prose quality).
- **Triage and synthesize are custom agents.**
  `.claude/agents/ghmem-triage.md` (model: sonnet) and
  `.claude/agents/ghmem-synthesize.md` (model: opus). Agents carry
  `model`, `tools`, and `hooks` frontmatter, and the full procedure
  text lives inline in each agent's definition body:
  `.claude/agents/` is file-watched, so editing a definition takes
  effect on the next spawn with no restart. A companion procedure
  skill was rejected: a registered skill injects its description into
  every session's context and is skipped silently if
  preloaded-then-missing. The no-GitHub guardrail is
  enforced mechanically, as an **allowlist**: a PreToolUse hook in each
  agent's frontmatter authorizes only `ghmem` subcommand invocations —
  anchored at command start, compound commands (`;`, `&&`, `|`, `$(`,
  backticks) rejected so a `ghmem` prefix can't smuggle a second
  command — and denies everything else with a structured reason.
  (Deny-patterns were rejected: they both over-block and are trivially
  slipped.) The agents' only data path is `ghmem` commands over the
  local corpus. Authoring
  follows plugin-dev:agent-development and plugin-dev:hook-development.
- **Triage agents pull their own items; the CLI meters the batch.**
  The orchestrator spawns a triage agent with no item content — item
  text never appears in orchestrator context. `ghmem triage` mints a
  session token and returns it with the first packet; from then on a
  single `ghmem route <token> <id> <verdict> [note]` records each
  verdict AND yields the next packet, so the agent spends ~one turn
  per item and sits in no deterministic step. The verdict is a topic
  name — accompanied by a short routing note naming the decision
  signal seen, later replayed in `ghmem topic`'s item index — or the
  reserved verdict `no-signal` (forbidden as a topic name). The batch
  budget — items and accumulated packet size — is owned by the CLI
  (its config, not orchestrator input), metered per token via ledger
  events and enforced CLI-side — a model cannot be trusted to count,
  so it never does: it loops until `route` answers `batch complete`
  (budget exhausted) or `triage complete`, then reports id → verdict
  lines. Packet size is metered in **characters**, the cheap proxy for
  tokens (lines vary wildly in length, especially in GitHub-flavored
  markdown; ~4 chars/token, refined by calibration — exact counting
  via the Anthropic token-counting API is free but needs an API key
  and a network hop, a possible later precision upgrade). Rationale
  for batching: fixed spawn overhead (~10–15k tokens)
  amortizes over the batch, so per-item cost bottoms out around 5–12
  items under prompt-cache pricing; the quality ceiling (context rot)
  scales with accumulated *tokens*, not item count, and heat-ordered
  triage hands out the largest threads first — hence a character
  budget, not an item cap alone. Both constants are provisional and
  calibrated against verdict-accuracy-by-batch-position data in the
  validation run. Crash recovery is explicit, not timed: `status`
  surfaces dispatched-but-unrouted items, and `ghmem triage --requeue`
  — run at orchestration-run start, before any agents are spawned —
  returns them to the queue. No timeout, no race with live sessions.
- **Formula constants are calibrated, never hip-shot.** Heat weights
  and standing thresholds ship as provisional values and are derived
  properly by a recorded calibration analysis over the real corpus
  after the first full sync: signal distributions and percentiles,
  plus ground-truth checks (dmaluka/JoeKar must derive as maintainers
  in the right eras; known-hot threads like #93/#3127 must rank in the
  top percentile). Standing rule: heuristic formulas are grounded in
  data analysis. That calibration has a prerequisite the first live
  run made concrete: it must normalize each metric's scale before it
  touches a weight, or it tunes the wrong knob. A weight does not set
  a metric's influence when the metrics differ ~40× in range —
  reactions reach 432 across the corpus while related-item counts
  reach 19, so at the shipped weights the metric weighted highest
  (related items, 3.0) contributes 14% of the top scores and the one
  weighted lowest (reactions, 0.5) contributes 33%. Sparsity
  compounds it: most items score zero on most metrics.
- **Topic-doc structure: FR/NFR/Limitations/Open questions/History**
  (detailed under Artifact below). My human partner's format from
  prior private use, adapted with descriptive-requirement semantics
  and an Open-questions section; chosen over a pure decision-record
  shape for its numbered-anchor citability.
- **Contributor standing is computed, time-varying data.** A
  maintainer's comment and a random user's identical comment are
  different facts, and standing changes over the decade (zyedidia's
  authority fading, dmaluka rising to de-facto maintainer ~2022).
  GitHub's `author_association` reflects fetch-time association, not
  posting-time, so the harness derives `standing(user, date)` — owner /
  maintainer / established contributor / newcomer — from the corpus
  itself: merge authority before that date (`merged_by` is the hardest
  signal), master commit authorship, own-PRs-accepted count. Rendered
  outputs annotate every author inline; agents read standing, never
  infer it.
- **`author_association` is a hint, never authority.** Research
  (2026-07-09): historical values are recoverable from GH Archive only
  for ~2018–Oct 2025 (the field appeared in event payloads ~2018 and
  GitHub removed it Oct 2025), and access means a BigQuery dependency
  with unverified scan costs — rejected; computed standing covers the
  whole decade uniformly. But the fetch-time value is free in the
  GraphQL query, so sync stores it per item/comment and renderers show
  it as a secondary hint on recent items only (where fetch-time ≈
  posting-time — exactly the incremental-update case), never on old
  ones.
- **Incremental synthesis reloads full topic context** (no delta
  machinery). Measured (2026-07-09, live API): micro sees ~8.5k tokens
  of new comment text per month across ~22 commented threads; a full
  hot-topic cluster is only ~12k tokens (mega-threads like #93 are
  ~8-11k, not 50k+). Naive full reload ≈ 80–150k Opus input
  tokens/month vs ≈ 60k for delta-only — an immaterial difference that
  doesn't justify the complexity, and full context yields better
  rewrites. Revisit only for repos with ~100× micro's traffic.

## Architecture

### 1. Harness — `tools/ghmem` (Python/uv, click, githubkit, SQLAlchemy/SQLite)

No agent ever calls the GitHub API; agents read what `ghmem` commands
emit from the local corpus.

- `ghmem sync` — GraphQL-batched fetch (githubkit; explicit nested
  cursors) of all issues, PRs (including review threads and review
  comments, `mergedBy`/`mergedAt`, fetch-time `author_association` as
  the hint column), and discussions, plus the commit log from the
  local `micro/` clone, into the corpus. PR diff text is not exposed
  via GraphQL, so `sync` fetches it from the REST diff endpoint (~1
  call per PR, ~1.3k calls — well inside the 5k/hr REST budget).
  Cursor on `updated_at`;
  resumable mid-run; rate-limit aware (tracks GraphQL point cost,
  sleeps/exits cleanly on exhaustion). The same command is the
  incremental update: it fetches only the delta past the cursor.
- `ghmem show <id>` — one thread rendered as compact markdown: body,
  comments, review threads, the PR diff (truncated when outsized, full
  file list always shown), cross-references, reactions, linked
  commits. The agent-facing read primitive. Authors are annotated with
  their standing at time of posting (e.g. `dmaluka (maintainer since
  ~2022; 180 merges at time of comment)`), from a derived standing
  table rebuilt from corpus data; `triage` and `topic` output carries
  the same annotations.
- `ghmem discussion <number>` — the read primitive for GitHub
  Discussions, which carry their own number sequence disjoint from
  issues/PRs (hence a distinct command, not a `show` mode: number `5`
  can name both an issue and a discussion). Renders one discussion as
  compact markdown: body, then comments interleaved with their
  one-level replies, the accepted answer marked, and reactions — no
  diff, reviews, merge line, or cross-references. Authors carry the
  same standing annotation as `show`; discussion activity is
  display-only and does not feed the standing derivation.
- `ghmem heat` — deterministic ranking over the corpus: comment count,
  reaction count, distinct participants, cross-reference degree,
  recurrence span (years between first and last activity).
- `ghmem triage` — opens a metered batch session: mints a session
  token and yields the next untriaged item (heat order) as a complete
  packet — a `session: <token>` line, a `packet-chars: <n>` header, the
  id, a `topics:` line naming every topic minted so far, and its `show`
  rendering. The topic line sits inside the budgeted body, so an
  oversized first item's diff allowance is computed over a floor that
  already carries it. The CLI meters items and characters
  served per token (ledger events), and the budget gate runs
  **before** serving: if the next item's packet would exceed the
  remaining budget, the batch ends with that item still queued — empty
  stdout, `batch complete: budget exhausted` on stderr, exit 0.
  Exception: a session's first item is always served, whatever its
  size. The guarantee that buys is that the queue advances — no single
  item can deadlock it — not that every packet fits. If that solo
  packet exceeds a whole batch budget, its diff body is cut to whatever
  the budget has left over the parts that cannot be cut, the thread
  text and the file list. Where even those exceed the budget on their
  own, the item is served anyway with no diff body at all. Every
  dispatch is tagged with which of the three happened — `fit`,
  `diff_cut`, `oversized` — in the packet and in the ledger, and
  subproject 4 calibrates the budget against that tag.
  Triage-complete has the same shape (`triage complete: 0
  pending`) — exit codes are reserved for actual errors; an expected
  outcome must not read as a failure. Yielding records a `dispatched`
  ledger event so concurrent sessions hand out distinct items; `route`
  resolves the dispatch. `status` surfaces dispatched-but-unrouted
  items, and `ghmem triage --requeue` — invoked at orchestration-run
  start, before any agents are spawned — returns them all to the queue
  at once (crash recovery without timeouts).
- `ghmem route <token> <id> <verdict> [note]` — records the triage
  verdict (a topic name, or the reserved verdict `no-signal`) in the
  ledger, marks the topic dirty, resolves the dispatch, and
  immediately yields the next packet under the same session (or the
  batch-complete / triage-complete message): one command per item, so
  the agent is out of every deterministic step. A topic verdict
  requires the note — one or two sentences naming the decision signal
  the agent saw (who decided what, on what grounds); it reappears in
  the item index `ghmem topic` emits. `no-signal` takes none.
- `ghmem status` — coverage ledger summary: counts of triaged / routed
  / no-signal / pending / dispatched / stuck, the full list of known
  topics (so triage can consult it before minting a near-duplicate),
  the dirty subset, and sync cursor position.
- `ghmem topic <name>` — assembles the synthesis context for one
  topic: an item index — one entry per routed item, a deterministic
  header rendered from the corpus (title, state, dates, author with
  standing annotation, participant and reaction counts,
  cross-references; for PRs the diffstat — files touched, +/− counts)
  plus the **routing note** recorded at triage — followed by the
  **full thread text, diff included, of every routed item**.
  Digest-only tiers were rejected: the standing rules need
  who-said-what verbatim (a two-sentence note cannot back an FR
  citation), and triage already filtered — the routed set is ~a third
  of the corpus, so full source across all topics is ~1–1.5M input
  tokens, inside the backfill estimate above. The context is headed
  by a **snapshot token** minted for this assembly and recorded in
  the ledger (it binds the topic to its ledger-sequence/`updated_at`
  high-water mark).
- `ghmem close <token>` — records synthesis completion, mirroring how
  `route` answers `triage`. Compare-and-set: the dirty flag on the
  token's topic clears only if the snapshot is still current — routes
  or comments that arrived between assembly and close-out leave the
  topic dirty for the next run, never silently discarded. Successful
  close-out regenerates `INDEX.md`.
- `ghmem index` — regenerates `docs/design/INDEX.md` from the topic
  docs' frontmatter `description:` lines, ordered alphabetically by
  topic name (stable diffs; heat-ordering would churn the file on
  every sync). INDEX.md is harness-generated, never agent-edited.

Storage: two append-only JSONL ledgers, mirrored into a derived SQLite
cache for querying. The mirror is defined and accessed through
SQLAlchemy 2.0 typed declarative models (`Mapped`/`mapped_column`,
`DeclarativeBase`) — schema, upserts, and row-loading come from the
declarative layer rather than hand-rolled DDL and cursor code. The
**fetch ledger** records every raw item as
`sync` fetches it, so rebuilding SQLite is a pure local load — no API
round-trip (the corpus is GitHub-regenerable in principle, but that is
hours of rate-limited fetching; the ledger also feeds test fixtures).
Gitignored for size. The **routing ledger** — verdicts, session and
snapshot tokens, topic list, topic dirty flags — is committed; it is
the system's own memory and makes runs reproducible. SQLite is
regenerable from the two ledgers → gitignored.

### 2. Artifact — `docs/design/`

- `INDEX.md` — the recall surface described above, **autogenerated**
  by `ghmem index` from the topic docs' frontmatter, alphabetical by
  topic. Concretely: every index line ends with the issue/PR numbers
  it rests on, e.g. `(#2374, #3107)`.
- `<topic>.md` (flat, beside INDEX.md) — per-theme living documents.
  YAML frontmatter carries `description:` — the hook-style index line
  for this topic (the "retrieval hooks, not titles" rule applies
  there). Body has a fixed
  section structure: **FR / NFR / Limitations / Open questions /
  History**.
  - FRs and NFRs are *descriptive*, not aspirational: requirements the
    project has demonstrably enforced, extracted from maintainer
    statements and merge/reject behavior (e.g. clipboard FR "must work
    over ssh without X forwarding (#93)"; project-wide NFR
    "option-count parsimony (#2374, #3107)"). Numbered for stable
    citation (`softwrap.NFR2`) from INDEX.md and future sessions.
  - Every FR/NFR/limitation carries citations to issues/PRs/commits.
    A requirement with no citable maintainer behavior behind it does
    not get written — this is the structural guard against hallucinated
    rationale.
  - **Limitations**: accepted trade-offs and known constraints of the
    current design — what it deliberately does not do and the cost the
    project chose to live with. NOT a graveyard of rejected approaches:
    a rejection that encodes a norm becomes an NFR, and the debate
    record for a rejected or stalled approach lives in History.
  - **Open questions**: live, unresolved debates and their current
    front line (who holds what position, where it stalled). Prevents
    the most re-litigation-prone material from being buried in History.
  - **History**: compressed chronological debate digest that the other
    sections cite into. Rejected and stalled approaches are recorded
    here with their rejection rationale; rejected and stalled PRs are
    first-class evidence.
  - Norm topics (cross-cutting philosophy like options parsimony) are
    legitimately all-NFR; empty sections are allowed and the synthesis
    prompt must not invent filler to populate them.

### 3. Agentic layer — two custom agents

Both are `.claude/agents/` definitions with pinned models, their full
procedure text inline in the definition body (the directory is
file-watched, so edits take effect on the next spawn), and a
frontmatter PreToolUse allowlist hook that authorizes
only `ghmem` commands and denies everything else — their only data
path is `ghmem` commands over the local corpus.

- **ghmem-triage (Sonnet).** Input: none — no item content, no budget
  numbers; the CLI owns the batch budget. It opens a session with
  `ghmem triage` (which mints the session token and serves the first
  packet), then loops: judge the packet, answer with one
  `ghmem route <token> <id> <verdict> [note]`, receive
  the next packet — until the CLI reports batch complete or triage
  complete. The budget is CLI-enforced; the agent never counts. Its
  final report leads with the terminal status — `batch complete`
  (items remain: spawn another batch) or `triage complete` (stop) —
  followed by the id → verdict lines, so the orchestrator branches on
  the first line. May propose new
  topics, consulting the packet's own `topics:` line first to avoid
  near-duplicates.
  "No-signal" is a comfortable default verdict, not a failure — most
  items encode no decision.
- **ghmem-synthesize (Opus).** Input: one topic's context from
  `ghmem topic` (with its snapshot token). Output: create/update that
  topic doc — including its frontmatter `description:` hook line —
  then `ghmem close <token>`; the harness regenerates INDEX.md. A
  stale token (activity landed mid-synthesis) is a normal race, not an
  error to escalate: the same agent re-assembles from the fresh
  snapshot, rewrites the doc, and retries, stopping only after two
  consecutive stale tokens. Update unit = one topic, so incremental runs
  touch only dirty topics. One topic per spawn — no batching: a
  topic's context (~12k tokens for a hot cluster) plus a full
  rewritten document already dwarfs the fixed spawn overhead that
  motivates triage batching, and prose quality — the point of the
  Opus tier — is what accumulated-context rot degrades first. Standing rules with teeth: FR/NFR/Limitation
  entries
  require maintainer-grade backing (a statement by someone with
  standing at that time, or actual merge/reject behavior) — user
  chorus is demand evidence, never policy; History and Open questions
  attribute positions with standing, since a live debate's front line
  is where people with merge power currently stand.
- **Incremental loop** = `ghmem sync` → `ghmem triage --requeue` →
  triage the delta → synthesize dirty topics. The loop is fully
  deterministic — agents report terminal status lines precisely so
  the orchestrator needs no judgment — so orchestration can be a
  skill-driven session now and an Agent-SDK script later (runs on
  subscription auth; removes the orchestrator model from the loop)
  if manual cadence proves annoying. Scripting it is the natural
  endgame, not a prerequisite.

## Validation

**Ground-truth test before trusting the pipeline:** run it on the
wrapindent cluster (#3107, #2374 and their cross-references) and
compare the produced topic doc against
`docs/softwrapcolumn-vs-wrapindent.md`, which was validated by hand.
The comparison is purely **semantic** — freshly generated output has
no reason to be structurally similar to the hand-written doc, so a
textual diff is useless; the gate judges whether each required signal
is present and citation-backed. If the pipeline misses the
option-count aversion, the indent-vs-wrap axis distinction, or the
stall rationale, the prompts are not ready.

Triage validation must include **distractors**: a mixed queue of
~15–20 items around the cluster (cross-references plus contemporaneous
unrelated bug reports and support questions) where most items should
come back no-signal or other-topic. Routing only the wrapindent
cluster against an empty topic list would make the match trivial and
prove nothing about accuracy.

Harness-level: `sync` resumability and cursor correctness testable
against recorded fixtures; `heat`/`triage`/`route`/`status` are pure
functions over the local store, testable without network (pytest,
checked-in fixtures).

## Error handling

- Rate limit exhaustion mid-sync: persist cursor, exit with a resume
  hint; never lose fetched data.
- GraphQL partial failures (single bad node): log, skip, record the id
  in the ledger as fetch-failed for retry.
- Token scope gaps (Projects): detect, warn once, skip.
- Triage disagreement over time: `route` is append-only in history;
  latest verdict wins; re-routing marks both old and new topics dirty.
- Topic close-out race: handled by the snapshot-token compare-and-set
  in `ghmem close` (see Architecture §1); a stale token leaves the
  topic dirty rather than discarding newer input, and the synthesize
  agent treats it as a retry — re-assemble from the fresh snapshot,
  rewrite, close out again; two consecutive stale tokens stop and report.
- Stranded triage dispatch: a `dispatched` event with no route (agent
  crashed mid-item) is surfaced by `status` and reclaimed by
  `ghmem triage --requeue` at the start of the next orchestration run,
  before agents are spawned — explicit reclaim instead of timeouts, so
  a slow-but-live session is never raced.

## Execution structure

The work is split into four subprojects, each planned **just-in-time
and from scratch** in a fresh session — a subproject's plan is written
only after this spec is finalized and its predecessor has landed,
derived from the spec plus the actual code, nothing else.

1. **corpus** — scaffold, direnv-activated venv, fetch + routing
   ledgers with SQLite mirror, `sync` (including PR diffs), standing
   derivation, `show`.
2. **dispatch** — `heat`, session-budgeted `triage`/`route` with
   reclaim, `status`, `topic`/`close`, `index`.
3. **agents** — the two agent definitions, the allowlist hook, the
   `docs/design/` scaffold.
4. **validation** — wrapindent ground-truth gate, formula calibration,
   backfill runbook.

Each plan opens with a spec-fidelity audit of its slice, is
**contract-style** (interface contracts, test lists, and genuinely
tricky algorithms — not full code listings; Sonnet implementers under
the toolchain gates and one-tier-up review fill in the rest), and the
human reviews the plan itself. Hard cap on any human-reviewed
artifact, plans and PRs alike: **400 lines** — anything longer gets
split. Sole exception: PRs that merely reorganize code with no
behavioral change.

## Non-goals

- No hosted service, no embeddings/RAG index, no chatbot.
- No mining of Discord/Gitter/mailing lists.
- No automatic upstream PR of the docs — upstreaming is a later, manual
  decision.
- No attempt to make topic docs exhaustive changelogs; they record
  decisions and debates, not history for its own sake.

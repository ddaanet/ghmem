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
- **Naming: `docs/design/`, flat, "topic doc".** `docs/memory/` was
  confusingly similar to the gitlore `./memory/` submodule, and
  "dossier" is not a term seen in the wild. The artifact is
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
- **Toolchain: edify standards; dependencies welcome.** The earlier
  stdlib-only constraint is dropped — a large code drop is held to the
  strictest bar instead: uv-managed project, click CLI, pytest
  (+pytest-mock), `ruff select = ["ALL"]` with the edify ignore list,
  `mypy strict` + `extra_checks`, docformatter, a justfile `green`
  recipe. Reference config: `/Users/david/code/edify/pyproject.toml`
  and its justfile. Python floor is **>=3.14** (its type-annotation
  improvements make stringified forward references unneeded and
  undesirable; this is developer tooling, no backward compatibility
  owed). GitHub access via **githubkit** (typed, active,
  built-in retry), token from `gh auth token` or `$GITHUB_TOKEN`.
  Shelling out to `gh api graphql` was rejected: `--paginate` handles
  only a single top-level connection, so nested comment/review
  pagination would be hand-rolled either way.
- **Model tiering.** Sonnet for triage (bulk classification), Opus for
  topic-doc synthesis (prose quality).
- **Triage and synthesize are custom agents, not skills.**
  `.claude/agents/ghmem-triage.md` (model: sonnet) and
  `.claude/agents/ghmem-synthesize.md` (model: opus). Agents carry
  `model`, `tools`, and `hooks` frontmatter — model guidance in a
  skill body was the smoking gun. Procedure text lives in companion
  skills preloaded via the agent `skills:` field: live-editable
  (re-read on each fresh invocation) and a stable, cacheable system
  prefix. The no-GitHub guardrail is enforced mechanically: a
  PreToolUse hook in each agent's frontmatter denies `gh`/network
  commands with a structured reason — the agents' only data paths are
  `ghmem` commands over the local corpus. Authoring follows
  plugin-dev:skill-development and plugin-dev:hook-development.
- **Triage is one item per invocation.** A 25-item batch packet is
  complex context Sonnet handles poorly, and the items are
  self-contained. Per-item agents cost roughly the same input tokens:
  the shared agent prefix is prompt-cached after the first invocation
  (~10% read price, 5-min TTL refreshed each hit; the prefix must
  exceed the 1,024-token cache floor, which it does), and total unique
  content is identical. Each invocation judges one item with an
  undiluted context.
- **Formula constants are calibrated, never hip-shot.** Heat weights
  and standing thresholds ship as provisional values and are derived
  properly by a recorded calibration analysis over the real corpus
  after the first full sync: signal distributions and percentiles,
  plus ground-truth checks (dmaluka/JoeKar must derive as maintainers
  in the right eras; known-hot threads like #93/#3127 must rank in the
  top percentile). Standing rule: heuristic formulas are grounded in
  data analysis.
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

### 1. Harness — `tools/ghmem` (Python/uv, click, githubkit, sqlite3)

No agent ever calls the GitHub API; agents read what `ghmem` commands
emit from the local corpus.

- `ghmem sync` — GraphQL-batched fetch (githubkit; explicit nested
  cursors) of all issues, PRs (including review threads and review
  comments, `mergedBy`/`mergedAt`, fetch-time `author_association` as
  the hint column), and discussions, plus the commit log from the
  local `micro/` clone, into a SQLite corpus. Cursor on `updated_at`;
  resumable mid-run; rate-limit aware (tracks GraphQL point cost,
  sleeps/exits cleanly on exhaustion). The same command is the
  incremental update: it fetches only the delta past the cursor.
- `ghmem show <id>` — one thread rendered as compact markdown: body,
  comments, review threads, cross-references, reactions, linked
  commits. The agent-facing read primitive. Authors are annotated with
  their standing at time of posting (e.g. `dmaluka (maintainer since
  ~2022; 180 merges at time of comment)`), from a derived standing
  table rebuilt from corpus data; `batch` and `topic` output carries
  the same annotations.
- `ghmem heat` — deterministic ranking over the corpus: comment count,
  reaction count, distinct participants, cross-reference degree,
  recurrence span (years between first and last activity).
- `ghmem triage` — yields the next untriaged item (heat order) as a
  complete packet: id plus its `show` rendering. When triage is
  complete, stdout is empty, a `triage complete: 0 pending` note goes
  to stderr, and the exit code is still 0 — exit codes are reserved
  for actual errors (an expected outcome must not read as a failure).
  Yielding records a
  `dispatched` ledger event so concurrent calls hand out distinct
  items; `route` resolves the dispatch, `status` surfaces
  dispatched-but-unrouted items, and `ghmem triage --requeue` returns
  them to the queue after a crashed run. (Replaces the earlier
  `batch --pending -n k` lister — with one item per agent invocation,
  a k-item packet had no consumer.)
- `ghmem route <id> <topic>` / `ghmem route <id> --no-signal` — records
  a triage verdict in the ledger; marks the topic dirty.
- `ghmem status` — coverage ledger summary: counts of triaged / routed
  / no-signal / pending, dirty topics, sync cursor position.
- `ghmem topic <name>` — assembles the synthesis context for one
  topic: every routed item's digest plus full text of the high-heat
  ones, headed by a **snapshot token** (max ledger sequence + max
  `updated_at` over the topic's items). Close-out is compare-and-set:
  `ghmem topic <name> --synthesized <token>` clears the dirty flag
  only if the token is still current — routes or comments that arrived
  between assembly and close-out leave the topic dirty for the next
  run, never silently discarded. Successful close-out regenerates
  `INDEX.md`.
- `ghmem index` — regenerates `docs/design/INDEX.md` from the topic
  docs' frontmatter `description:` lines, ordered alphabetically by
  topic name (stable diffs; heat-ordering would churn the file on
  every sync). INDEX.md is harness-generated, never agent-edited.

Storage: corpus SQLite is regenerable → gitignored. The ledger — an
append-only JSONL file of routing verdicts plus the topic list and
topic dirty flags — is committed; it is the system's own memory and
makes runs reproducible. The harness treats the JSONL as the source of
truth and mirrors it into SQLite for querying.

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
  - **Limitations**: rejected approaches and won't-happens, with the
    rejection rationale. Rejected/stalled PRs are first-class evidence.
  - **Open questions**: live, unresolved debates and their current
    front line (who holds what position, where it stalled). Prevents
    the most re-litigation-prone material from being buried in History.
  - **History**: compressed chronological debate digest that the other
    sections cite into.
  - Norm topics (cross-cutting philosophy like options parsimony) are
    legitimately all-NFR; empty sections are allowed and the synthesis
    prompt must not invent filler to populate them.

### 3. Agentic layer — two custom agents

Both are `.claude/agents/` definitions with pinned models, preloaded
companion skills for the (live-editable) procedure text, and
frontmatter PreToolUse hooks denying GitHub/network access — their
only data path is `ghmem` commands over the local corpus.

- **ghmem-triage (Sonnet).** Input: one item (its `ghmem show`
  rendering), one item per invocation. Output: a single `ghmem route`
  verdict. May propose new topics. "No-signal" is a comfortable
  default verdict, not a failure — most items encode no decision.
- **ghmem-synthesize (Opus).** Input: one topic's context from
  `ghmem topic` (with its snapshot token). Output: create/update that
  topic doc — including its frontmatter `description:` hook line —
  then close out with the token; the harness regenerates INDEX.md.
  Update unit = one topic, so incremental runs touch only dirty
  topics. Standing rules with teeth: FR/NFR/Limitation entries
  require maintainer-grade backing (a statement by someone with
  standing at that time, or actual merge/reject behavior) — user
  chorus is demand evidence, never policy; History and Open questions
  attribute positions with standing, since a live debate's front line
  is where people with merge power currently stand.
- **Incremental loop** = `ghmem sync` → triage the delta → synthesize
  dirty topics. Invoked manually (a skill/command) first; a cron
  wrapper only if manual cadence proves annoying.

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
against recorded fixtures; `heat`/`batch`/`route`/`status` are pure
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
  on `--synthesized` (see Architecture §1); a stale token leaves the
  topic dirty rather than discarding newer input.

## Non-goals

- No hosted service, no embeddings/RAG index, no chatbot.
- No mining of Discord/Gitter/mailing lists.
- No automatic upstream PR of the docs — upstreaming is a later, manual
  decision.
- No attempt to make topic docs exhaustive changelogs; they record
  decisions and debates, not history for its own sake.

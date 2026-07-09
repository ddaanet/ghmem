# ghmem — reified institutional memory for micro

Date: 2026-07-09
Status: approved (design), pending implementation plan

## Problem

The micro project has no institutional memory. Recurring debates
(option-count aversion, clipboard behavior, highlighter architecture,
wrap semantics) are re-litigated in GitHub issues because the outcomes
of past debates live only in closed threads nobody re-reads. This
project reifies that memory as maintained design documents in this
repo's `docs/memory/`, published on GitHub now and offerable upstream
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
  linking into dossiers), dossiers hold the debate history. Same
  index-plus-detail-files pattern as Claude's memory system, so agents
  load cheaply and drill down only when relevant; to a human it reads
  as an ordinary catalog.
- **Index entries are retrieval hooks, not titles.** "wrapindent
  stalled: option-count aversion + indent-vs-wrap axis confusion →
  dossier" rather than "PR #3107 discussion".
- **Backfill: full coverage, heat-ranked order.** Whole corpus is
  ~3–4M tokens of raw text; one-pass Sonnet triage ≈ 5M input tokens,
  Opus synthesis ≈ 3–6M more. Fits a Max5 plan across a few 5-hour
  windows. Hottest topics get dossiers first; the cold tail is
  batch-triaged last; the run is abortable at any point with the best
  material banked. Skeleton-first is the schedule, full coverage the
  end state.
- **12-factor split.** All deterministic work (fetch, store, render,
  rank, ledger) lives in a CLI harness. Agents only judge: route items
  to topics, and write prose.
- **Model tiering.** Sonnet for triage (bulk classification), Opus for
  dossier synthesis (prose quality).

## Architecture

### 1. Harness — `tools/ghmem` (Python, stdlib + sqlite3, `gh` for auth)

No agent ever calls the GitHub API; agents read files the harness
emits.

- `ghmem sync` — GraphQL-batched fetch of all issues, PRs (including
  review threads and review comments), and discussions, plus the commit
  log from the local `micro/` clone, into a SQLite corpus. Cursor on
  `updated_at`; resumable mid-run; rate-limit aware (tracks GraphQL
  point cost, sleeps/exits cleanly on exhaustion). The same command is
  the incremental update: it fetches only the delta past the cursor.
- `ghmem show <id>` — one thread rendered as compact markdown: body,
  comments, review threads, cross-references, reactions, linked
  commits. The agent-facing read primitive.
- `ghmem heat` — deterministic ranking over the corpus: comment count,
  reaction count, distinct participants, cross-reference degree,
  recurrence span (years between first and last activity).
- `ghmem batch --pending -n <k>` — emits a triage packet (markdown
  file) of the next k untriaged items in heat order, compact per-item
  digests.
- `ghmem route <id> <topic>` / `ghmem route <id> --no-signal` — records
  a triage verdict in the ledger; marks the topic's dossier dirty.
- `ghmem status` — coverage ledger summary: counts of triaged / routed
  / no-signal / pending, dirty dossiers, sync cursor position.
- `ghmem topic <name>` — assembles the synthesis context file for one
  topic: every routed item's digest plus full text of the high-heat
  ones, ready for an Opus session.

Storage: corpus SQLite is regenerable → gitignored. The ledger — an
append-only JSONL file of routing verdicts plus the topic list and
dossier dirty flags — is committed; it is the system's own memory and
makes runs reproducible. The harness treats the JSONL as the source of
truth and mirrors it into SQLite for querying.

### 2. Artifact — `docs/memory/`

- `INDEX.md` — the recall surface described above. Every claim cites
  issue/PR numbers.
- `dossiers/<topic>.md` — per-theme living documents: the question, the
  positions taken, what was rejected and why (rejected/stalled PRs are
  first-class evidence), current state, open ends. Citations
  throughout.

### 3. Agentic layer — two skills

- **Triage (Sonnet).** Input: one batch packet. Output: `ghmem route`
  calls. May propose new topics. "No-signal" is a comfortable default
  verdict, not a failure — most items encode no decision.
- **Synthesize (Opus).** Input: one topic's context file from
  `ghmem topic`. Output: create/update that dossier and its INDEX.md
  lines. Update unit = one topic, so incremental runs touch only dirty
  dossiers.
- **Incremental loop** = `ghmem sync` → triage the delta → synthesize
  dirty topics. Invoked manually (a skill/command) first; a cron
  wrapper only if manual cadence proves annoying.

## Validation

**Ground-truth test before trusting the pipeline:** run it on the
wrapindent cluster (#3107, #2374 and their cross-references) and diff
the produced dossier against `docs/softwrapcolumn-vs-wrapindent.md`,
which was validated by hand. If the pipeline misses the option-count
aversion, the indent-vs-wrap axis distinction, or the stall rationale,
the prompts are not ready.

Harness-level: `sync` resumability and cursor correctness testable
against recorded fixtures; `heat`/`batch`/`route`/`status` are pure
functions over the local store, unit-testable without network.

## Error handling

- Rate limit exhaustion mid-sync: persist cursor, exit with a resume
  hint; never lose fetched data.
- GraphQL partial failures (single bad node): log, skip, record the id
  in the ledger as fetch-failed for retry.
- Token scope gaps (Projects): detect, warn once, skip.
- Triage disagreement over time: `route` is append-only in history;
  latest verdict wins; re-routing marks both old and new topics dirty.

## Non-goals

- No hosted service, no embeddings/RAG index, no chatbot.
- No mining of Discord/Gitter/mailing lists.
- No automatic upstream PR of the docs — upstreaming is a later, manual
  decision.
- No attempt to make dossiers exhaustive changelogs; they record
  decisions and debates, not history for its own sake.

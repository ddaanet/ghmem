# `memory/ghmem-project.md` reduction

## Result

**3,999 bytes** (was 117,073 — 1,890 lines down to 90). The whole file
sits inside the 4,096-byte recall window, so nothing is unreachable and
no split was needed.

Frontmatter is byte-identical apart from `modified:`, which the write
hook bumped. `description:` untouched, per the brief.

Nothing outside `memory/ghmem-project.md` was written. `memory/MEMORY.md`
and the `ddaanet` submodule already showed as modified in
`git -C memory status` before this task began (an index line for
`mutate-code-to-test-the-tests` that another session added); those
changes are not mine and were left alone.

## Section order

Ordered by the literal a reader arrives holding, not by the order the
facts were learned:

1. **Orientation** (one paragraph) — where the design and the dated
   change record actually live, so a session arriving with "ghmem"
   leaves for the right artifact instead of reading on.
2. **Dispatching a triage or synthesis worker** — the most frequent live
   operation, and the one whose failure is silent and uncontained. The
   index line already routes on `subagent_type`, so this is the section
   that line promises.
3. **Judging the topic space** — arrives holding a topic name or a
   fragmentation complaint.
4. **Changing a ledger or corpus schema** — arrives holding `fetch.jsonl`
   / `routing.jsonl` / "do I need a migration".
5. **Editing a GraphQL query** — arrives holding a query string.
6. **Writing a SQLAlchemy aggregate** — arrives holding `.correlate` or a
   wrong aggregate.
7. **Rendering markdown** — arrives holding a renderer change.
8. **`just green`** — arrives holding a gate failure (`D205`, a
   whole-repo docformatter diff).

2–4 are the human-partner constraints and 5–8 are the observed failure
modes; within each group the more frequently met symptom comes first.

## Cut as owned elsewhere — with the verification

### Owned by `docs/ghmem-design.md`

The largest single cut is the command surface — the semantics, exit
codes and terminal strings of all twelve commands:

| commands | cut | grepped for | found at |
| --- | --- | --- | --- |
| `sync`, `rebuild`, `show`, `discussion`, `heat`, `status`, `index` | what each reads and emits, and its exit status | `ghmem rebuild`, `ghmem heat` | Architecture §1 |
| `triage`, `route`, `requeue` | the session loop, the note protocol, the terminal strings | `batch complete`, `triage complete`, `ghmem route` | Architecture §1 |
| `topic`, `close`, `unroute` | snapshot/close-out and the withdrawal path | `ghmem close`, `ghmem unroute` | Architecture §1 |

The rest of what `docs/ghmem-design.md` took over. Citations are by
section and heading rather than line number: the doc is edited on
every slice, so a line number in this table would be a pointer that
rots on the next commit.

| Cut | Grepped for | Found at |
| --- | --- | --- |
| Two-ledger storage model, which ledger is regenerable, SQLite as derived cache, SQLAlchemy 2.0 declarative as the access layer | `regenerable`, `SQLAlchemy` | NFR5; Architecture, the "**Storage.**" paragraph |
| Note required on every verdict incl. `no-signal`; note arrives on stdin only; a quoting mistake corrupts a note silently | `only on stdin`, `no-signal` | FR3; Design decisions, "The routing note arrives on stdin" |
| The allowlist is a positive character class; quoted-heredoc shape; delimiter held to letters/digits/underscores; deny-patterns rejected | `positive character class`, `heredoc`, `delimiter` | NFR3; Design decisions, the containment-hook paragraph; Rejected alternatives, "A deny-pattern hook" |
| Companion skill rejected as context noise / silent-skip risk | `companion procedure skill` | Rejected alternatives, "A companion procedure skill for the agents" |
| Procedure text inline in agent definitions; `.claude/agents/` file-watched so no restart for a definition edit | `file-watched` | Architecture §3, and the Design-decisions paragraph on the agent definitions |
| Model tiering (Sonnet triage / Opus synthesis); one topic per synthesis spawn | `Model tiering`, `One topic per synthesis spawn` | Design decisions |
| Character-metered batch budgets, CLI-side, never model-honored | `a model cannot be trusted to count` | NFR4; "Triage agents pull their own items" |
| Heat weights kept unchanged after calibration; calibration must normalize scale before tuning weights; reweighting reorders permanent topic-name minting | `normalize each metric's scale`, `record_verdict makes permanent` | NFR7; "Formula constants are calibrated, never hip-shot" |
| PR diffs as corpus data; `DIFF_BODY_MAX_CHARS` as a display valve; first-item-served guarantee is "the queue advances", not "every packet fits"; `fit`/`diff_cut`/`oversized` tags | `diff_cut`, `oversized`, `whatever its size` | "PR diffs are corpus data" and "A session's first item is always served" |
| Snapshot digest compare-and-set, stale close is a normal race | `compare-and-set`, `digest` | `ghmem close` bullet; Failure handling "Topic close-out race" |
| Explicit reclaim over dispatch timeouts | `timeout` | "Crash recovery is explicit, not timed"; Rejected alternatives |
| `unroute` design: deletes route + dispatch, deletes an emptied `Topic` row, no containment change, `no-signal` unroutable too, no `--session` selector | `unroute` | FR7; the `ghmem unroute` bullet; "Unrouting a topic's last item"; four Rejected-alternatives entries |
| 400-line cap on human-reviewed artifacts | `400 lines` | NFR9 |
| Topic-doc section structure FR/NFR/Limitations/Open questions/History; INDEX.md never agent-edited; alphabetical index order | `Open questions`, `never agent-edited` | FR4; Architecture §2; "Heat-ordered `INDEX.md`" rejection |
| Standing is computed and time-varying; `author_association` is a hint | `author_association` | FR8; two Design-decisions paragraphs |
| Incremental operating mode ("sweep is once, triage is forever") | `incremental` | FR6; "The incremental loop"; "Incremental synthesis reloads full topic context" |

### Owned by `docs/ghmem-changelog.md`

Every "Part N DONE — landed \<date\>" narrative, the four-subproject
split, the just-in-time/contract-style planning process, and each slice's
pointer to its frozen spec or plan. Grepped `Slice records`,
`contract-style`, `just-in-time`; all present, one dated entry per slice
including the 2026-09-02 `unroute` entry.

### Owned by a sibling memory file (verified present under `memory/ddaanet/`)

| Cut | Owner |
| --- | --- |
| Named dispatch demotes `agentType`; `hasTrustDialogAccepted` gate; agents reporting containment while their tool results show execution | `frontmatter-hooks-two-silent-gates.md` (kept as a link) |
| The allowlist forbade the notes `route` requires, so it never enforced; the backtick-substitution note corruption, the swept proxy credential, the rewritten append-only ledger | `guardrail-must-permit-real-commands.md` (kept as a link) |
| Nine live-run findings (missing `corpus/` dir, missing FK indexes making `heat` >10 min, inverted weight-vs-influence, first-page connection truncation, unreachable B2 fit paths) and the rule to run live before planning the next subproject | `test-with-live-data-before-designing.md` (kept as a link) |
| Every "test passed for the wrong reason" instance — the vacuous live-vs-rebuild compare, the fixture with nothing to leak, `drop_all` under an idempotence test, `"topic-a" in output` | `green-is-not-evidence.md` (kept as a link) |
| Mutation-sweep method, keeping the script's shape, an "all killed" verdict that means nothing | `mutate-code-to-test-the-tests.md` |
| `PATH_add` and new agent definitions needing a restart; the wrong `~/.bashrc`-interactive-shell diagnosis | `verify-restart-before-structural-diagnosis.md` |
| The 0-byte read-only `.claude/agents` and `.claude/hooks` stray files | `sandbox-effects.md` — "phantom dotfiles as char devices or zero-byte files" |
| A named background agent's final message not reaching the orchestrator | `cc-subagent-approval.md` — "a bare `idle_notification` is not a report" |
| Plans carry contracts, not full code | `plan-contracts-not-full-code.md` |
| `ruff check` reaching outside the gate's hash inputs | `gate-cache-must-cover-every-check.md` |
| Justfile/edify prolog, bare tool names, `check_line_limits.sh` | `edify-python-standards.md` |
| The packet not carrying the topic list the agent body told workers to consult | `instruction-names-absent-data.md` |
| Triage judges content, not settledness | `ghmem-ongoing-sync-not-snapshot.md` (kept as a link) |

Four `[[links]]` in the old text pointed at files that no longer exist —
`tests-pass-for-the-wrong-reason`, `unkillable-mutation-means-a-wrong-model`,
`mutation-sweep-must-survive-a-kill`, `stale-artifact-is-not-agent-death`.
Their content was folded into `green-is-not-evidence` and
`mutate-code-to-test-the-tests` in an earlier retirement pass, so the new
file links the survivors instead.

### Owned by the code

| Cut | Verified at |
| --- | --- |
| `since:` filters `committedDate`, not `authoredDate`, and is inclusive; the hwm is the newest `committedDate`; `_raw_data` drops it | `tools/ghmem/src/ghmem/sync_commits.py:39-42`, `:114` |
| `max()` over ISO-8601 text is lexicographic; take `julianday` | `heat_query.py:180-183` (comment) |
| `auto_retry=RETRY_SERVER_ERROR` keeps 5xx retries and declines the rate-limit block; every query must select `rateLimit { cost remaining resetAt }`; truncation is `errors[].code == "too_large"` | `github.py:13-15`, `:81-100`, `:112`, `:138-157` |
| `string.Template` subclass with `delimiter = "%"` because `$` collides with GraphQL variables | `sync_core.py:106-126` |
| A plain top-level `EXISTS` needs no explicit `.correlate` | `heat_query.py:67` docstring (the *trap* itself is kept — see below) |
| Nesting rides `_TOP_HEADING_LEVEL`/`_REPLY_HEADING_LEVEL` | `show.py:36-37` (the *rule* is kept — see below) |
| Deliberate FK indexes on `comment.item_id`, `review.pr_id`, `review_comment.review_id`, `cross_ref.dst_id`, `discussion_comment.discussion_id` | `models.py:176,188,199,219,260` |
| The raw-node key contract emitted by the sync driver | `ingest.py` module docstring, cross-referenced from `raw_nodes.py` |
| `Dispatch` keyed by `item_id` alone, and why | `Dispatch`'s docstring |

### Owned by `CLAUDE.md`

The `docformatter` install route (`uv tool install`, `~/.local/bin`, no
Python 3.14 support, the `require-docformatter` guard), the `just green`
cache and its inputs, the `tools/ghmem/hooks/scripts/` plugin-shaped
layout, and the `just precommit` entry point. Grepped `docformatter`,
`bats`, `hooks/scripts` — all present in the project `CLAUDE.md`.

### Owned by `memory/ddaanet/shared-claude.md`

The box's ~2GB OOM limit and no-parallel-heavy-work rule; ground
heuristic constants in data and bind weights as query parameters;
optional means the source can genuinely omit it (the
`authorAssociation` worked example); parse structured formats rather
than regexing a grammar; do not bake a strong guarantee into every call
(`Ledger.append` vs `Ledger.sync()`); a slow gate needs both levers.
Grepped `OOM`, `authorAssociation`, `zalgo`, `Ledger.append` — all
present.

## Kept, with the doubt resolved

- **The SQLAlchemy auto-correlation trap.** `heat_query.py:114-116`
  carries the comment, so by the strict owner test this is code's. Kept
  anyway: the comment sits at one call site inside one module, and the
  trap fires on *any* new correlated aggregate anywhere in the package —
  Part B's filtered ranking hit it a second time. The brief also names it
  as a keeper. The `EXISTS` half is the discriminator that makes the rule
  decidable, so it stays in the same sentence.
- **The markdown-parser rule.** `show.py` owns the constants but not the
  reasoning, and the rule now applies to a second surface the code does
  not cover: synthesized topic docs and `INDEX.md`. Kept as three lines.
- **The docformatter 1.7.7/1.7.8 split.** `CLAUDE.md` says docformatter
  cannot live in the venv and must be `uv tool install`ed; it does not
  say what happens if someone puts it in the venv anyway. That failure —
  a whole-repo rewrap, mid-hyphenated-word breakage, and `in-place = true`
  leaking into a `--diff` run so a *check* edits the tree — is the reason
  the CLAUDE.md rule exists, and it is what a session meeting a
  whole-repo diff will be holding. Kept.
- **"Four concurrent `ghmem-triage` workers."** `shared-claude.md` states
  the general ~500MB-headroom rule; the concrete ghmem number is a
  measurement against this specific workload and is not derivable from
  the general rule. Kept, one sentence.
- **The read-only sampling method.** Its original framing ("routing
  consumes items irreversibly") is now *false* — `ghmem unroute` shipped
  2026-09-02. Kept in its still-true form: topic-name minting is what
  `record_verdict` makes permanent, so sample with `heat` CSV and `show`
  rather than spending routes. Rewritten rather than copied.
- **"Schema changes are free."** The design doc says `fetch.jsonl` is
  regenerable and `routing.jsonl` is not, but it nowhere states the
  instruction that follows — carry no back-compat, no optional fields,
  no migrations for landed entries. That is a human-partner call with no
  owner, so it stays. The old text's "SUPERSEDED IN ITS PREMISE" delta
  framing is gone; the surviving rule is stated positively with the
  re-sync as its price.

## Cut and genuinely lost

No owner, judged not worth the bytes:

- **"A plan's File Structure omitting a file means implementers never
  touch it"** — the `ledger.py` gap that left the discussions slice
  runtime-complete but not type-complete. The specific instance is
  closed (`ledger.py` collapsed to one `RawNodeEvent` plus a
  `RawNodeType` Literal, and `_event_type` as a `ClassVar` now ties the
  struct list to the tag list), and no further contract-style plans are
  outstanding. The general lesson is real and now unrecorded.
- **"A composite primary key does not serve its non-leading columns"** —
  the reason `cross_ref.dst_id` needs its own `index=True` despite being
  a PK column. `models.py` carries the declaration and a test asserts the
  query plan has no `SCAN`, so the guard survives; the explanation does
  not.
- **"Check the codebase's existing seam before inventing a task
  boundary"** and the as-built-divergence-block convention (annotate a
  plan rather than rewriting it to match landed code). Both are planning
  craft with no ghmem-specific hook; `design-doc-writing.md` is adjacent
  but does not say either.
- **"When a bug is a units/semantics confusion, check whether the name
  encodes the wrong semantics too"** — from `xref_degree` →
  `related_items`. Good general advice, no ghmem trigger.
- **Open findings 4, 6, 8 from the first live run** — `fetch_failures()`
  having no caller outside a test, `_last_cursor` re-parsing the whole
  ledger twice per connection per startup, and 8 PRs rendering an empty
  ` ```diff ` fence with no marker. These are open *work items*, not
  facts; the brief rules out pending framing, and a memory is the wrong
  home for a backlog. They are recorded here so they are not silently
  dropped — if they should survive, they belong in an issue or a plan.
- **The `model: opus` trial for `ghmem-synthesize`** — designed, never
  run. Pending framing, cut; the design doc's "Model tiering" paragraph
  states the shipped choice.

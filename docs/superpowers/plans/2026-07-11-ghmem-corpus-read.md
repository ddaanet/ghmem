# ghmem Subproject 1 (corpus), Part 2 — Read

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Plan style.** Contract-style per the spec's Execution mandate:
> interface contracts, test lists, tricky algorithms — **not** full
> code listings. Implementers are Sonnet under the toolchain gates
> below with one-tier-up (Opus) review. Every task is TDD: write the
> failing test, watch it fail, implement, watch it pass, commit.
>
> **Split — Part 2 of three.** The corpus subproject splits into
> **Part 1 — Store** (`…-corpus-store.md`, landed), **Part 2 — Read**
> (this file), **Part 3 — Sync** (`…-corpus-sync.md`). This part builds
> the offline read layer over Part 1's `Store`. It **depends on Part
> 1**, already landed, and is fully testable against hand-authored
> fixture ledgers (no network).

**Goal:** Derive time-varying contributor standing from the corpus and
render any single thread as compact, standing-annotated markdown — both
reading only Part 1's local `Store`.

**Architecture:** Deterministic and offline. `build_standing` derives
owner/maintainer/established/newcomer as of any date from merge
authority, accepted-PR counts, and master-commit authorship; `show`
resolves one item and renders its thread with those annotations inline.
Both are pure functions over the SQLAlchemy-mirrored corpus, testable
without network.

**Tech Stack:** Python ≥3.14, uv, click, SQLAlchemy 2.0 over SQLite,
pytest + pytest-mock, ruff (`select=ALL`), mypy strict.

## Global Constraints

Copied verbatim from the spec
(`docs/superpowers/specs/2026-07-09-ghmem-design.md`); every task's
requirements implicitly include these.

- Python floor **>=3.14**; no stringified forward references (3.14
  makes them unneeded and undesirable — developer tooling, no backward
  compatibility owed).
- Toolchain held to the strictest bar, config taken from sibling
  `edify` (`/Users/david/code/edify/pyproject.toml` + justfile):
  uv-managed, click CLI, pytest (+pytest-mock), `ruff select=["ALL"]`
  with edify's curated ignore list, `mypy strict` + `extra_checks`,
  docformatter, a justfile `green` recipe. Verify a task with
  `just green` (format, ruff, docformatter -c, mypy, pytest).
- No agent ever calls the GitHub API; agents (later subprojects) read
  only what `ghmem` emits.
- Formula constants (standing thresholds, the `show` diff-body display
  cap) ship **provisional** and are calibrated in subproject 4 — never
  hip-shot final values. Mark each `# PROVISIONAL — calibrated in
  subproject 4`.
- Hard cap on human-reviewed artifacts, this plan and every resulting
  PR alike: **400 lines**; anything longer is split (sole PR
  exception: pure code-reorg with no behavioral change).

---

## Spec-Fidelity Audit (Part 2 slice)

The spec assigns corpus this slice (§Execution 1): *"scaffold,
direnv-activated venv, fetch + routing ledgers with SQLite mirror,
`sync` (including PR diffs), standing derivation, `show`."* Part 2
covers the two read-layer items — **standing derivation** (T1) and
**`show`** (T3) — plus one small `Store` addition `show` needs (T2).

**Deviations from the spec's `show` contract, resolved for Part 2**
(both surfaced by the landed schema; approved to defer):

- **Linked commits — deferred.** The spec's `show` contract lists
  "linked commits", but the landed `Commit` entity carries no PR↔commit
  linkage (`sha`, `author`, `authored_date`, `message` only) — it is
  the master-branch log that feeds standing, not a per-PR commit set.
  Part 2 `show` renders no linked-commits section. Revisit in Part 3
  (sync) only if per-PR commits are ever fetched with a linkage column.
- **`author_association` recent-only hint — deferred.** The spec wants
  renderers to show the fetch-time `author_association` "as a secondary
  hint on recent items only". "Recent" needs a now-anchor; `show` is a
  deterministic, snapshot-tested offline renderer with none. Part 2
  `show` renders only **computed-standing** annotations (the primary,
  unambiguous form). The recency-gated hint is handled uniformly across
  renderers once the recency model is settled (subproject 2/4).

`show`'s standing-annotation format is shared with `triage`/`topic`
output (later subprojects) but only `show` consumes it here.

## File Structure

Adds to the `tools/ghmem/` uv project built in Part 1.

Created in Part 2:

- `src/ghmem/standing.py` — `Standing`, `StandingTable`, `build_standing`
- `src/ghmem/show.py` — single-thread markdown renderer
- `tests/test_standing.py`, `tests/test_show.py`,
  `tests/fixtures/standing_corpus.jsonl`,
  `tests/fixtures/show_corpus.jsonl`

Modified in Part 2:

- `src/ghmem/store.py` — add `issue_or_pr_by_id` (T2)
- `tests/test_store.py` — one test for `issue_or_pr_by_id` (T2)
- `src/ghmem/cli.py` — add `show` (T3)

**Consumed from Part 1** (landed; exact names — the stale draft of this
plan used pre-rename names, do not reintroduce them): `Store` with
`get_issue_or_pr(number) -> IssueOrPR`, `comments_for(item_id)`,
`reviews_for(pr_id)`, `review_comments_for(review_id)`,
`reactions_for(subject_id)`, `cross_refs_for(item_id)`,
`pr_diff_for(number) -> PRDiff`, `commits()`, `merges_before(d: date)`,
`issues_or_prs_authored_by(user)`. Entities are **detached ORM
dataclasses** (value `__eq__`): `IssueOrPR` (fields incl. `id:
GraphQLId`, `kind`, `number`, `title`, `state`, `author`, `created_at`,
`updated_at`, `merged_by`, `merged_at`, `author_association`, `body`),
`Comment`, `Review`, `ReviewComment` (has `path`), `Reaction`
(`subject_id`, `content`, `count` — aggregated), `CrossRef` (`src_id`,
`dst_id`, `kind`), `Commit`, `PRDiff` (`diffstat: tuple[FileStat, ...]`,
`diff_text`, `truncated`), `FileStat`. Aliases `GraphQLId`, `Number`
(`ghmem.aliases`); `IssueOrPRNotFoundError`, `PRDiffNotFoundError`
(`ghmem.exceptions`). Children are keyed by **`GraphQLId`**, so `show`
resolves `get_issue_or_pr(number).id` first, then queries by that id.

Test fixtures are JSONL fetch ledgers of raw GraphQL nodes; rebuild a
store with the `_rebuilt_store(tmp_path, fixture)` pattern from
`tests/test_store.py` (`Store.open` → `rebuild_from_ledgers(fetch,
routing)`). The raw-node key shapes are the `_Raw*` structs documented
in `src/ghmem/ingest.py` — author nodes are `{"login": ...}` (or `null`
→ `"ghost"`), a commit `author` is a bare display string, timestamps
are ISO-8601 `"…Z"`.

---

### Task 1: Contributor standing derivation

**Files:** Create `src/ghmem/standing.py`, `tests/test_standing.py`,
`tests/fixtures/standing_corpus.jsonl`.

**Interfaces — Consumes:** `Store.merges_before`, `Store.commits`
(Part 1).

**Produces:**

- `class Standing(StrEnum)` — members via `enum.auto()`: `OWNER`,
  `MAINTAINER`, `ESTABLISHED`, `NEWCOMER` (StrEnum + `auto()` yields the
  lowercased member name as value; serialization is free and
  self-describing).
- `class StandingTable` (frozen dataclass) — snapshots corpus evidence
  once at build time; answers per-date without re-querying. Methods:
  - `of(user: str, when: date) -> Standing`
  - `annotation(user: str, when: date) -> str` — the inline form
    (examples below). Lives on the table, not the enum: it needs
    corpus-derived counts/dates the enum member cannot hold.
- `build_standing(store: Store) -> StandingTable`.

**Derivation (time-varying).** `build_standing` pulls all evidence
once: `store.merges_before(date.max)` (every merged PR — each carries
`author`, `merged_by`, `merged_at`) and `store.commits()` (each carries
`author` display-string, `authored_date`). It groups into three
per-user date lists held on the table: **merges credited** (dates where
`merged_by == user`), **accepted PRs** (merge dates where PR `author ==
user`), **commit authorship** (`authored_date` where `Commit.author ==
user`). `of(user, when)` counts only dates `<= when`:

- `user == OWNER_LOGIN` (`"zyedidia"`, the repo owner) ⇒ `OWNER`.
- merges-credited count `>= MERGE_THRESHOLD` ⇒ `MAINTAINER` (merge
  authority is the hardest signal).
- else accepted-PR count `>= ACCEPTED_PR_THRESHOLD` **or** any commit
  authored `<= when` ⇒ `ESTABLISHED`.
- else `NEWCOMER`.

`annotation(user, when)` renders, by class:
`"zyedidia (owner)"`;
`"dmaluka (maintainer since ~2022; 180 merges at time of comment)"`
(the "since" year is the year of the `MERGE_THRESHOLD`-th credited
merge; the count is merges credited `<= when`);
`"alice (established contributor; 6 accepted PRs at time of comment)"`;
`"newperson (newcomer)"`.

Standing is *computed*, never inferred by agents; `author_association`
is not consulted here.

**Known approximation — flag, don't solve now.** `Commit.author` is a
bare GitActor **display name**, while `merged_by`/PR `author` are GitHub
**logins**; the commit-authorship signal therefore matches on the
author string as stored and can miss a contributor whose commit name
differs from their login. Documented as approximate; accepted-PR count
is the primary `ESTABLISHED` signal, commit authorship supplementary.
Calibrated/reconciled in subproject 4.

**Provisional constants — do NOT finalize now.** `MERGE_THRESHOLD`,
`ACCEPTED_PR_THRESHOLD`, and `OWNER_LOGIN` ship as documented values
each marked `# PROVISIONAL — calibrated in subproject 4`
(`OWNER_LOGIN` is a fact, not a threshold, but pin it as a named
constant). Ground-truth anchors the tests, not the constants.

**Steps:**

- [ ] Author `tests/fixtures/standing_corpus.jsonl`: merged PRs
  credited to `dmaluka` starting ~2022 (enough to cross
  `MERGE_THRESHOLD`) and to `JoeKar`; merged PRs authored by a newcomer
  `alice` (enough to cross `ACCEPTED_PR_THRESHOLD`); a master commit by
  `bob`; a user `newperson` with no evidence. Use `item` events for the
  PRs (with `mergedBy`/`mergedAt`) and `commit` events for commits.
- [ ] Write `tests/test_standing.py` (When/Then docstrings, per house
  style):
  `test_dmaluka_maintainer_after_threshold_not_before` (era boundary:
  `of("dmaluka", <before>)` is not `MAINTAINER`, `of("dmaluka",
  <after>)` is),
  `test_joekar_maintainer`,
  `test_owner_is_owner`,
  `test_accepted_prs_promote_to_established`,
  `test_commit_author_promotes_to_established`,
  `test_unknown_user_is_newcomer`,
  `test_maintainer_annotation_format` (asserts the exact "maintainer
  since ~YYYY; N merges at time of comment" string),
  `test_established_and_newcomer_annotation_format`.
- [ ] Run, verify fail.
- [ ] Implement `standing.py`.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): time-varying contributor standing
  derivation`.

---

### Task 2: `Store.issue_or_pr_by_id` — resolve a cross-ref target

**Files:** Modify `src/ghmem/store.py`, `tests/test_store.py`.

**Why.** `cross_refs_for(item_id)` returns `CrossRef` rows keyed by
**`GraphQLId`** (`src_id`/`dst_id`), but `Store` only maps `Number →
row` (`get_issue_or_pr`). To render a cross-reference as `#N: title`,
`show` must resolve the *other* end's id back to its item. This is the
one small, in-mandate addition to the landed store the read layer
needs.

**Interfaces — Produces:**

- `Store.issue_or_pr_by_id(item_id: GraphQLId) -> IssueOrPR | None` —
  the row with that `id`, or `None` if absent (a cross-ref may point at
  an item never fetched into the corpus; the caller renders those as a
  bare id). Mirrors the existing `select(IssueOrPR).where(IssueOrPR.id
  == item_id)` / `Session` pattern already in `store.py`, returning
  `scalar_one_or_none()` directly (no raise — absence is expected).

**Steps:**

- [ ] Add `test_issue_or_pr_by_id` to `tests/test_store.py` using the
  existing `_rebuilt_store` fixture: a known id resolves to the right
  `IssueOrPR` (assert `.number`/`.title`); an unknown `GraphQLId`
  returns `None` (not a raise).
- [ ] Run, verify fail.
- [ ] Implement `issue_or_pr_by_id` in `store.py`.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): resolve an issue/PR by GraphQL id`.

---

### Task 3: `show` — single-thread markdown renderer

**Files:** Create `src/ghmem/show.py`, `tests/test_show.py`,
`tests/fixtures/show_corpus.jsonl`. Modify `src/ghmem/cli.py` (add
`show`).

**Interfaces — Consumes:** `Store` (incl. `issue_or_pr_by_id` from T2),
`StandingTable` + `build_standing` (T1), `Number`, `GraphQLId`,
`IssueOrPRNotFoundError`, `PRDiffNotFoundError`.

**Produces:**

- `render(store: Store, standing: StandingTable, number: Number) -> str`
  — resolves the item via `store.get_issue_or_pr(number)`, so an
  unknown number surfaces as `IssueOrPRNotFoundError` (propagated, not
  caught here).
- `cli.show` — `ghmem show <number>`: builds the store from
  `sqlite_path()`, `build_standing(store)`, prints `render(...)`;
  catches `IssueOrPRNotFoundError` → clean `click.echo(..., err=True)`
  and a non-zero exit (`raise SystemExit(1)` / `ctx.exit(1)`), no
  traceback.

**Rendering contract.** Compact, deterministic markdown (stable
ordering → snapshot-testable):

1. **Header** — `#{number} {title}` line, then `state`, `kind`,
   author annotated via `standing.annotation(item.author,
   item.created_at.date())`, created/updated dates.
2. **Body** — `item.body`.
3. **Timeline** — general comments (`comments_for(item.id)`, keyed on
   `created_at`) merged with reviews (`reviews_for(item.id)`, keyed on
   `submitted_at`), each review followed by its
   `review_comments_for(review.id)` (keyed on `created_at`, each
   showing its `path`). Merge into one stream sorted by `(timestamp,
   id)` for determinism. Every author annotated via
   `standing.annotation(author, <that entry's date>)`.
4. **Cross-references** — for each `cross_refs_for(item.id)` row, take
   the end that is not `item.id`, resolve it with
   `store.issue_or_pr_by_id`; render `#{number}: {title} ({kind})` when
   found, else the bare id + `kind`. Sort by `kind` then id.
5. **Reactions** — `reactions_for(item.id)` as `{content}×{count}`,
   already `content`-ordered by the store.
6. **Diff** (PRs only) — `pr_diff_for(number)` inside `try/except
   PRDiffNotFoundError` (issues have no diff → omit the section). The
   **diffstat file list always renders** (`path +A −D` per
   `FileStat`). The **diff body is truncated when outsized**: emit
   `diff_text` in full when `len(diff_text) <= DIFF_BODY_MAX_CHARS`
   (`# PROVISIONAL — calibrated in subproject 4`), else the first
   `DIFF_BODY_MAX_CHARS` chars followed by an explicit
   `… [diff truncated] …` marker. Also emit the marker when the stored
   `PRDiff.truncated` flag is already set (sync truncated it upstream).
   Thread text is never truncated by `show`.

No linked-commits section and no `author_association` hint (both
deferred — see the Spec-Fidelity Audit).

**Notes.** The `DIFF_BODY_MAX_CHARS` display cap is independent of the
triage serve-gate budget (subproject 2). The standing-annotation format
is the shared one from T1.

**Steps:**

- [ ] Author `tests/fixtures/show_corpus.jsonl`: one merged PR with
  comments and a review-plus-review-comment from users of differing
  standing (reuse standing-relevant `item`/`commit` events so
  annotations resolve to maintainer/established/newcomer), a `pr_diff`
  whose `diffText` exceeds `DIFF_BODY_MAX_CHARS`, a `cross_ref` to
  another in-corpus issue, and `reaction` rows.
- [ ] Write `tests/test_show.py` (When/Then docstrings):
  `test_renders_standing_annotations` (maintainer/established/newcomer
  strings appear against the right authors),
  `test_timeline_is_chronological` (comments and review interleave by
  timestamp),
  `test_diff_file_list_always_present`,
  `test_large_diff_body_truncated_with_marker`,
  `test_cross_refs_resolved_to_number_and_title`,
  `test_reactions_rendered`,
  `test_issue_without_diff_omits_diff_section`,
  `test_unknown_number_raises` (`render` propagates
  `IssueOrPRNotFoundError`),
  `test_cli_show_unknown_number_exits_nonzero` (via
  `click.testing.CliRunner`).
- [ ] Run, verify fail.
- [ ] Implement `show.py`, wire `cli.show`.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): compact single-thread markdown renderer`.

---

## Self-Review

- **Spec coverage (Part 2 slice):** standing derivation (T1), `show`
  (T3), plus the `issue_or_pr_by_id` store primitive `show` needs (T2).
  `sync`/client and the master commit-log population are Part 3.
- **Approved deferrals:** linked commits (no PR↔commit linkage in the
  landed schema) and the `author_association` recent-only hint (no
  now-anchor in a deterministic renderer) are both out of Part 2 by
  decision, recorded in the Spec-Fidelity Audit. If a reviewer wants
  either in scope, it is an added task, not a rewrite.
- **API fidelity:** every consumed name matches the landed Part 1
  surface (`get_issue_or_pr`, `issues_or_prs_authored_by`,
  `review_comments_for`, `IssueOrPRNotFoundError`, `PRDiffNotFoundError`,
  GraphQLId-keyed children). The stale pre-rename names (`get_item`,
  `items_authored_by`, `ItemNotFoundError`, stdlib `sqlite3`) are gone.
- **Type consistency:** `StandingTable.of(user, when)` /
  `annotation(user, when)` names match across T1/T3; `render(store,
  standing, number: Number)` uses Part 1's `Number` and surfaces its
  `IssueOrPRNotFoundError`; T3 consumes T2's
  `issue_or_pr_by_id(...) -> IssueOrPR | None`.
- **Provisional discipline:** `MERGE_THRESHOLD`,
  `ACCEPTED_PR_THRESHOLD`, `OWNER_LOGIN`, `DIFF_BODY_MAX_CHARS` all
  marked provisional; calibration deferred to subproject 4. The
  commit-author-vs-login approximation is flagged in T1, not silently
  resolved.
- **PR-size cap:** three commits map to three reviewable slices, each
  well under 400 lines.

## Execution Handoff

Part 2 plan complete; Part 1 has landed, so it is ready to execute. Two
options:

1. **Subagent-Driven (recommended)** — fresh Sonnet subagent per task,
   Opus review between tasks (per SDD reviewer-tiering), fast
   iteration.
2. **Inline Execution** — execute tasks in this session with
   checkpoints.

After Part 2 lands, execute Part 3 (`…-corpus-sync.md`). Which
approach?

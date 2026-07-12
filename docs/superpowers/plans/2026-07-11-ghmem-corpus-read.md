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
> **Part 1 — Store** (`…-corpus-store.md`), **Part 2 — Read** (this
> file), **Part 3 — Sync** (`…-corpus-sync.md`). This part builds the
> two offline read-layer commands over Part 1's `Store`. It **depends
> on Part 1**, which must land first: it consumes Part 1's `Store`,
> `Number`, and `ItemNotFoundError`, and is fully testable against
> hand-authored fixture ledgers (no network).

**Goal:** Derive time-varying contributor standing from the corpus and
render any single thread as compact, standing-annotated markdown —
both reading only Part 1's local `Store`.

**Architecture:** Deterministic and offline. `standing` derives
owner/maintainer/established/newcomer as of any date from merge
authority, master-commit authorship, and accepted-PR counts; `show`
resolves one item and renders its thread with those annotations
inline. Both are pure functions over the SQLite store, testable
without network.

**Tech Stack:** Python ≥3.14, uv, click, stdlib `sqlite3`, pytest +
pytest-mock, ruff (`select=ALL`), mypy strict.

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
  docformatter, a justfile `green` recipe.
- No agent ever calls the GitHub API; agents (later subprojects) read
  only what `ghmem` emits.
- Formula constants (standing thresholds) ship **provisional** and are
  calibrated in subproject 4 — never hip-shot final values.
- Hard cap on human-reviewed artifacts, this plan and every resulting
  PR alike: **400 lines**; anything longer is split (sole PR
  exception: pure code-reorg with no behavioral change).

---

## Spec-Fidelity Audit (Part 2 slice)

The spec assigns corpus this slice (§Execution 1): *"scaffold,
direnv-activated venv, fetch + routing ledgers with SQLite mirror,
`sync` (including PR diffs), standing derivation, `show`."* Part 2
covers the two read-layer items:

- standing derivation (time-varying, from merge authority / master
  commits / accepted-PR counts) → **T1**
- `show` (compact markdown, standing annotations,
  diff-truncated-but-file-list-always, cross-refs, reactions, linked
  commits) → **T2**

`show`'s standing-annotation format is shared with `triage`/`topic`
output (later subprojects) but only `show` consumes it here.

## File Structure

Adds to the `tools/ghmem/` uv project built in Part 1.

Created in Part 2:

- `src/ghmem/standing.py` — `build_standing`, `StandingTable`
- `src/ghmem/show.py` — single-thread markdown renderer
- `tests/test_standing.py`, `tests/test_show.py`,
  `tests/fixtures/standing_corpus.jsonl`,
  `tests/fixtures/show_corpus.jsonl`

Modified in Part 2:

- `src/ghmem/cli.py` — add `show`

**Consumed from Part 1** (not redefined here): `Store` and its query
API (`get_item`, `comments_for`, `reviews_for`, `reactions_for`,
`cross_refs_for`, `pr_diff_for`, `commits`, `merges_before`,
`items_authored_by`), the `Number` id `NewType`, and
`ItemNotFoundError`.

---

### Task 1: Contributor standing derivation

**Files:** Create `src/ghmem/standing.py`, `tests/test_standing.py`,
`tests/fixtures/standing_corpus.jsonl`.

**Interfaces — Consumes:** `Store` (Part 1).

**Produces:**

- `build_standing(store: Store) -> StandingTable`.
- `class Standing(StrEnum)`: members via `enum.auto()` — `OWNER`,
  `MAINTAINER`, `ESTABLISHED`, `NEWCOMER` (StrEnum + `auto()` yields
  the lowercased member name as value, so serialization is free and
  self-describing).
- `class StandingTable`; `of(user: str, when: date) -> Standing`;
  `annotation(user: str, when: date) -> str` — lives on the table (it
  needs corpus-derived counts/dates, which the enum member cannot
  hold), renders the inline form `dmaluka (maintainer since ~2022; 180
  merges at time of comment)`.

**Derivation (time-varying).** For each `when`, classify from corpus
evidence dated `<= when`: **merge authority** (`merged_by` on merged
PRs — the hardest signal) ⇒ MAINTAINER once cumulative merges cross a
threshold; repo `owner` (zyedidia) is OWNER; **master-branch commit
authorship** and **own-PRs-accepted count** promote
NEWCOMER→ESTABLISHED. Standing is *computed*, never inferred by
agents; `author_association` is not consulted here (renderers show it
separately as a recent-only hint).

**Provisional constants — calibrated in subproject 4, do NOT finalize
now.** Merge-count and accepted-PR thresholds ship as documented
provisional values with a `# PROVISIONAL — calibrated in subproject 4`
marker. Ground-truth anchors the tests, not the constants.

**Steps:**

- [ ] Author `tests/fixtures/standing_corpus.jsonl`: merges by
  `dmaluka` starting ~2022, by `JoeKar`, master commits, and PRs by a
  newcomer — enough to exercise each class and an era boundary.
- [ ] Write `tests/test_standing.py`:
  `test_dmaluka_maintainer_after_2022_not_before` (era boundary),
  `test_joekar_maintainer`, `test_owner_is_owner`,
  `test_unknown_user_is_newcomer`, `test_annotation_format`.
- [ ] Run, verify fail.
- [ ] Implement `standing.py`.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): time-varying contributor standing
  derivation`.

---

### Task 2: `show` — single-thread markdown renderer

**Files:** Create `src/ghmem/show.py`, `tests/test_show.py`,
`tests/fixtures/show_corpus.jsonl`. Modify `src/ghmem/cli.py` (add
`show`).

**Interfaces — Consumes:** `Store`, `StandingTable` (T1), `Number`,
`ItemNotFoundError`.

**Produces:**

- `render(store: Store, standing: StandingTable, number: Number) ->
  str` — resolves the item via `store.get_item(number)`, so an unknown
  number surfaces as `ItemNotFoundError`.
- `cli.show` — `ghmem show <number>` prints the rendering; catches
  `ItemNotFoundError` → clean stderr message, non-zero exit.

**Rendering contract.** Compact markdown: title/state/dates header;
body; comments and review threads in chronological order; then the PR
diff. Every author annotated inline with
`standing.annotation(author, posting_date)`. Diff rules (spec §show):
the **file list (diffstat) always renders**; the diff **body is
truncated when outsized** with an explicit truncation marker; thread
text is never truncated by `show`. Also render cross-references,
reactions, and linked commits. Output is deterministic (stable
ordering) so it is snapshot-testable.

**Notes:** The "outsized" diff-body threshold for `show` is a display
convenience (a constant), independent of the triage serve-gate budget
(subproject 2). Standing annotation format is the shared one from T1.

**Steps:**

- [ ] Author `tests/fixtures/show_corpus.jsonl`: one PR with comments
  from users of differing standing, a large diff, cross-refs,
  reactions, a linked commit.
- [ ] Write `tests/test_show.py`: `test_renders_standing_annotations`,
  `test_diff_file_list_always_present`,
  `test_large_diff_body_truncated_with_marker`,
  `test_cross_refs_reactions_commits_rendered`,
  `test_unknown_number_errors`.
- [ ] Run, verify fail.
- [ ] Implement `show.py`, wire `cli.show`.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): compact single-thread markdown renderer`.

---

## Self-Review

- **Spec coverage (Part 2 slice):** standing (T1), show (T2). Both
  read Part 1's `Store`; `sync`/client are Part 3.
- **Type consistency:** `StandingTable.of(user, when)` and
  `StandingTable.annotation(user, when)` names match across T1/T2
  (annotation lives on the table, not the enum); `render(...number:
  Number)` uses Part 1's `Number` and surfaces its `ItemNotFoundError`.
- **Provisional discipline:** standing thresholds marked provisional
  (T1), calibration deferred to subproject 4 — no hip-shot finals.
- **PR-size cap:** two commits map to reviewable slices.

## Execution Handoff

Part 2 plan complete; execute **after** Part 1 lands. Two execution
options:

1. **Subagent-Driven (recommended)** — fresh Sonnet subagent per task,
   Opus review between tasks (per SDD reviewer-tiering), fast
   iteration.
2. **Inline Execution** — execute tasks in this session with
   checkpoints.

After Part 2 lands, execute Part 3 (`…-corpus-sync.md`). Which
approach?

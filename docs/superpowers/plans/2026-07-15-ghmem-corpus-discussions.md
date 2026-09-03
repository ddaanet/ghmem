# ghmem Subproject 1 (corpus), Discussions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Plan style — read before implementing.** Like the store/read/sync
> corpus plans, this plan is **contract-style**: it gives interface
> contracts, test lists, and the one genuinely tricky algorithm — **not**
> full code listings. Implementers are Sonnet under the toolchain gates
> below with one-tier-up (Opus) review; they fill in the bodies. Every
> task is still TDD: write the failing test, watch it fail, implement,
> watch it pass, commit.

**Goal:** Add GitHub Discussions to the corpus end-to-end — a
`Discussion` + `DiscussionComment` store contract, ledger replay, and a
`ghmem discussion <number>` renderer — so subproject 1 Part 3 (sync) can
emit discussion events into a real home. Fold in the one remaining
`forbid_unknown_fields` contract-hardening.

**Architecture:** Discussions are modeled as their own declarative
entities (a Discussion is not an Issue: no state/merge/diff, but a
category, an accepted answer, and one-level comment replies). Two new
`_RawNode` structs decode the raw-node events; the `_RAW_BY_TYPE`
registry auto-wires them into `rebuild_from_ledgers` with no replay edit.
A separate `ghmem discussion` command renders one discussion, because
discussions carry a number sequence disjoint from issues/PRs.

**Tech Stack:** Python ≥3.14, uv, click, msgspec, SQLAlchemy 2.0 typed
declarative, SQLite, pytest (+pytest-mock), ruff (`select=ALL`), mypy
strict, docformatter, justfile `green`.

## Global Constraints

Copied from the spec (`docs/ghmem-design.md`)
and the landed corpus code; every task's requirements implicitly include
these.

- Python floor **>=3.14**; no stringified forward references.
- Toolchain held to the strictest bar (edify config): uv-managed, click
  CLI, pytest (+pytest-mock), `ruff select=["ALL"]` with the curated
  ignore list, `mypy strict` + `extra_checks`, docformatter, the
  justfile `green` recipe. Gate every task on `just green`.
- **Docstring dead-zone:** keep one-line docstring summaries ≤80 total
  chars, or write a real multi-line docstring (blank line after the
  summary). docformatter `wrap-summaries=80` vs ruff line-length 88
  reflows an 81–88-char summary to two lines, which then trips ruff
  D205. There is no `just green` fixed point otherwise.
- Storage: SQLite is a derived cache regenerated from the ledgers; the
  declarative entities own schema, upserts (`session.merge`), and
  row-loading. Discussions add tables, never touch the ledger format.
- Human-reviewed-artifact cap: **400 lines** for this plan and each
  resulting PR; anything longer is split.

---

## Scope Note — hygiene already resolved

Two "cheap hygiene" items flagged for this slice were verified
**already landed**, so they are **not** tasks here:

- `Ledger.exists()` **is** tested — `test_exists_tracks_first_append`
  in `tests/test_ledger.py`.
- The pytest config is clean — `strict_markers`/`strict_config` (pytest)
  and `strict` (mypy) are all valid real options; there is no no-op key.

The only hardening folded in is `forbid_unknown_fields=True` (Task 1).

## Spec-Fidelity Audit (Discussions slice)

The spec's harness section lists `ghmem discussion <number>` — "renders
one discussion as compact markdown: body, then comments interleaved with
their one-level replies, the accepted answer marked, and reactions — no
diff, reviews, merge line, or cross-references; authors carry the same
standing annotation as `show`; discussion activity is display-only and
does not feed the standing derivation." The spec's `sync` bullet already
lists discussions among the fetched connections.

- store contract (entities + raw structs + queries) → **T1**
- `ghmem discussion` renderer + CLI → **T2**
- display-only standing → **T2** reuses `StandingTable.annotation`;
  `standing.py` is untouched (no new standing input).
- **Deferred, correctly downstream (not this slice):** the sync driver
  that *emits* discussion events is Part 3; it must select exactly the
  contract keys per event (Task 1's `forbid_unknown_fields` enforces
  this once real responses flow).

## File Structure

Adds to and extends the `tools/ghmem/` uv project.

Created:

- `tests/test_discussion.py` — render + CLI tests for the new command.

Modified:

- `src/ghmem/aliases.py` — add `DiscussionNumber` NewType.
- `src/ghmem/models.py` — add `Discussion`, `DiscussionComment`; register
  `DiscussionNumber` in `type_annotation_map`.
- `src/ghmem/ingest.py` — add `_RawCategory`, `_RawIdRef` + `_id_of`,
  `_RawDiscussion`, `_RawDiscussionComment`; extend the contract
  docstring; add `forbid_unknown_fields=True` to the `_RawNode` base.
- `src/ghmem/store.py` — add `get_discussion`, `discussion_by_id`,
  `discussion_comments_for`.
- `src/ghmem/exceptions.py` — add `DiscussionNotFoundError`.
- `src/ghmem/show.py` — add `render_discussion`, reusing
  `_render_reactions` and the `StandingTable.annotation` pattern.
- `src/ghmem/cli.py` — add the `discussion` command.
- `tests/test_store.py` — discussion parse/replay/query cases.

**Consumed from Parts 1–2** (not redefined): the `_RawNode` base +
`_RAW_BY_TYPE` registry + `parse`/`to_entity` protocol, `_RawLogin` +
`_login_of`, `Base`/`MappedAsDataclass`, `GraphQLId`, the `Store` query
patterns (`get_issue_or_pr` raises, `issue_or_pr_by_id` returns `None`),
`StandingTable.annotation(login, date)`, `show.py`'s `_render_reactions`.

---

### Task 1: Discussion store contract + `forbid_unknown_fields`

**Files:** Modify `src/ghmem/aliases.py`, `src/ghmem/models.py`,
`src/ghmem/ingest.py`, `src/ghmem/store.py`, `src/ghmem/exceptions.py`.
Test: `tests/test_store.py`, `tests/test_ledger.py` (unchanged — verify
still green).

**Interfaces — Consumes:** `_RawNode`, `_RAW_BY_TYPE`, `_RawLogin`,
`_login_of`, `Base`, `GraphQLId`, `Store`.

**Produces:**

- `aliases.DiscussionNumber = NewType("DiscussionNumber", int)`.
- `models.Discussion(Base)` — columns: `id: GraphQLId` (pk),
  `number: DiscussionNumber` (unique), `title: str`, `body: str`,
  `author: str`, `created_at: datetime`, `updated_at: datetime`,
  `category: str`, `answer_id: GraphQLId | None`,
  `author_association: str | None`. Table name `discussion`.
- `models.DiscussionComment(Base)` — columns: `id: GraphQLId` (pk),
  `discussion_id: GraphQLId`, `parent_id: GraphQLId | None`,
  `author: str`, `created_at: datetime`, `updated_at: datetime`,
  `author_association: str | None`, `body: str`. Table name
  `discussion_comment`.
- `models.Base.type_annotation_map` gains `DiscussionNumber: Integer`.
- `ingest._RawCategory(msgspec.Struct)` — `name: str`.
- `ingest._RawIdRef(msgspec.Struct)` — `id: GraphQLId`; helper
  `_id_of(ref: _RawIdRef | None) -> GraphQLId | None` (mirrors
  `_login_of`; `None` → `None`).
- `ingest._RawDiscussion(_RawNode)` — `_event_type = "discussion"`.
  Fields (camelCase wire keys): `id: GraphQLId`,
  `number: DiscussionNumber`, `title: str`, `body: str`,
  `author: _RawLogin | None`, `created_at: datetime`,
  `updated_at: datetime`, `category: _RawCategory`,
  `answer: _RawIdRef | None`, `author_association: str | None = None`.
  `to_entity()` → `Discussion(category=self.category.name,
  answer_id=_id_of(self.answer), author=_login_of(self.author), ...)`.
- `ingest._RawDiscussionComment(_RawNode)` —
  `_event_type = "discussion_comment"`. Fields: `id: GraphQLId`,
  `discussion_id: GraphQLId` (**injected**), `author: _RawLogin | None`,
  `created_at: datetime`, `updated_at: datetime`,
  `body: str`, `author_association: str | None = None`,
  `parent_id: GraphQLId | None = None` (**injected**; omitted/null on a
  top-level comment, the parent comment id on a reply). `to_entity()` →
  `DiscussionComment(author=_login_of(self.author), ...)`.
- `store.get_discussion(number: DiscussionNumber) -> Discussion` —
  raises `DiscussionNotFoundError` on miss (mirror `get_issue_or_pr`).
- `store.discussion_by_id(discussion_id: GraphQLId) -> Discussion | None`
  — returns `None` on miss (mirror `issue_or_pr_by_id`).
- `store.discussion_comments_for(discussion_id: GraphQLId) ->
  list[DiscussionComment]` — ordered by `created_at`.
- `exceptions.DiscussionNotFoundError(LookupError)`.
- `_RawNode` base gains `forbid_unknown_fields=True` (a struct-config
  kwarg on `class _RawNode(msgspec.Struct, rename="camel", kw_only=True,
  forbid_unknown_fields=True)`).

**Contract-docstring extension.** Add `_RawDiscussion` and
`_RawDiscussionComment` to `ingest.py`'s module-docstring contract, in
the existing register: the `"discussion"` event's `data` requires
`id`/`number`/`title`/`body`/`author`/`createdAt`/`updatedAt`/`category`
(a `{name}` node) / `answer` (a `{id}` node or `null`); optional
`authorAssociation`. The `"discussion_comment"` event requires
`id`/`author`/`createdAt`/`updatedAt`/`body` plus **injected**
`discussionId` and **injected** `parentId` (null on a top-level
comment); optional `authorAssociation`. Note the new hard rule:
`forbid_unknown_fields` means every key on a raw-node `data` object must
be a declared field — the Part 3 driver must select exactly these keys.

**Tricky point — `forbid_unknown_fields` blast radius.** The flag lands
on the `_RawNode` base, so it governs every event struct
(`item`/`comment`/`review`/`review_comment`/`reaction`/`cross_ref`/
`commit`/`pr_diff` + the two new ones), not the nested value structs
(`_RawLogin`/`_RawFileStat`/`_RawCategory`/`_RawIdRef`, which stay
lenient). Existing fixtures carry exactly the contract keys, so the 38
current tests must stay green after the flag — that is the regression
check, not a new behavior. Add one test proving the flag rejects a
surprise key.

**Steps:**

- [ ] Add `forbid_unknown_fields=True` to `_RawNode`; run the full suite
  (`uv run pytest`) and confirm the existing 38 tests still pass
  (regression gate for the flag).
- [ ] Write `tests/test_store.py` cases:
  `test_discussion_roundtrips_through_rebuild` (a `discussion` +
  `discussion_comment` (top-level) + reply (`parentId` set) + `reaction`
  ledger → rebuild → `get_discussion`/`discussion_comments_for` return
  them, comments ordered by `created_at`),
  `test_discussion_ghost_author` (null `author` → `"ghost"`),
  `test_discussion_null_answer_and_category`
  (`answer: null` → `answer_id is None`; `category.name` stored),
  `test_get_discussion_missing_raises` (`DiscussionNotFoundError`),
  `test_discussion_by_id_missing_returns_none`,
  `test_raw_node_rejects_unknown_key` (a `data` with a stray key →
  `msgspec` raises).
- [ ] Run, verify fail.
- [ ] Implement `aliases`, `models`, `ingest` structs + docstring,
  `store` queries, `exceptions`.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): discussion store contract + forbid unknown
  raw-node fields`.

---

### Task 2: `ghmem discussion` renderer + CLI

**Files:** Modify `src/ghmem/show.py`, `src/ghmem/cli.py`. Create
`tests/test_discussion.py`.

**Interfaces — Consumes:** `Store` (T1 queries), `StandingTable`,
`show.py`'s `_render_reactions`, `Discussion`/`DiscussionComment`.

**Produces:**

- `show.render_discussion(store: Store, standing: StandingTable, number:
  DiscussionNumber) -> str` — deterministic markdown; propagates
  `DiscussionNotFoundError` uncaught (mirror `render`).
- `cli.discussion` — `ghmem discussion <number>` (`@click.argument
  ("number", type=int)`); builds the store + standing table like `show`,
  calls `render_discussion(store, table, DiscussionNumber(number))`,
  catches `DiscussionNotFoundError` → stderr
  `ghmem discussion: no discussion numbered #<n>`, exit 1.

**Rendering contract.** In order:

1. Header: `# discussion #<number> <title>`, a category line, and the
   author annotated with `standing.annotation(author, created_at.date())`
   — same annotation form `show` uses.
2. Blank line, then `body`.
3. Comments, interleaved chronologically. **Reply nesting (the tricky
   bit):** top-level comments are those with `parent_id is None`, ordered
   by `created_at`; each is followed immediately by its replies (the
   comments whose `parent_id == comment.id`), ordered by `created_at`,
   rendered indented one level. GitHub discussions nest exactly one
   level, so no recursion — a two-pass group-by-`parent_id` over the
   single `discussion_comments_for` list. Each comment/reply annotates
   its author by its own date.
4. The accepted answer: the comment whose `id == discussion.answer_id`
   (if any) is marked (e.g. a `**✓ Accepted answer**` prefix line). Mark
   in place; do not reorder.
5. Reactions on the discussion via `_render_reactions(store,
   discussion.id)`.

No diff, reviews, merge line, or cross-references.

**Placement decision:** `render_discussion` lives in `show.py` (which is
152 lines) to reuse `_render_reactions` without extracting a shared
module prematurely; the file lands ~230 lines, well within norms. Split
later only if it grows unwieldy.

**Steps:**

- [ ] Write `tests/test_discussion.py`: build a store from a synthetic
  discussion (title/category/body, two top-level comments, one reply to
  the first, an accepted answer = the second top-level comment,
  reactions) via the same rebuild-from-fixtures fixture pattern as
  `test_show.py`. Assert:
  `test_render_discussion_golden` (full markdown snapshot: header,
  category, body, chronological comments with the reply indented under
  its parent, the answer marked, reactions),
  `test_reply_indented_under_parent`,
  `test_accepted_answer_marked`,
  `test_no_answer_no_marker` (`answer_id is None` → no marker anywhere),
  `test_discussion_command_not_found_exits_1` (CLI, missing number →
  exit 1, stderr message).
- [ ] Run, verify fail.
- [ ] Implement `render_discussion` in `show.py`; wire `cli.discussion`.
- [ ] `just green`.
- [ ] Commit: `feat(ghmem): ghmem discussion renderer and command`.

---

## Self-Review

- **Spec coverage:** the `ghmem discussion` bullet's every clause maps to
  T2 (body/replies/answer/reactions render; no diff/reviews/merge/xref;
  standing annotation; display-only). The store contract that lets sync
  emit discussions is T1. `standing.py` untouched (display-only). ✓
- **Type consistency:** `DiscussionNumber` is the number type across
  aliases/models/store/CLI; `get_discussion` raises and
  `discussion_by_id` returns `None`, matching the issue/PR pair;
  `render_discussion(store, standing, number)` mirrors `render`'s
  signature; `_id_of`/`_login_of` share shape; the `_event_type` strings
  (`"discussion"`, `"discussion_comment"`) match the injected-key notes.
- **Placeholder scan:** none — every interface is a concrete signature,
  every test a named assertion.
- **PR-size cap:** two commits (store contract, renderer+CLI) at the same
  store/read seam Parts 1–2 split on; each is a reviewable slice under
  400 lines.

## Execution Handoff

Plan complete. Land this **before** planning Part 3 (sync) — Part 3
depends on the `discussion`/`discussion_comment` contract landing here.
Two execution options:

1. **Subagent-Driven (recommended)** — fresh Sonnet subagent per task,
   Opus review between tasks (per SDD reviewer-tiering).
2. **Inline Execution** — execute tasks in this session with checkpoints.

Which approach?

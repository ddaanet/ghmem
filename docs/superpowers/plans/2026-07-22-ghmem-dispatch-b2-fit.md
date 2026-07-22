# ghmem dispatch B2 — solo-item fit

> **For agentic workers:** REQUIRED SUB-SKILL: use
> superpowers:subagent-driven-development to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Spec: `docs/superpowers/specs/2026-07-22-ghmem-b2-solo-item-fit-design.md`

**Goal:** Truncate a session's first item's diff against whatever the
batch budget has left, serve it oversized when even that is not enough,
and record which of the three outcomes happened in the packet and the
ledger.

**Architecture:** `show.render` gains a diff-body budget and a cut rule
that never overshoots it. `triage.serve_next` uses that budget only on
the first item of a session, and only when the packet exceeds
`BATCH_CHARS`: it measures a floor render with the diff body removed,
gives the diff whatever remains, and falls back to serving the floor
whole. Every `dispatched` event carries a `fit` tag naming the outcome.

**Tech Stack:** Python 3.14, click, SQLAlchemy 2.0 typed declarative,
msgspec, pytest. `just green` is the gate.

## This plan is contract-style

Signatures, types, names and boundary-crossing literal strings are
given verbatim — a mismatch there is a real defect a single-task
reviewer cannot catch. Everything else is a contract: precondition,
postcondition, invariant, and the mutation that must kill it. **Write
the code yourself.** Do not expect a listing to copy; there isn't one,
deliberately. This deviates from `superpowers:writing-plans`, which
demands full code, and the deviation is approved.

## Global Constraints

- Gate, run from `tools/ghmem`:
  `PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH" just green`
- **Sequential only.** One implementer at a time in the main tree
  `/Users/david/code/micro`, against the existing `tools/ghmem/.venv`.
  No worktrees, no `uv sync`, no background jobs, no parallel suites.
- **Implementers do not commit.** The controller commits on `main`,
  alone — the pre-commit hook is a memory hog and must not run
  alongside a test suite.
- Big scratch goes in the project's `tmp/`, never `/tmp`.
- 400-line cap per file, enforced by the gate. `show.py` is at 322,
  `triage.py` at 219, `test_show.py` at 213 — all have room. Do not
  let any of them cross 400; surface a split instead of shaving
  docstrings.
- `ruff select = ["ALL"]`, `mypy --strict`, docformatter. Every new
  function gets a docstring; every new test gets a `When:`/`Then:`
  docstring, matching the existing modules.
- Entry points first, definitions after their users. Never front-load
  constants.
- Both budget constants stay PROVISIONAL for subproject 4.

---

## Task 1: The diff budget and the non-overshooting cut

**Files:**
- Modify: `tools/ghmem/src/ghmem/show.py:39` (constant),
  `:56-68` (`render`), `:299-317` (`_render_diff`)
- Create: `tools/ghmem/tests/diff_fixtures.py`
- Test: `tools/ghmem/tests/test_show.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `show.DIFF_BODY_MAX_CHARS: int` — now `25_000`.
  - `show.render(store, standing, number, *, diff_max_chars: int = DIFF_BODY_MAX_CHARS) -> str`
  - `tests.diff_fixtures.LINE_WIDTH: int` — `40`
  - `tests.diff_fixtures.DIFF_HEADER_LINE: str`
  - `tests.diff_fixtures.diff_body_line(index: int) -> str`
  - `tests.diff_fixtures.big_diff_text(lines: int) -> str`
  - `tests.diff_fixtures.write_diff_corpus(path: Path, *, diff_lines: int) -> None`

- [ ] **Step 1: Write the fixture generator**

Create `tools/ghmem/tests/diff_fixtures.py` — uncollected, no `test_`
prefix, the arrangement `triage_helpers.py` and `sync_helpers.py`
already use. Its module docstring must say why it exists: large diffs
are generated rather than versioned as blobs, because a
25,000-character diff inside a JSONL line is neither readable nor
reviewable, and it is the generator that gets reviewed.

Contracts:

- `LINE_WIDTH = 40` — the exact width of one generated body line,
  newline included. Fixed so a test converts between "lines of diff"
  and "characters of body" without re-deriving the formatting.
- `DIFF_HEADER_LINE` — the single `diff --git` line every generated
  diff opens with, newline excluded.
- `diff_body_line(index)` — the `index`-th body line, newline
  excluded, so `len(...) == LINE_WIDTH - 1`. Distinct per index, and
  its distinguishing text must not be a substring of `LINE_WIDTH`'s
  padding — a fit test asserts a specific line's absence. Exported so
  a test asserts which lines survived a cut without restating the
  formatting.
- `big_diff_text(lines)` — `DIFF_HEADER_LINE`, then exactly `lines`
  body lines. Invariant: the body after the header is exactly
  `lines * LINE_WIDTH` characters, so a caller sizes a diff against a
  character budget directly.
- `write_diff_corpus(path, *, diff_lines)` — write a **fetch** ledger
  (JSONL, one `{"type": ..., "data": ...}` per line) holding exactly
  one `item` event of `__typename` `PullRequest`, number `1`, plus one
  `pr_diff` event for PR 1 whose `diffText` is `big_diff_text(diff_lines)`,
  `truncated` false, and whose `files` list is the single entry
  `src/big.go` with `additions=diff_lines, deletions=0`. One item so
  heat order is unambiguous and the PR is always a session's first
  served item — the only item the fit path touches. Copy the item
  event's field names and shapes from `tests/fixtures/show_corpus.jsonl`;
  the PR's `body` must be a distinctive sentence a test can assert
  survived intact.

- [ ] **Step 2: Write the failing tests**

In `tools/ghmem/tests/test_show.py`, add a `diff_store` fixture beside
the existing `standing_table` fixture: it writes a generated corpus of
200 diff lines into `tmp_path`, opens a `Store`, and rebuilds it from
that fetch ledger plus an empty routing ledger. Follow the existing
fixtures in the module for the exact `Store.open` /
`rebuild_from_ledgers` shape.

Replace `test_large_diff_body_truncated_with_marker` (`:78-93`) — it
drives the 645-character `show_corpus` diff, which no longer reaches
the raised cap — with two tests:

`test_diff_body_cut_at_a_whole_line_within_the_cap`

> **When:** a 200-line generated diff is rendered with `diff_max_chars`
> set to five lines' worth.
> **Then:** the body never exceeds the cap — pinning the cut as a real
> ceiling rather than the first line boundary past it — every line it
> kept is whole and in order starting with `DIFF_HEADER_LINE`, and
> `… [diff truncated] …` follows.

Assert the ceiling (`len(body) <= cap`), that more than the header
survived, and that the surviving body lines equal
`diff_body_line(index)` for the indices they occupy. Do **not** assert
a fixed surviving line count — derive it from what came back. Extract
the body by slicing between the fence and the marker.

`test_diff_max_chars_zero_renders_the_file_list_and_no_body`

> **When:** the PR is rendered with `diff_max_chars=0`.
> **Then:** no diff body line survives, the marker is present, and the
> diffstat file list still renders — the floor the fit computation
> measures against.

Replace `test_diff_body_max_chars_is_modest` (`:205-213`, asserting
`< 1000`) with `test_diff_body_max_chars_is_a_safety_valve`:

> **When:** `DIFF_BODY_MAX_CHARS` is imported.
> **Then:** `10_000 <= DIFF_BODY_MAX_CHARS <= 50_000` — large enough
> that an ordinary PR diff renders whole (the median PR touches 16
> lines), small enough to stay a fraction of one batch rather than a
> ceiling on real diffs.

- [ ] **Step 3: Run the tests to verify they fail**

Run from `tools/ghmem`: `.venv/bin/pytest tests/test_show.py -q`

Expected: the two cut tests fail with `TypeError: render() got an
unexpected keyword argument 'diff_max_chars'`; the valve test fails on
`assert 10_000 <= 300`.

- [ ] **Step 4: Implement**

In `tools/ghmem/src/ghmem/show.py`:

**The constant.** `DIFF_BODY_MAX_CHARS = 25_000`, keeping the
`# PROVISIONAL — calibrated in subproject 4` marker, and restate its
docstring: this is a safety valve on one rendered diff body, not a
display budget. Ground it — the median PR in this corpus touches 16
lines and only 10 of 1,299 exceed 2,000, so an ordinary diff renders
whole and only the vendor-dump tail is cut, a tail whose body carries
no decision signal beyond its file list. Roughly 6,000 tokens, about
an eighth of one triage batch.

**`render`.** Gains keyword-only
`diff_max_chars: int = DIFF_BODY_MAX_CHARS` and forwards it to
`_render_diff`. Nothing else about the function changes. Its docstring
gains a paragraph: the default is the safety valve, which is what
`ghmem show` uses; `triage` passes a budget derived from what a batch
has left and is the only caller that ever narrows it.

**`_render_diff`.** Takes the cap as a parameter. Unchanged: an issue
has no diff, so `PRDiffNotFoundError` omits the section entirely, and
the diffstat file list always renders in full whatever the cap — it is
the part of a diff that keeps its signal when the body is gone. The
`… [diff truncated] …` marker (that exact string, existing) is emitted
when the body was cut **or** when the fetched diff was already
`truncated`.

**A new `_cut_diff_body(diff_text, diff_max_chars) -> tuple[str, bool]`**
below it, returning the cut body and whether a cut happened.

- Precondition: none; any cap including `0` and negatives is legal.
- Postcondition: `len(returned_body) <= diff_max_chars` always, and
  the body is a whole number of complete lines, and it is a prefix of
  `diff_text`. A body within the cap is returned untouched with
  `False`.
- Invariant that carries the whole task: **the cap is a real
  ceiling.** The old rule cut at the first line boundary *past* the
  cap — harmless for a display cap, wrong for a caller sizing a packet
  against a budget. Keep whole lines up to the cap and drop the line
  that would cross it. A cap of zero, and a first line longer than the
  cap, both keep nothing; the diffstat above still renders.
- Mutation that must kill: reverting to a forward search for the first
  newline at or past the cap must make
  `test_diff_body_cut_at_a_whole_line_within_the_cap` fail on
  `len(body) <= cap`.

- [ ] **Step 5: Run the full gate**

Run from `tools/ghmem`:
`PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH" just green`

Expected: PASS. `ghmem show` is unchanged for callers, so no other
test module should move.

- [ ] **Step 6: Report — do not commit**

Write `.superpowers/sdd/b2-task-1-report.md`. State the gate's pass
count, and confirm the mutation above: apply it, confirm the named
test dies on the named assertion, restore, re-run.

---

## Task 2: The `fit` tag on the dispatch event

**Files:**
- Modify: `tools/ghmem/src/ghmem/ledger.py:143-155`,
  `tools/ghmem/src/ghmem/triage.py:116-123`
- Modify (mechanical): `tools/ghmem/tests/test_dispatch_store.py`,
  `tests/test_routing_live.py`, `tests/test_queue.py`,
  `tests/test_requeue.py`,
  `tests/fixtures/status_routing.jsonl`,
  `tests/fixtures/routing_events.jsonl`,
  `tests/test_status.py:80`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `ledger.DispatchFit = Literal["fit", "diff_cut", "oversized"]`
  - `ledger.DispatchedData` gains a required `fit: DispatchFit`.

- [ ] **Step 1: Add the type and the field**

In `tools/ghmem/src/ghmem/ledger.py`, declare above `DispatchedData`:

```python
type DispatchFit = Literal["fit", "diff_cut", "oversized"]
```

Those three members are exact and cross a task boundary — Task 3
returns them and the tests assert them by string.

Document them: `fit` — the packet was within budget as rendered;
`diff_cut` — it was a session's first item and its diff body was
truncated to bring it within budget; `oversized` — it was a session's
first item and its thread text and file list alone exceed the budget,
so it was served whole, neither of those being cuttable.

Record why the field exists at all: subproject 4 calibrates
`BATCH_CHARS` against it — a corpus producing many `oversized` serves
has a budget set too low. Like `RequeueData.session`, it is a
write-only signal; the reducer never reads it back, so it gets no
`Dispatch` column. One tag rather than a pair of booleans, because the
three states are mutually exclusive and a pair would admit a fourth
combination that cannot occur.

`fit` is **required, with no default**. An event missing it is
malformed, not old: this is pre-release internal tooling with no
back-compatibility to keep.

- [ ] **Step 2: Run the type checker to see every construction site**

Run from `tools/ghmem`: `.venv/bin/mypy src tests`

Expected: FAIL, one `typeddict-item` error per site that builds a
`dispatched` event without `fit` — in `src/ghmem/triage.py` and across
`tests/test_dispatch_store.py`, `tests/test_routing_live.py`,
`tests/test_queue.py`, `tests/test_requeue.py`. **Use that list as the
worklist; do not hunt by hand.**

- [ ] **Step 3: Fill in every site**

In `triage.py`, the event built at `:116-123` gains `"fit": fit`, with
a local `fit: DispatchFit = "fit"` declared immediately above it for
now. Task 3 is what makes the value vary; this task only makes the
field exist and be honest.

Every test construction site gains `"fit": "fit"`. Those events assert
reservation, requeue and queue-exclusion behaviour that has nothing to
do with budget fit, so `"fit"` is correct in all of them.

Both fixture ledgers gain the key on each `dispatched` line, as does
the inline JSON string at `tests/test_status.py:80`. These lines are
read by `ingest.apply_routing_event`, which reads named keys and never
touches `fit`, so nothing about replay changes — but a fixture that
omits a required field is a malformed event, and the fixtures are read
as examples of the format.

- [ ] **Step 4: Run the full gate**

Run from `tools/ghmem`:
`PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH" just green`

Expected: PASS with the same test count as before this task. No
behaviour changed; only a field was added.

- [ ] **Step 5: Report — do not commit**

Write `.superpowers/sdd/b2-task-2-report.md`. Confirm explicitly that
the live-versus-rebuilt convergence tests in `tests/test_routing_live.py`
still pass — they are what would catch the new field disturbing the
reducer.

---

## Task 3: The fit path in `serve_next`

**Files:**
- Modify: `tools/ghmem/src/ghmem/triage.py:73-158`
- Create: `tools/ghmem/tests/test_triage_fit.py`
- Test: also `tools/ghmem/tests/test_triage.py` (unchanged, must stay
  green)

**Interfaces:**
- Consumes: `show.render(..., diff_max_chars=...)` and
  `tests.diff_fixtures` from Task 1; `ledger.DispatchFit` from Task 2.
- Produces: no new public names. `ServeResult` is unchanged;
  `serve_next`'s signature is unchanged.

Two packet-note strings, exact — they cross into the tests and into
what an agent reads off stdout:

```
packet-note: diff truncated to fit the batch budget
packet-note: served oversized; packet exceeds the batch budget
```

- [ ] **Step 1: Write the failing tests**

Create `tools/ghmem/tests/test_triage_fit.py`. The rest of the session
loop lives in `test_triage.py`; this module covers only what happens
when a session's first item does not fit the batch budget. Reuse
`tests.triage_helpers` (`_store`, `_ranked_numbers`, `_content_only`)
and `tests.diff_fixtures`.

Three module-private helpers:

- `_diff_session(tmp_path, diff_lines) -> tuple[Store, Ledger[RoutingEvent]]`
  — write a generated corpus, open a `Store`, rebuild from it plus an
  empty routing ledger, return both.
- `_dispatched(routing) -> list[DispatchedData]` — every dispatched
  event's data in append order. Write it as an **explicit loop with an
  annotated accumulator**, not a comprehension: the loop is what
  narrows the event union on its `type` discriminant, and a
  comprehension leaves the element type unnarrowed and `fit`
  unreachable under mypy strict.
- `_floor_chars(store, number) -> int` — the packet size of `number`
  with its diff body removed. Build it from `show.render(...,
  diff_max_chars=0)` and the `item: {number}\n` line directly, **not**
  by calling `triage`'s private renderer, so the test's idea of the
  floor is an independent measure rather than the formula under test —
  the same reasoning behind `triage_helpers._content_only`.

The five tests:

`test_packet_within_budget_records_fit_and_carries_no_note`

> **When:** the first item of a session is served under the real
> budget.
> **Then:** its dispatched event carries `fit="fit"` and the packet has
> no `packet-note:` header at all.

`test_oversized_first_item_has_its_diff_cut_to_fit`

> **When:** `BATCH_CHARS` is monkeypatched to a value between the floor
> render (thread and file list, no diff body) and the full packet, on
> a 500-line generated diff.
> **Then:** the dispatched event is tagged `diff_cut`, its
> `packet_chars` is at or under the budget — the "fits by
> construction" claim asserted, not assumed — the packet carries
> `packet-note: diff truncated to fit the batch budget`, the file list
> line for `src/big.go` survives, and the truncation marker is present.

Derive the budget as `_floor_chars(...) + LINE_WIDTH * k` so it sits
on a line boundary; that is what gives the `- 1` mutation below
something to bite on.

`test_uncuttable_first_item_is_served_oversized_and_says_so`

> **When:** `BATCH_CHARS` is monkeypatched below even the floor render.
> **Then:** the item is still served — the queue must advance —
> `result.message` is `None`, the dispatched event is tagged
> `oversized`, the packet carries `packet-note: served oversized;
> packet exceeds the batch budget`, the file list survives, and no
> generated body line appears.

`test_thread_text_is_never_cut_on_the_oversized_path`

> **When:** the first item is served oversized under a budget of 1.
> **Then:** the PR's distinctive body sentence appears in the packet in
> full — thread text is never a truncation target on any path.

`test_a_later_item_never_enters_the_fit_path`

> **When:** the budget fits the first item and nothing more, and a
> second serve is attempted under the same session token.
> **Then:** the batch ends with `packet is None` and
> `message == "batch complete: budget exhausted"` rather than serving a
> cut second packet, and every dispatched event written stays tagged
> `fit`.

- [ ] **Step 2: Run the tests to verify they fail**

Run from `tools/ghmem`: `.venv/bin/pytest tests/test_triage_fit.py -q`

Expected: FAIL on assertions, not on import or signature errors —
Task 1 already gave `render` the keyword the helpers use. The first
and last tests pass already, since Task 2 made every event `fit`. The
three fit-path tests fail on the missing `packet-note` header and on
`fit` reading `"fit"` where `"diff_cut"` or `"oversized"` is expected.

- [ ] **Step 3: Implement**

In `tools/ghmem/src/ghmem/triage.py`, importing `DispatchFit` from
`ghmem.ledger` and `DIFF_BODY_MAX_CHARS` from `ghmem.show`.

**`serve_next`'s candidate loop.** The gate at `:113-114` currently
reads `if not first_of_session and chars_used + packet_chars >
BATCH_CHARS`. Split it into two branches — the first-item exemption
and the char gate are already mutually exclusive, and the fit path
only exists in one of them. First item over `BATCH_CHARS`: re-render
through `_fit_first_item` and take its packet, chars and tag.
Otherwise, a later item over budget still returns the unchanged
`batch complete: budget exhausted` result. The `dispatched` event
gains `"fit": fit`.

Ordering: the fit path runs **before** `reserve_dispatch`, exactly
where the gate it replaces sat, so a reservation lost to a concurrent
session costs at most the extra renders and never a wasted triage.

Also update `serve_next`'s docstring paragraph on the first-item
exemption — it currently names B2 as a future hook and must now
describe what the code does.

**`_render_packet`** gains keyword-only
`diff_max_chars: int = DIFF_BODY_MAX_CHARS` and `note: str | None = None`.

- Postcondition, unchanged and load-bearing: the returned
  `packet_chars` measures the item line and the rendered thread only.
  The header block is fixed overhead the budget need not track, and
  folding it in would make the reported size depend on the session
  token's own length for no reason a caller cares about.
- `note`, when set, joins the **header** as a `packet-note: ` line
  beside `packet-chars`, so saying a packet was truncated never
  changes what the truncation was measured against.

**`_fit_first_item(store, standing, session_token, number) -> tuple[str, int, DispatchFit]`**,
defined below `_render_packet` with the two note strings beside it.

- Precondition: `number` is a session's first item and its packet at
  the default valve already exceeds `BATCH_CHARS`.
- Step one: render the floor — the same packet at `diff_max_chars=0`
  — and measure it. That is thread text plus the diffstat file list,
  neither of which may be cut, and it already includes the fence and
  the truncation marker, so everything left over is body space.
- Step two: the diff body's allowance is what the budget has left over
  that floor, **less one character** for the newline that joins the
  body to the marker below it. Without that subtraction a packet cut
  to the allowance lands one character past the budget in the boundary
  case.
- Postcondition, allowance positive: re-render at that allowance, with
  the diff-cut note. The packet is at or under `BATCH_CHARS` by
  construction, because Task 1 made the cut a real ceiling. Return
  `"diff_cut"`.
- Postcondition, allowance non-positive: the uncuttable parts alone
  exceed the budget. Serve the floor packet with the oversized note
  and return `"oversized"`. The item is served regardless — the
  first-item exemption exists so no single item can deadlock the
  queue, and the guarantee it delivers is that the queue advances, not
  that every packet fits. Both outcomes are named in the packet and
  recorded in the ledger.
- The marker is never at risk of being dropped: reaching this path at
  all means the valve-truncated body already overran the budget, so
  the allowance is always smaller than the body and the cut always
  fires.

- [ ] **Step 4: Run the tests to verify they pass**

Run from `tools/ghmem`:
`.venv/bin/pytest tests/test_triage_fit.py tests/test_triage.py -q`

Expected: PASS. `test_first_item_served_even_when_it_exceeds_the_char_cap`
in `test_triage.py` now travels the oversized path and must still pass
unchanged — it asserts only that a packet is served.

- [ ] **Step 5: Run the full gate**

Run from `tools/ghmem`:
`PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH" just green`

Expected: PASS.

- [ ] **Step 6: Report — do not commit**

Write `.superpowers/sdd/b2-task-3-report.md`. Run these three
mutations and record whether each kills a test, restoring after each:

1. Drop the `- 1` from the allowance. Expect
   `packet_chars <= budget` to fail in the `diff_cut` test at the
   boundary. This one only bites when the allowance lands exactly on a
   line boundary, so if it survives, tune `diff_lines` and the budget
   in that test until it does — an unkillable mutation here means the
   boundary is not pinned, not that it cannot be. Record what you did
   either way.
2. Weaken the non-positive-allowance branch to fire only on a strictly
   negative allowance. Expect the oversized test to fail.
3. Hardcode `"fit"` on both of `_fit_first_item`'s returns. Expect
   both the `diff_cut` and `oversized` ledger assertions to fail.

---

## Out of scope

- Calibrating `DIFF_BODY_MAX_CHARS`, `BATCH_CHARS` or `BATCH_ITEMS`
  against a live corpus — subproject 4.
- Cutting thread text, or eliding the diffstat file list.
- Any stderr signal for an oversized serve. The packet goes to stdout
  and carries the note; stderr stays reserved for terminal messages,
  which an oversized serve is not.
- A `Dispatch` column for `fit`.
- `topic` / `close` / `index` — Part C.

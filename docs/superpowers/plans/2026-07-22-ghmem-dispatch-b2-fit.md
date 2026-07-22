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
  - `tests.diff_fixtures.big_diff_text(lines: int) -> str`
  - `tests.diff_fixtures.write_diff_corpus(path: Path, *, diff_lines: int) -> None`

- [ ] **Step 1: Write the fixture generator**

Create `tools/ghmem/tests/diff_fixtures.py`:

```python
"""Generated corpus fixtures for the diff-truncation and fit tests.

Not collected by pytest — the module carries no `test_` prefix, the
same arrangement `triage_helpers` and `sync_helpers` use for the split
test modules.

Large diffs are generated here rather than versioned as fixture blobs.
A 25,000-character diff inside a JSONL line is neither readable nor
reviewable; the generator that produces it is both, and it is the
generator that gets reviewed.
"""

import json
from pathlib import Path

LINE_WIDTH = 40
"""Exact width of one generated diff line, newline included.

Fixed so a test can convert between "lines of diff" and "characters of
diff body" without re-deriving the generator's formatting.
"""

DIFF_HEADER_LINE = "diff --git a/src/big.go b/src/big.go"
"""The one header line every generated diff opens with, newline excluded."""


def diff_body_line(index: int) -> str:
    """Return the `index`-th generated diff body line, newline excluded.

    Exported so a test can assert against the exact lines it expects
    to survive a cut without restating the generator's formatting.
    """
    return f"+{index:05d}".ljust(LINE_WIDTH - 1)


def big_diff_text(lines: int) -> str:
    """Return diff text whose body is exactly `lines` added lines.

    The body is `lines * LINE_WIDTH` characters after the single header
    line, so a caller can size a diff against a character budget
    directly.
    """
    body = "".join(diff_body_line(index) + "\n" for index in range(lines))
    return DIFF_HEADER_LINE + "\n" + body


def write_diff_corpus(path: Path, *, diff_lines: int) -> None:
    """Write a fetch ledger holding one PR that carries a generated diff.

    A single item, so heat order is unambiguous and the PR is always
    the session's first served item — which is the only item the fit
    path ever touches.
    """
    events = [
        {
            "type": "item",
            "data": {
                "id": "I_1",
                "__typename": "PullRequest",
                "number": 1,
                "title": "A pull request with a large diff",
                "state": "MERGED",
                "author": {"login": "alice"},
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
                "mergedBy": {"login": "bob"},
                "mergedAt": "2026-01-02T00:00:00Z",
                "authorAssociation": "NONE",
                "body": "body of the large-diff pull request",
            },
        },
        {
            "type": "pr_diff",
            "data": {
                "prNumber": 1,
                "files": [
                    {"path": "src/big.go", "additions": diff_lines, "deletions": 0}
                ],
                "diffText": big_diff_text(diff_lines),
                "truncated": False,
            },
        },
    ]
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
```

- [ ] **Step 2: Write the failing tests**

In `tools/ghmem/tests/test_show.py`, add this fixture after the
existing `standing_table` fixture:

```python
@pytest.fixture
def diff_store(tmp_path: Path) -> Store:
    """Return a Store holding one PR whose diff is generated at runtime."""
    corpus = tmp_path / "diff_corpus.jsonl"
    write_diff_corpus(corpus, diff_lines=200)
    store = Store.open(tmp_path / "diff_cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(corpus)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "diff_routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store
```

Add the import:

```python
from tests.diff_fixtures import (
    DIFF_HEADER_LINE,
    LINE_WIDTH,
    diff_body_line,
    write_diff_corpus,
)
```

Replace `test_large_diff_body_truncated_with_marker` (currently at
`:78-93`, driven by the 645-character `show_corpus` diff that no longer
reaches the raised cap) with:

```python
def test_diff_body_cut_at_a_whole_line_within_the_cap(diff_store: Store) -> None:
    """An oversized diff body is cut to whole lines, never past the cap.

    When: a 200-line generated diff is rendered with diff_max_chars set
      to five lines' worth
    Then: the body never exceeds the cap — pinning the cut as a real
      ceiling rather than the first line boundary past it — every line
      it kept is whole and in order, and the truncation marker follows
    """
    cap = LINE_WIDTH * 5
    standing = build_standing(diff_store)

    output = render(diff_store, standing, IssueOrPRNumber(1), diff_max_chars=cap)

    body = output.split("```diff\n", 1)[1].split("\n… [diff truncated]", 1)[0]
    assert len(body) <= cap
    lines = body.splitlines()
    assert len(lines) > 1  # the header plus at least one body line survived
    assert lines[0] == DIFF_HEADER_LINE
    assert lines[1:] == [diff_body_line(index) for index in range(len(lines) - 1)]
    assert "… [diff truncated] …" in output


def test_diff_max_chars_zero_renders_the_file_list_and_no_body(
    diff_store: Store,
) -> None:
    """A cap of zero drops the whole diff body but keeps the file list.

    When: the PR is rendered with diff_max_chars=0
    Then: no diff body line survives, the marker is present, and the
      diffstat file list still renders — the floor the fit computation
      measures against
    """
    standing = build_standing(diff_store)

    output = render(diff_store, standing, IssueOrPRNumber(1), diff_max_chars=0)

    assert "+00000" not in output
    assert "… [diff truncated] …" in output
    assert "- src/big.go +200 -0" in output
```

Replace `test_diff_body_max_chars_is_modest` (currently at `:205-213`)
with:

```python
def test_diff_body_max_chars_is_a_safety_valve() -> None:
    """The provisional cap is a valve against the vendor-dump tail.

    When: DIFF_BODY_MAX_CHARS is imported
    Then: it is large enough that an ordinary PR diff renders whole —
      the median PR touches 16 lines — and small enough to stay a
      fraction of one batch rather than a ceiling on real diffs
    """
    assert 10_000 <= DIFF_BODY_MAX_CHARS <= 50_000
```

- [ ] **Step 3: Run the tests to verify they fail**

Run from `tools/ghmem`:

```
.venv/bin/pytest tests/test_show.py -q
```

Expected: `test_diff_body_cut_at_a_whole_line_within_the_cap` and
`test_diff_max_chars_zero_renders_the_file_list_and_no_body` fail with
`TypeError: render() got an unexpected keyword argument
'diff_max_chars'`; `test_diff_body_max_chars_is_a_safety_valve` fails
on `assert 10_000 <= 300`.

- [ ] **Step 4: Implement**

In `tools/ghmem/src/ghmem/show.py`, raise the constant and restate what
it is:

```python
DIFF_BODY_MAX_CHARS = 25_000  # PROVISIONAL — calibrated in subproject 4
"""Safety valve on one rendered diff body, in characters.

Not a display budget. The median PR in this corpus touches 16 lines
and only 10 of 1,299 exceed 2,000, so at this size an ordinary diff
renders whole and only the vendor-dump tail is cut — a tail whose diff
body carries no decision signal beyond its file list. Roughly 6,000
tokens, about an eighth of one triage batch.
"""
```

Give `render` the keyword and forward it:

```python
def render(
    store: Store,
    standing: StandingTable,
    number: IssueOrPRNumber,
    *,
    diff_max_chars: int = DIFF_BODY_MAX_CHARS,
) -> str:
    """Render issue/PR `number`'s full thread as deterministic markdown.

    Resolves the item via `store.get_issue_or_pr`, so an unknown
    `number` propagates `IssueOrPRNotFoundError` uncaught.

    `diff_max_chars` caps the rendered diff body. It defaults to the
    safety valve, which is what `ghmem show` uses; `triage` passes a
    budget derived from what a batch has left, which is the only
    caller that ever narrows it.
    """
    item = store.get_issue_or_pr(number)
    lines: list[str] = [*_render_header(item, standing), "", item.body, ""]
    lines.extend(_render_timeline(store, standing, item.id))
    lines.extend(_render_cross_refs(store, item.id))
    lines.extend(_render_reactions(store, item.id))
    lines.extend(_render_diff(store, number, diff_max_chars))
    return "\n".join(lines).rstrip() + "\n"
```

Replace `_render_diff` and add `_cut_diff_body` below it:

```python
def _render_diff(
    store: Store, number: IssueOrPRNumber, diff_max_chars: int
) -> list[str]:
    """Render the diffstat and (possibly truncated) diff body for a PR.

    Issues have no diff — `PRDiffNotFoundError` means the section is
    omitted entirely. The file list always renders in full, whatever
    the cap: it is the part of a diff that keeps its signal when the
    body is gone.
    """
    try:
        diff = store.pr_diff_for(number)
    except PRDiffNotFoundError:
        return []
    lines = ["## Diff", "", *[_format_file_stat(f) for f in diff.diffstat], ""]
    body, was_cut = _cut_diff_body(diff.diff_text, diff_max_chars)
    lines.append("```diff")
    if body:
        lines.append(body)
    if was_cut or diff.truncated:
        lines.append("… [diff truncated] …")
    lines.append("```")
    lines.append("")
    return lines


def _cut_diff_body(diff_text: str, diff_max_chars: int) -> tuple[str, bool]:
    """Return the body cut to whole lines within the cap, and whether it was cut.

    The cap is a real ceiling. The body keeps whole lines up to it and
    drops the line that would cross it, rather than the older rule of
    cutting at the first line boundary *past* the cap — an overshoot
    that is harmless for a display cap and wrong for a caller sizing a
    packet against a budget.

    A cap of zero, and a first line longer than the cap, both keep
    nothing. The diffstat above still renders either way.
    """
    if len(diff_text) <= diff_max_chars:
        return diff_text, False
    end = diff_text.rfind("\n", 0, diff_max_chars + 1)
    if end == -1:
        return "", True
    return diff_text[:end], True
```

- [ ] **Step 5: Run the full gate**

Run from `tools/ghmem`:

```
PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH" just green
```

Expected: PASS. `ghmem show` is unchanged for callers, so no other
test module should move.

- [ ] **Step 6: Report — do not commit**

Write the report to `.superpowers/sdd/b2-task-1-report.md`. State the
gate's pass count, and confirm by mutation that the cut rule is
pinned: change `rfind` back to `diff_text.find("\n", diff_max_chars)`
and confirm `test_diff_body_cut_at_a_whole_line_within_the_cap` dies on
the `len(body) <= cap` assertion. Restore afterwards and re-run.

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

In `tools/ghmem/src/ghmem/ledger.py`, above `DispatchedData`:

```python
type DispatchFit = Literal["fit", "diff_cut", "oversized"]
"""How a served packet sat against the batch budget.

`fit` — the packet was within budget as rendered. `diff_cut` — it was
a session's first item and its diff body was truncated to bring it
within budget. `oversized` — it was a session's first item and its
thread text and file list alone exceed the budget, so it was served
whole; neither of those may be cut.

Written for subproject 4 to calibrate `BATCH_CHARS` against: a corpus
that produces many `oversized` serves has a budget set too low. Like
`RequeueData.session`, this is a write-only signal — the reducer never
reads it back, so it gets no `Dispatch` column.

One tag rather than a pair of booleans: the three states are mutually
exclusive, and a pair would admit a fourth combination that cannot
occur.
"""


class DispatchedData(TypedDict):
    """A corpus item served to a triage session."""

    session: str
    item_id: str
    packet_chars: int
    fit: DispatchFit
```

Required, with no default. An event missing it is malformed, not old:
this is pre-release internal tooling with no back-compatibility to
keep.

- [ ] **Step 2: Run the type checker to see every construction site**

Run from `tools/ghmem`:

```
.venv/bin/mypy src tests
```

Expected: FAIL, one `typeddict-item` error per site that builds a
`dispatched` event without `fit` — in `src/ghmem/triage.py` and across
`tests/test_dispatch_store.py`, `tests/test_routing_live.py`,
`tests/test_queue.py`, `tests/test_requeue.py`. Use that list as the
worklist; do not hunt by hand.

- [ ] **Step 3: Fill in every site**

In `tools/ghmem/src/ghmem/triage.py`, the event built at `:116-123`
gains `"fit": fit` — declare `fit: DispatchFit = "fit"` immediately
above it for now. Task 3 is what makes the value vary; this task only
makes the field exist and be honest.

Every test construction site gains `"fit": "fit"`. Those events assert
reservation, requeue and queue-exclusion behaviour that has nothing to
do with budget fit, so `"fit"` is the correct value in all of them.

Both fixture ledgers gain the key on each `dispatched` line — e.g. in
`tests/fixtures/status_routing.jsonl`:

```
{"type": "dispatched", "data": {"session": "s1", "item_id": "I_1", "packet_chars": 100, "fit": "fit"}}
```

Do the same for `tests/fixtures/routing_events.jsonl` and for the
inline JSON string at `tests/test_status.py:80`. These lines are read
by `ingest.apply_routing_event`, which reads named keys and never
touches `fit`, so nothing about replay changes — but a fixture that
omits a required field is a malformed event, and the fixtures are read
as examples of the format.

- [ ] **Step 4: Run the full gate**

Run from `tools/ghmem`:

```
PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH" just green
```

Expected: PASS with the same test count as before this task. No
behaviour changed; only a field was added.

- [ ] **Step 5: Report — do not commit**

Write the report to `.superpowers/sdd/b2-task-2-report.md`. Confirm
explicitly that the live-versus-rebuilt convergence tests in
`tests/test_routing_live.py` still pass, since they are what would
catch the new field disturbing the reducer.

---

## Task 3: The fit path in `serve_next`

**Files:**
- Modify: `tools/ghmem/src/ghmem/triage.py:73-158`
- Create: `tools/ghmem/tests/test_triage_fit.py`
- Test: also `tools/ghmem/tests/test_triage.py` (unchanged, must stay
  green)

**Interfaces:**
- Consumes: `show.render(..., diff_max_chars=...)` from Task 1;
  `ledger.DispatchFit` from Task 2.
- Produces: no new public names. `ServeResult` is unchanged;
  `serve_next`'s signature is unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tools/ghmem/tests/test_triage_fit.py`:

```python
"""Test the solo-item fit path: cut the diff, or serve oversized and say so.

The rest of the session loop lives in `test_triage.py`; this module
covers only what happens when a session's first item does not fit the
batch budget.
"""

from pathlib import Path

import pytest

from ghmem import triage
from ghmem.aliases import IssueOrPRNumber
from ghmem.ledger import DispatchedData, FetchEvent, Ledger, RoutingEvent
from ghmem.show import render
from ghmem.standing import build_standing
from ghmem.store import Store
from tests.diff_fixtures import LINE_WIDTH, write_diff_corpus
from tests.triage_helpers import _content_only, _ranked_numbers, _store


def _diff_session(
    tmp_path: Path, diff_lines: int
) -> tuple[Store, Ledger[RoutingEvent]]:
    """Return a Store and routing ledger holding one PR with a generated diff."""
    corpus = tmp_path / "diff_corpus.jsonl"
    write_diff_corpus(corpus, diff_lines=diff_lines)
    store = Store.open(tmp_path / "cache.sqlite")
    fetch: Ledger[FetchEvent] = Ledger(corpus)
    routing: Ledger[RoutingEvent] = Ledger(tmp_path / "routing.jsonl")
    store.rebuild_from_ledgers(fetch, routing)
    return store, routing


def _dispatched(routing: Ledger[RoutingEvent]) -> list[DispatchedData]:
    """Return every dispatched event's data, in append order.

    The explicit loop is what narrows the event union on its `type`
    discriminant; a comprehension leaves the element type unnarrowed
    and `fit` unreachable under mypy strict.
    """
    data: list[DispatchedData] = []
    for event in routing:
        if event["type"] == "dispatched":
            data.append(event["data"])
    return data


def _floor_chars(store: Store, number: IssueOrPRNumber) -> int:
    """Return the packet size of `number` with its diff body removed.

    Derived here from `show.render` directly rather than by calling
    `triage`'s own private renderer, so the test's idea of the floor
    is an independent measure and not the formula under test — the
    same reasoning behind `triage_helpers._content_only`.
    """
    body = render(store, build_standing(store), number, diff_max_chars=0)
    return len(f"item: {number}\n{body}")


def test_packet_within_budget_records_fit_and_carries_no_note(
    tmp_path: Path,
) -> None:
    """An ordinary serve is tagged `fit` and says nothing about truncation.

    When: the first item of a session is served under the real budget
    Then: its dispatched event carries fit="fit" and the packet has no
      packet-note header
    """
    store, routing = _store(tmp_path)

    result = triage.serve_next(store, routing, triage.mint_session())

    assert result.packet is not None
    assert "packet-note:" not in result.packet
    assert _dispatched(routing)[0]["fit"] == "fit"


def test_oversized_first_item_has_its_diff_cut_to_fit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first item over budget is served with its diff cut to what fits.

    When: BATCH_CHARS is set between the floor render (thread and file
      list, no diff body) and the full packet
    Then: the packet fits the budget, keeps its file list, carries the
      truncation note, and its dispatched event is tagged diff_cut
    """
    store, routing = _diff_session(tmp_path, diff_lines=500)
    budget = _floor_chars(store, IssueOrPRNumber(1)) + LINE_WIDTH * 10
    monkeypatch.setattr(triage, "BATCH_CHARS", budget)

    result = triage.serve_next(store, routing, triage.mint_session())

    assert result.packet is not None
    event = _dispatched(routing)[0]
    assert event["fit"] == "diff_cut"
    assert event["packet_chars"] <= budget
    assert "packet-note: diff truncated to fit the batch budget" in result.packet
    assert "- src/big.go +500 -0" in result.packet
    assert "… [diff truncated] …" in result.packet


def test_uncuttable_first_item_is_served_oversized_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When thread and file list alone bust the budget, serve it anyway.

    When: BATCH_CHARS is set below even the floor render
    Then: the item is still served — the queue must advance — with an
      empty diff body, its file list intact, the oversized note, and a
      dispatched event tagged oversized
    """
    store, routing = _diff_session(tmp_path, diff_lines=500)
    monkeypatch.setattr(triage, "BATCH_CHARS", 1)

    result = triage.serve_next(store, routing, triage.mint_session())

    assert result.packet is not None
    assert result.message is None
    assert _dispatched(routing)[0]["fit"] == "oversized"
    assert (
        "packet-note: served oversized; packet exceeds the batch budget"
        in result.packet
    )
    assert "- src/big.go +500 -0" in result.packet
    assert "+00000" not in result.packet


def test_thread_text_is_never_cut_on_the_oversized_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The item's own body survives an oversized serve whole.

    When: the first item is served oversized under a budget of 1
    Then: the PR's body text appears in the packet in full — thread
      text is never a truncation target on any path
    """
    store, routing = _diff_session(tmp_path, diff_lines=500)
    monkeypatch.setattr(triage, "BATCH_CHARS", 1)

    result = triage.serve_next(store, routing, triage.mint_session())

    assert result.packet is not None
    assert "body of the large-diff pull request" in result.packet


def test_a_later_item_never_enters_the_fit_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a session's first item is ever truncated to fit.

    When: the budget fits the first item and nothing more, and a second
      serve is attempted under the same token
    Then: the batch ends rather than serving a cut second packet, and
      every dispatched event written stays tagged fit
    """
    store, routing = _store(tmp_path)
    first = _ranked_numbers(tmp_path)[0]
    token = triage.mint_session()
    monkeypatch.setattr(triage, "BATCH_CHARS", len(_content_only(store, first)) + 1)

    assert triage.serve_next(store, routing, token).packet is not None
    result = triage.serve_next(store, routing, token)

    assert result.packet is None
    assert result.message == "batch complete: budget exhausted"
    assert [event["fit"] for event in _dispatched(routing)] == ["fit"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run from `tools/ghmem`:

```
.venv/bin/pytest tests/test_triage_fit.py -q
```

Expected: FAIL on assertions, not on import or signature errors —
Task 1 already gave `render` the `diff_max_chars` keyword the helpers
use. `test_packet_within_budget_records_fit_and_carries_no_note` and
`test_a_later_item_never_enters_the_fit_path` pass already, since Task
2 made every event `fit`. The three fit-path tests fail on the missing
`packet-note` header and on `fit` still reading `"fit"` where
`"diff_cut"` or `"oversized"` is expected.

- [ ] **Step 3: Implement**

In `tools/ghmem/src/ghmem/triage.py`, import `DispatchFit` from
`ghmem.ledger` and `DIFF_BODY_MAX_CHARS` from `ghmem.show`.

Replace the candidate loop's gate in `serve_next` — the `if not
first_of_session and ...` at `:113-114` — with a branch that keeps the
two cases apart, since the first-item exemption and the char gate are
already mutually exclusive:

```python
    for candidate in candidates:
        packet, packet_chars = _render_packet(
            store, standing, session_token, candidate.number
        )
        fit: DispatchFit = "fit"
        if first_of_session:
            if packet_chars > BATCH_CHARS:
                packet, packet_chars, fit = _fit_first_item(
                    store, standing, session_token, candidate.number
                )
        elif chars_used + packet_chars > BATCH_CHARS:
            return ServeResult(packet=None, message="batch complete: budget exhausted")
        item_id = store.get_issue_or_pr(candidate.number).id
        event: DispatchedEvent = {
            "type": "dispatched",
            "data": {
                "session": session_token,
                "item_id": item_id,
                "packet_chars": packet_chars,
                "fit": fit,
            },
        }
```

Give `_render_packet` the budget and the note:

```python
def _render_packet(
    store: Store,
    standing: StandingTable,
    session_token: str,
    number: IssueOrPRNumber,
    *,
    diff_max_chars: int = DIFF_BODY_MAX_CHARS,
    note: str | None = None,
) -> tuple[str, int]:
    """Render one packet and the char count that counts against the budget.

    `packet_chars` measures the item line and the rendered thread
    only — the header block above them is fixed overhead the budget
    doesn't need to track, and folding it in would make the reported
    size depend on the session token's own length for no reason a
    caller cares about. `note`, when set, joins that header, so saying
    a packet was truncated never changes what the truncation was
    measured against.
    """
    rendered = render(store, standing, number, diff_max_chars=diff_max_chars)
    body = f"item: {number}\n{rendered}"
    packet_chars = len(body)
    header = f"session: {session_token}\npacket-chars: {packet_chars}\n"
    if note is not None:
        header += f"packet-note: {note}\n"
    return header + body, packet_chars
```

Add the fit path below `_render_packet`, with its notes beside it:

```python
_DIFF_CUT_NOTE = "diff truncated to fit the batch budget"
_OVERSIZED_NOTE = "served oversized; packet exceeds the batch budget"


def _fit_first_item(
    store: Store,
    standing: StandingTable,
    session_token: str,
    number: IssueOrPRNumber,
) -> tuple[str, int, DispatchFit]:
    """Re-render a session's oversized first item to fit, or serve it whole.

    Measures a floor render with the diff body removed: thread text and
    the diffstat file list, neither of which may be cut. Whatever the
    budget has left over is the diff body's allowance.

    The allowance loses one character to the newline that joins the
    body to the marker below it, so a packet cut to the allowance
    lands at the budget rather than one past it.

    A non-positive allowance means the uncuttable parts alone exceed
    the budget. The item is served whole regardless: the first-item
    exemption exists so no single item can deadlock the queue, and the
    guarantee it delivers is that the queue advances, not that every
    packet fits. Both outcomes are named in the packet and recorded in
    the ledger.
    """
    _floor_packet, floor_chars = _render_packet(
        store, standing, session_token, number, diff_max_chars=0
    )
    allowance = BATCH_CHARS - floor_chars - 1
    if allowance <= 0:
        packet, packet_chars = _render_packet(
            store,
            standing,
            session_token,
            number,
            diff_max_chars=0,
            note=_OVERSIZED_NOTE,
        )
        return packet, packet_chars, "oversized"
    packet, packet_chars = _render_packet(
        store,
        standing,
        session_token,
        number,
        diff_max_chars=allowance,
        note=_DIFF_CUT_NOTE,
    )
    return packet, packet_chars, "diff_cut"
```

Update `serve_next`'s docstring paragraph about the first-item
exemption — it currently names B2 as a future hook and must now
describe what the code does.

- [ ] **Step 4: Run the tests to verify they pass**

Run from `tools/ghmem`:

```
.venv/bin/pytest tests/test_triage_fit.py tests/test_triage.py -q
```

Expected: PASS. `test_first_item_served_even_when_it_exceeds_the_char_cap`
in `test_triage.py` now travels the oversized path and must still pass
unchanged — it asserts only that a packet is served.

- [ ] **Step 5: Run the full gate**

Run from `tools/ghmem`:

```
PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH" just green
```

Expected: PASS.

- [ ] **Step 6: Report — do not commit**

Write the report to `.superpowers/sdd/b2-task-3-report.md`. Run these
three mutations and record whether each kills a test, restoring after
each:

1. Drop the `- 1` from the allowance. Expect
   `event["packet_chars"] <= budget` to fail in the `diff_cut` test at
   the boundary. This one only bites when the allowance lands exactly
   on a line boundary, so if it survives, tune `diff_lines` and the
   budget in that test until it bites — an unkillable mutation here
   means the boundary is not pinned, not that it cannot be. Record
   what you did either way.
2. Replace the `allowance <= 0` branch with `allowance < 0`. Expect
   the oversized test to fail.
3. Hardcode `fit = "fit"` in `_fit_first_item`'s returns. Expect both
   the `diff_cut` and `oversized` ledger assertions to fail.

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

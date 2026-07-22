"""Generate oversized diffs for the fit tests, rather than versioning them.

A 25,000-character diff inside a JSONL fixture line would be neither
readable nor reviewable, so the large-diff cases in `test_show.py` and
`test_triage_fit.py` build one at runtime instead. It is this
generator that gets reviewed, not a blob it produces.

Not collected by pytest — the module carries no `test_` prefix, the
arrangement `triage_helpers.py` and `sync_helpers.py` already use.
"""

from pathlib import Path

from ghmem.aliases import JsonObject
from ghmem.ledger import FetchEvent, Ledger, RawNodeEvent

LINE_WIDTH = 40
"""The exact width, newline included, of one generated diff body line."""

DIFF_HEADER_LINE = "diff --git a/src/big.go b/src/big.go"
"""The single `diff --git` line every generated diff opens with."""

PR_BODY_MARKER = "distinctive sentence for the fit tests to find"
"""Substring of `_PR_BODY`, exported so a test can assert it survives."""

_PR_BODY = f"This PR carries a {PR_BODY_MARKER}."


def diff_body_line(index: int) -> str:
    """Return generated diff body line `index`, newline excluded.

    Distinct per index via an embedded, zero-padded counter, padded
    out with `x` to exactly `LINE_WIDTH - 1` characters. The counter
    digits never appear inside another line's `x` padding, so a test
    can assert a specific line's absence unambiguously.
    """
    marker = f"+line {index:06d} "
    return marker + "x" * (LINE_WIDTH - 1 - len(marker))


def big_diff_text(lines: int) -> str:
    """Return `DIFF_HEADER_LINE` followed by `lines` generated body lines.

    Invariant: the text after the header and its separating newline is
    exactly `lines * LINE_WIDTH` characters, so a caller sizes a diff
    against a character budget directly.
    """
    body = "".join(f"{diff_body_line(i)}\n" for i in range(lines))
    return f"{DIFF_HEADER_LINE}\n{body}"


def _item_event(number: int) -> RawNodeEvent:
    """Return one PullRequest `item` event for `number`."""
    item: JsonObject = {
        "id": f"I_{number}",
        "__typename": "PullRequest",
        "number": number,
        "title": "Big PR",
        "state": "OPEN",
        "author": {"login": "estab1"},
        "createdAt": "2023-01-01T12:00:00Z",
        "updatedAt": "2023-01-01T12:00:00Z",
        "mergedBy": None,
        "mergedAt": None,
        "authorAssociation": "CONTRIBUTOR",
        "body": _PR_BODY,
    }
    return {"type": "item", "data": item}


def _diff_event(number: int, diff_lines: int) -> RawNodeEvent:
    """Return one `pr_diff` event for `number`, sized by `diff_lines`."""
    diff: JsonObject = {
        "prNumber": number,
        "files": [{"path": "src/big.go", "additions": diff_lines, "deletions": 0}],
        "diffText": big_diff_text(diff_lines),
        "truncated": False,
    }
    return {"type": "pr_diff", "data": diff}


def write_diff_corpus(path: Path, *, diff_lines: int) -> None:
    """Write a fetch ledger holding one PR, sized by `diff_lines`.

    One `item` event (PullRequest #1) and one `pr_diff` event for it,
    whose diff text is `big_diff_text(diff_lines)` and whose sole
    changed file is `src/big.go` with `diff_lines` additions. One item,
    so heat order is unambiguous and PR 1 is always a session's first
    served item — the only item the fit path touches.
    """
    fetch: Ledger[FetchEvent] = Ledger(path)
    fetch.append(_item_event(1))
    fetch.append(_diff_event(1, diff_lines))


def write_two_pr_diff_corpus(
    path: Path, *, small_diff_lines: int, large_diff_lines: int
) -> None:
    """Write a fetch ledger holding two PRs, #1 small and #2 large.

    Both PRs carry identical heat inputs — no comments, no reactions,
    no participants — so heat order ties and falls back to ascending
    number (`heat_query.py`'s `order_by(desc("score"), item.number)`):
    PR #1 is always ranked, and served, first. That is what the
    later-item fit-path guard needs: a session's first served item
    stays small enough to fit on its own, while its second item, sized
    by `large_diff_lines`, is big enough that its own packet would trip
    the fit path if the guard didn't exclude non-first items from it.
    """
    fetch: Ledger[FetchEvent] = Ledger(path)
    fetch.append(_item_event(1))
    fetch.append(_diff_event(1, small_diff_lines))
    fetch.append(_item_event(2))
    fetch.append(_diff_event(2, large_diff_lines))

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

_PR_BODY = "This PR carries a distinctive sentence for the fit tests to find."


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


def write_diff_corpus(path: Path, *, diff_lines: int) -> None:
    """Write a fetch ledger holding one PR, sized by `diff_lines`.

    One `item` event (PullRequest #1) and one `pr_diff` event for it,
    whose diff text is `big_diff_text(diff_lines)` and whose sole
    changed file is `src/big.go` with `diff_lines` additions. One item,
    so heat order is unambiguous and PR 1 is always a session's first
    served item — the only item the fit path touches.
    """
    fetch: Ledger[FetchEvent] = Ledger(path)
    item: JsonObject = {
        "id": "I_1",
        "__typename": "PullRequest",
        "number": 1,
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
    item_event: RawNodeEvent = {"type": "item", "data": item}
    fetch.append(item_event)
    diff: JsonObject = {
        "prNumber": 1,
        "files": [{"path": "src/big.go", "additions": diff_lines, "deletions": 0}],
        "diffText": big_diff_text(diff_lines),
        "truncated": False,
    }
    diff_event: RawNodeEvent = {"type": "pr_diff", "data": diff}
    fetch.append(diff_event)

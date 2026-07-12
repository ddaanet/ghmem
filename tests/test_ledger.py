"""Test the append-only JSONL ledger primitive."""

from pathlib import Path

from ghmem.ledger import Ledger


def test_append_iter_roundtrip(tmp_path: Path) -> None:
    """Test that appended events are yielded back in append order.

    When: three events are appended to a fresh ledger
    Then: iterating the ledger yields exactly those events, in order
    """
    ledger: Ledger[dict[str, int]] = Ledger(tmp_path / "events.jsonl")
    events = [{"n": 1}, {"n": 2}, {"n": 3}]

    for event in events:
        ledger.append(event)

    assert list(ledger) == events


def test_reopen_appends(tmp_path: Path) -> None:
    """Test that a new Ledger instance on the same path appends after existing.

    When: a ledger is written to, then a second Ledger instance is opened
      on the same path and appends more events
    Then: iterating the second instance yields all events, in append order
    """
    path = tmp_path / "events.jsonl"
    first: Ledger[dict[str, int]] = Ledger(path)
    first.append({"n": 1})

    second: Ledger[dict[str, int]] = Ledger(path)
    second.append({"n": 2})

    assert list(second) == [{"n": 1}, {"n": 2}]


def test_trailing_partial_line_ignored(tmp_path: Path) -> None:
    """Test that a truncated trailing line from a crash is skipped.

    When: the ledger file on disk ends with a whole record followed by a
      truncated (non-JSON, no trailing newline) partial line
    Then: iterating the ledger yields only the whole record
    """
    path = tmp_path / "events.jsonl"
    path.write_text('{"n": 1}\n{"n": 2, "trunc')

    ledger: Ledger[dict[str, int]] = Ledger(path)

    assert list(ledger) == [{"n": 1}]

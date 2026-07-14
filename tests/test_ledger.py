"""Test the append-only JSONL ledger primitive."""

from pathlib import Path

from ghmem.ledger import Ledger


def test_append_iter_roundtrip(tmp_path: Path) -> None:
    """Appended events are yielded back in append order.

    When: three events are appended to a fresh ledger
    Then: iterating the ledger yields exactly those events, in order
    """
    ledger: Ledger[dict[str, int]] = Ledger(tmp_path / "events.jsonl")
    events = [{"n": 1}, {"n": 2}, {"n": 3}]

    for event in events:
        ledger.append(event)

    assert list(ledger) == events


def test_reopen_appends(tmp_path: Path) -> None:
    """A new Ledger on the same path appends after existing events.

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


def test_sync_missing_file_is_noop(tmp_path: Path) -> None:
    """Syncing a never-appended ledger neither raises nor creates a file.

    When: sync() is called on a ledger whose file was never written
    Then: it returns quietly and leaves the path non-existent
    """
    path = tmp_path / "events.jsonl"
    ledger: Ledger[dict[str, int]] = Ledger(path)

    ledger.sync()

    assert not path.exists()


def test_sync_written_ledger_does_not_crash(tmp_path: Path) -> None:
    """Syncing a written ledger completes without error.

    fsync's guarantee is on the physical disk and cannot be observed
    from a test, so this only pins that sync() runs cleanly on a real
    file.

    When: an event is appended and then sync() is called
    Then: sync() returns without raising
    """
    ledger: Ledger[dict[str, int]] = Ledger(tmp_path / "events.jsonl")
    ledger.append({"n": 1})

    ledger.sync()


def test_exists_tracks_first_append(tmp_path: Path) -> None:
    """exists() is false until the first append creates the file.

    When: a ledger is bound to a path, then an event is appended
    Then: exists() is false before the append and true after
    """
    ledger: Ledger[dict[str, int]] = Ledger(tmp_path / "events.jsonl")

    assert not ledger.exists()
    ledger.append({"n": 1})
    assert ledger.exists()


def test_trailing_partial_line_ignored(tmp_path: Path) -> None:
    """A truncated trailing line from a crash is skipped.

    When: the ledger file on disk ends with a whole record followed by a
      truncated (non-JSON, no trailing newline) partial line
    Then: iterating the ledger yields only the whole record
    """
    path = tmp_path / "events.jsonl"
    path.write_text('{"n": 1}\n{"n": 2, "trunc')

    ledger: Ledger[dict[str, int]] = Ledger(path)

    assert list(ledger) == [{"n": 1}]

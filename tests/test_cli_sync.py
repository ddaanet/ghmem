"""Test the `ghmem sync` CLI command."""

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from pytest_mock import MockerFixture

from ghmem.cli import main
from ghmem.exceptions import RateLimitError
from ghmem.ledger import Ledger
from ghmem.sync import SyncResult


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the corpus dir and a fake token at a scratch directory."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    monkeypatch.setenv("GITHUB_TOKEN", "tok")


def test_sync_command_reports_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mocker: MockerFixture
) -> None:
    """`ghmem sync` runs the driver and prints its counts.

    When: the driver succeeds
    Then: the command exits 0 and echoes the returned counts
    """
    _env(monkeypatch, tmp_path)
    mocker.patch(
        "ghmem.cli.run_sync",
        return_value=SyncResult(Counter(item=1), {}),
    )

    result = CliRunner().invoke(main, ["sync"])

    assert result.exit_code == 0, result.output
    assert "'item': 1" in result.output


def test_sync_command_rate_limit_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mocker: MockerFixture
) -> None:
    """A `RateLimitError` mid-sync prints a continue hint and exits 0.

    When: the driver raises `RateLimitError`
    Then: the command exits 0 (progress is already durably persisted in
      the ledger) and points at a plain re-run to continue
    """
    _env(monkeypatch, tmp_path)
    mocker.patch(
        "ghmem.cli.run_sync",
        side_effect=RateLimitError(datetime(2026, 7, 16, 20, 0, tzinfo=UTC)),
    )

    result = CliRunner().invoke(main, ["sync"])

    assert result.exit_code == 0, result.output
    assert "ghmem sync" in result.stderr
    assert "rate limit exhausted" in result.stderr


def test_sync_command_takes_no_resume_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mocker: MockerFixture
) -> None:
    """Resuming is unconditional, so no flag selects it.

    Resuming from the last persisted cursor is what a bare `ghmem sync`
    already does, so `--resume` would name the default rather than an
    alternative to it.

    When: `ghmem sync --resume` is invoked
    Then: click rejects the unknown option, and a bare `ghmem sync`
      reaches the driver with just a `Ledger`
    """
    _env(monkeypatch, tmp_path)
    run_sync = mocker.patch(
        "ghmem.cli.run_sync", return_value=SyncResult(Counter(), {})
    )

    rejected = CliRunner().invoke(main, ["sync", "--resume"])
    assert rejected.exit_code != 0

    result = CliRunner().invoke(main, ["sync"])

    assert result.exit_code == 0, result.output
    assert run_sync.call_args.kwargs == {}
    assert isinstance(run_sync.call_args.args[1], Ledger)

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
    """A `RateLimitError` mid-sync prints a resume hint and exits 0.

    When: the driver raises `RateLimitError`
    Then: the command exits 0 (progress is already durably persisted in
      the ledger) and prints a `--resume` hint to stderr
    """
    _env(monkeypatch, tmp_path)
    mocker.patch(
        "ghmem.cli.run_sync",
        side_effect=RateLimitError(datetime(2026, 7, 16, 20, 0, tzinfo=UTC)),
    )

    result = CliRunner().invoke(main, ["sync"])

    assert result.exit_code == 0, result.output
    assert "--resume" in result.stderr


def test_sync_command_passes_resume_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mocker: MockerFixture
) -> None:
    """`--resume` reaches the driver as `resume=True`.

    When: `ghmem sync --resume` is invoked
    Then: `run_sync` is called with a `Ledger` and `resume=True`
    """
    _env(monkeypatch, tmp_path)
    run_sync = mocker.patch(
        "ghmem.cli.run_sync", return_value=SyncResult(Counter(), {})
    )

    result = CliRunner().invoke(main, ["sync", "--resume"])

    assert result.exit_code == 0, result.output
    assert run_sync.call_args.kwargs["resume"] is True
    assert isinstance(run_sync.call_args.args[1], Ledger)

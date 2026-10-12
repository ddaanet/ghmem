"""Test the per-corpus target config, `target.toml` beside the ledgers."""

from pathlib import Path
from typing import cast
from unittest.mock import Mock

import click
import pytest
from click.testing import CliRunner
from pytest_mock import MockerFixture

from ghmem.aliases import JsonObject
from ghmem.cli import main
from ghmem.github import GitHubClient
from ghmem.ledger import FetchEvent, Ledger
from ghmem.sync import sync
from tests.sync_helpers import _ALL_PAGES, _client, _stub_graphql

FIXTURE = Path(__file__).parent / "fixtures" / "show_corpus.jsonl"

_KEYS = {"owner": "acme", "repo": "widget", "owner_login": "newdev"}


def _corpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *keys: str) -> Path:
    """Point $GHMEM_CORPUS_DIR at a rebuilt corpus whose target has `keys`.

    No `keys` at all means no `target.toml` is written. The rebuild
    runs either way, which is itself part of what is under test: it
    must not need the target.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "fetch.jsonl").write_text(FIXTURE.read_text())
    if keys:
        lines = [f'{key} = "{_KEYS[key]}"\n' for key in keys]
        (corpus / "target.toml").write_text("".join(lines))
    monkeypatch.setenv("GHMEM_CORPUS_DIR", str(corpus))
    assert CliRunner().invoke(main, ["rebuild"]).exit_code == 0
    return corpus


def test_missing_target_file_is_an_explained_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A command that needs the target names the file it could not read.

    When: `ghmem show` renders standing over a corpus with no
      `target.toml`
    Then: it exits non-zero with the file's path in its output, and no
      traceback
    """
    corpus = _corpus(monkeypatch, tmp_path)

    result = CliRunner().invoke(main, ["show", "1"])

    assert result.exit_code != 0
    assert str(corpus / "target.toml") in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("missing", sorted(_KEYS))
def test_missing_target_key_names_the_path_and_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str
) -> None:
    """A `target.toml` short one key fails naming that key, never a default.

    All three keys are required whichever command asks: `show` only
    uses `owner_login`, and still refuses a file missing `repo`.

    When: `target.toml` carries every key but `missing`
    Then: `ghmem show` exits non-zero, naming the file and the key
    """
    present = [key for key in _KEYS if key != missing]
    corpus = _corpus(monkeypatch, tmp_path, *present)

    result = CliRunner().invoke(main, ["show", "1"])

    assert result.exit_code != 0
    assert str(corpus / "target.toml") in result.output
    assert f"`{missing}`" in result.output


def test_owner_login_is_read_from_the_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The OWNER standing goes to whoever `target.toml` names.

    When: `owner_login` names `newdev`, who has no merges at all in the
      fixture
    Then: `ghmem show` annotates newdev as the owner
    """
    _corpus(monkeypatch, tmp_path, *_KEYS)

    result = CliRunner().invoke(main, ["show", "1"])

    assert result.exit_code == 0, result.output
    assert "newdev (owner)" in result.output


def test_sync_without_a_target_fetches_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mocker: MockerFixture
) -> None:
    """`sync` refuses before its first request when the target is missing.

    When: `sync` runs over a corpus with no `target.toml`
    Then: it raises the explained failure naming the file, having made
      no GraphQL call
    """
    corpus = _corpus(monkeypatch, tmp_path)
    client = _client(mocker, _ALL_PAGES)

    with pytest.raises(click.ClickException) as exc_info:
        sync(client, Ledger(tmp_path / "fetch.jsonl"))

    assert str(corpus / "target.toml") in exc_info.value.message
    cast("Mock", client.graphql).assert_not_called()


def test_sync_queries_the_configured_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mocker: MockerFixture
) -> None:
    """Every repository-rooted request names the target's owner and repo.

    When: a full sync runs with a target of `acme/widget`
    Then: the four connection queries each select
      `repository(owner: "acme", name: "widget")`, and the REST diff
      fetch is asked for that same repository
    """
    _corpus(monkeypatch, tmp_path, *_KEYS)
    client = GitHubClient("tok")
    queries: list[str] = []
    answer = _stub_graphql(_ALL_PAGES)

    def record(query: str, variables: JsonObject, response: type) -> object:
        queries.append(query)
        return answer(query, variables, response)

    mocker.patch.object(client, "graphql", side_effect=record)
    pr_diff_text = mocker.patch.object(
        client, "pr_diff_text", return_value=("diff --git a/x b/x\n", False)
    )
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")

    sync(client, fetch)

    rooted = {query for query in queries if "repository(" in query}
    assert len(rooted) == 4
    for query in rooted:
        assert 'repository(owner: "acme", name: "widget")' in query
    assert pr_diff_text.call_args.args[:2] == ("acme", "widget")


def test_a_command_off_the_target_path_needs_no_target_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Commands that never touch the target run without `target.toml`.

    The read is lazy, so only sync and standing pay for it. `rebuild`
    is covered by `_corpus` itself, which asserts it exits 0.

    When: `ghmem status` runs over a corpus with no `target.toml`
    Then: it exits 0 and reports the corpus
    """
    _corpus(monkeypatch, tmp_path)

    result = CliRunner().invoke(main, ["status"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("total=13 ")

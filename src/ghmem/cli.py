"""Command-line interface for ghmem."""

import click

from ghmem.aliases import DiscussionNumber, IssueOrPRNumber
from ghmem.exceptions import (
    DiscussionNotFoundError,
    IssueOrPRNotFoundError,
    RateLimitError,
)
from ghmem.github import GitHubClient
from ghmem.ledger import FetchEvent, Ledger
from ghmem.paths import fetch_ledger_path, routing_ledger_path, sqlite_path
from ghmem.show import render, render_discussion
from ghmem.standing import build_standing
from ghmem.store import RoutingEvent, Store
from ghmem.sync import sync as run_sync


@click.group()
@click.version_option(
    package_name="ghmem-cli",
    message="%(package)s %(version)s",
)
def main() -> None:
    """ghmem: institutional-memory pipeline for micro."""


@main.command()
def rebuild() -> None:
    """Rebuild the SQLite mirror from the fetch and routing ledgers."""
    path = sqlite_path()
    store = Store.open(path)
    fetch: Ledger[FetchEvent] = Ledger(fetch_ledger_path())
    routing: Ledger[RoutingEvent] = Ledger(routing_ledger_path())
    store.rebuild_from_ledgers(fetch, routing)
    click.echo(f"Rebuilt {path}")


@main.command()
def sync() -> None:
    """Fetch issues, PRs, discussions, and commits into the fetch ledger.

    Always continues from where the last run left off, so re-running after an
    interruption picks up rather than starting over.
    """
    client = GitHubClient.from_env()
    fetch: Ledger[FetchEvent] = Ledger(fetch_ledger_path())
    try:
        result = run_sync(client, fetch)
    except RateLimitError as exc:
        click.echo(
            f"ghmem sync: rate limit exhausted, resets at {exc.reset_at}. "
            "Re-run `ghmem sync` to continue from here.",
            err=True,
        )
        raise SystemExit(0) from exc
    click.echo(f"Synced: {dict(result.counts)}")


@main.command()
@click.argument("number", type=int)
def show(number: int) -> None:
    """Render one issue or pull request's whole thread as markdown."""
    store = Store.open(sqlite_path())
    table = build_standing(store)
    try:
        output = render(store, table, IssueOrPRNumber(number))
    except IssueOrPRNotFoundError as exc:
        click.echo(f"ghmem show: no issue or pull request numbered #{number}", err=True)
        raise SystemExit(1) from exc
    click.echo(output, nl=False)


@main.command()
@click.argument("number", type=int)
def discussion(number: int) -> None:
    """Render one discussion's whole thread as markdown."""
    store = Store.open(sqlite_path())
    table = build_standing(store)
    try:
        output = render_discussion(store, table, DiscussionNumber(number))
    except DiscussionNotFoundError as exc:
        click.echo(f"ghmem discussion: no discussion numbered #{number}", err=True)
        raise SystemExit(1) from exc
    click.echo(output, nl=False)

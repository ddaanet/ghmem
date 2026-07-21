"""Command-line interface for ghmem."""

import csv
from dataclasses import astuple, fields

import click

from ghmem.aliases import DiscussionNumber, IssueOrPRNumber
from ghmem.exceptions import (
    DiscussionNotFoundError,
    IssueOrPRNotFoundError,
    RateLimitError,
    TriageSessionError,
)
from ghmem.github import GitHubClient
from ghmem.heat import rank
from ghmem.ledger import FetchEvent, Ledger, RoutingEvent
from ghmem.models import HeatScore
from ghmem.paths import fetch_ledger_path, routing_ledger_path, sqlite_path
from ghmem.show import render, render_discussion
from ghmem.standing import build_standing
from ghmem.status import build_status, render_status
from ghmem.store import Store
from ghmem.sync import sync as run_sync
from ghmem.triage import ServeResult, mint_session, record_verdict, serve_next


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
def heat() -> None:
    """Rank the corpus by heat as CSV, hottest item first.

    CSV because this command's only consumer is analysis — weight
    calibration reads the whole distribution, per-metric columns and all
    — and titles carrying commas need real quoting. (The ranking itself
    has a second, in-process consumer: `triage` takes the hottest
    untriaged item.) Ranking before the header keeps a failed run from
    leaving a header-only file that parses as a valid empty corpus.
    """
    store = Store.open(sqlite_path())
    scores = rank(store)
    out = csv.writer(click.get_text_stream("stdout"), lineterminator="\n")
    out.writerow([field.name for field in fields(HeatScore)])
    out.writerows(astuple(score) for score in scores)


@main.command()
def status() -> None:
    """Report corpus coverage: counts, topics, dirty set, sync cursor."""
    store = Store.open(sqlite_path())
    fetch: Ledger[FetchEvent] = Ledger(fetch_ledger_path())
    click.echo(render_status(build_status(store, fetch)))


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


@main.command()
def triage() -> None:
    """Mint a session token and serve its first packet.

    Prints the packet to stdout on success. An empty or fully-routed
    queue prints its terminal message to stderr instead, and still
    exits 0 — this command has no verdict to reject, so it never
    raises `TriageSessionError`.
    """
    store = Store.open(sqlite_path())
    routing: Ledger[RoutingEvent] = Ledger(routing_ledger_path())
    token = mint_session()
    _emit_served(serve_next(store, routing, token))


@main.command()
@click.argument("token")
@click.argument("number", type=int)
@click.argument("verdict")
@click.argument("note", required=False, default="")
def route(token: str, number: int, verdict: str, note: str) -> None:
    """Record a verdict for NUMBER under TOKEN, then serve the next packet.

    One command per triaged item, so an agent spends no step sitting
    on a purely deterministic result. A rejected verdict — a bad note
    pairing, or NUMBER not currently dispatched to TOKEN — is an
    actual error: printed to stderr, exit 1, the ledger untouched.
    Otherwise this behaves exactly like `triage`: a packet or a
    terminal message, exit 0 either way.
    """
    store = Store.open(sqlite_path())
    routing: Ledger[RoutingEvent] = Ledger(routing_ledger_path())
    try:
        record_verdict(store, routing, token, IssueOrPRNumber(number), verdict, note)
    except TriageSessionError as exc:
        click.echo(f"ghmem route: {exc}", err=True)
        raise SystemExit(1) from exc
    _emit_served(serve_next(store, routing, token))


def _emit_served(result: ServeResult) -> None:
    """Print a ServeResult: a packet to stdout, or a message to stderr.

    Both exit 0 (click's default for a command that returns normally).
    A terminal message is an expected outcome — "budget exhausted",
    "0 pending", or any string `serve_next` adds later — never a
    failure an orchestration script should read as one.
    """
    if result.packet is not None:
        click.echo(result.packet, nl=False)
    else:
        click.echo(result.message, err=True)

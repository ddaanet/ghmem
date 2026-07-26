"""Command-line interface for ghmem."""

import csv
from dataclasses import astuple, fields

import click

from ghmem.aliases import DiscussionNumber, IssueOrPRNumber
from ghmem.exceptions import (
    DiscussionNotFoundError,
    IssueOrPRNotFoundError,
    RateLimitError,
    TopicNotRoutedError,
    TriageSessionError,
    UnknownSnapshotError,
)
from ghmem.github import GitHubClient
from ghmem.heat import rank
from ghmem.index_cmd import build_index, render_index
from ghmem.ledger import FetchEvent, Ledger, RoutingEvent
from ghmem.models import HeatScore
from ghmem.paths import (
    design_dir,
    fetch_ledger_path,
    routing_ledger_path,
    sqlite_path,
)
from ghmem.show import render, render_discussion
from ghmem.standing import build_standing
from ghmem.status import build_status, render_status
from ghmem.store import Store
from ghmem.sync import sync as run_sync
from ghmem.topic import assemble
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
def requeue() -> None:
    """Free every item a crashed session dispatched but never routed.

    Meant to run before an orchestration run starts any triage session,
    not mid-session: serving a stranded item here would hand out a
    fresh packet with no session to route it, recreating the exact
    stranded state this command exists to clear. It requeues, prints
    the count, and serves nothing.
    """
    store = Store.open(sqlite_path())
    routing: Ledger[RoutingEvent] = Ledger(routing_ledger_path())
    stranded = store.dispatched_unrouted()
    for dispatch in stranded:
        event: RoutingEvent = {
            "type": "requeue",
            "data": {"session": dispatch.session, "item_id": dispatch.item_id},
        }
        store.record_routing_event(routing, event)
    click.echo(f"Requeued {len(stranded)} item(s)")


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
def index() -> None:
    """Regenerate docs/design/INDEX.md from the topic docs and routing mirror.

    Reads every topic doc beside INDEX.md and the routing mirror's topics, then
    rewrites the one INDEX.md. Creates docs/design/ if it does not exist — an
    empty index is a valid state, not an error — and signs off with what it
    wrote.
    """
    store = Store.open(sqlite_path())
    directory = design_dir()
    summary = build_index(store, directory)
    (directory / "INDEX.md").write_text(render_index(summary), encoding="utf-8")
    click.echo(
        f"INDEX.md: {summary.topic_count} topics "
        f"({summary.synthesized_count} synthesized, "
        f"{summary.pending_count} pending)"
    )


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
@click.option("--note", "note_from_stdin", is_flag=True, help="Read NOTE from stdin.")
def route(
    token: str, number: int, verdict: str, note: str, *, note_from_stdin: bool
) -> None:
    """Record a verdict for NUMBER under TOKEN, then serve the next packet.

    One command per triaged item, so an agent spends no step sitting
    on a purely deterministic result. A rejected verdict — a bad note
    pairing, or NUMBER not currently dispatched to TOKEN — is an
    actual error: printed to stderr, exit 1, the ledger untouched.
    Otherwise this behaves exactly like `triage`: a packet or a
    terminal message, exit 0 either way.

    `--note` takes the note from stdin instead of the argument list.
    A note is prose, and prose carries the characters a shell reads as
    instructions; passing it as an argument means quoting it, and a
    quoting mistake corrupts the note silently. Fed by a quoted
    heredoc, stdin expands nothing at all.
    """
    store = Store.open(sqlite_path())
    routing: Ledger[RoutingEvent] = Ledger(routing_ledger_path())
    try:
        note = _resolve_note(note, from_stdin=note_from_stdin)
        record_verdict(store, routing, token, IssueOrPRNumber(number), verdict, note)
    except TriageSessionError as exc:
        click.echo(f"ghmem route: {exc}", err=True)
        raise SystemExit(1) from exc
    _emit_served(serve_next(store, routing, token))


def _resolve_note(note: str, *, from_stdin: bool) -> str:
    """Return the note to record, reading stdin when `--note` was given.

    Rejects the ways the pair can be malformed the same way
    `record_verdict` rejects a bad note pairing, so `route` maps one
    exception class for every reason it can turn a request down. An
    empty stdin is among them: `--note` with nothing behind it is a
    malformed command, never a request to record no note.
    """
    if not from_stdin:
        return note
    if note:
        raise TriageSessionError.two_notes()
    piped = click.get_text_stream("stdin").read().strip()
    if not piped:
        raise TriageSessionError.empty_note()
    if "\n" in piped:
        raise TriageSessionError.multiline_note()
    return piped


@main.command()
@click.argument("name")
def topic(name: str) -> None:
    """Assemble NAME's synthesis context behind a freshly minted snapshot.

    Prints the assembled context to stdout on success. NAME carrying
    no routed items is an actual error: printed to stderr, exit 1, the
    ledger untouched.
    """
    store = Store.open(sqlite_path())
    routing: Ledger[RoutingEvent] = Ledger(routing_ledger_path())
    try:
        output = assemble(store, routing, name)
    except TopicNotRoutedError as exc:
        click.echo(f"ghmem topic: {exc}", err=True)
        raise SystemExit(1) from exc
    click.echo(output, nl=False)


@main.command()
@click.argument("token")
def close(token: str) -> None:
    """Record TOKEN's synthesis as complete, regenerating the topic index.

    A compare-and-set against the snapshot TOKEN names: if the topic
    changed since that snapshot was taken, nothing is written and the
    stale outcome is reported on stderr, exit 0 — re-run `ghmem topic`
    to pick up the change. TOKEN naming no snapshot at all is an
    actual error: printed to stderr, exit 1, the ledger untouched.
    """
    store = Store.open(sqlite_path())
    routing: Ledger[RoutingEvent] = Ledger(routing_ledger_path())
    try:
        outcome = store.close_topic(routing, token)
    except UnknownSnapshotError as exc:
        click.echo(f"ghmem close: {exc}", err=True)
        raise SystemExit(1) from exc
    if outcome.cleared:
        directory = design_dir()
        summary = build_index(store, directory)
        (directory / "INDEX.md").write_text(render_index(summary), encoding="utf-8")
        click.echo(f"closed {outcome.topic}")
    else:
        click.echo(
            f"stale: {outcome.topic} changed since its snapshot; re-run ghmem topic",
            err=True,
        )


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

"""Command-line interface for ghmem."""

import click

from ghmem.aliases import Number
from ghmem.exceptions import IssueOrPRNotFoundError
from ghmem.ledger import FetchEvent, Ledger
from ghmem.paths import fetch_ledger_path, routing_ledger_path, sqlite_path
from ghmem.show import render
from ghmem.standing import build_standing
from ghmem.store import RoutingEvent, Store


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
@click.argument("number", type=int)
def show(number: int) -> None:
    """Render one issue or pull request's whole thread as markdown."""
    store = Store.open(sqlite_path())
    table = build_standing(store)
    try:
        output = render(store, table, Number(number))
    except IssueOrPRNotFoundError:
        click.echo(f"ghmem show: no issue or pull request numbered #{number}", err=True)
        raise SystemExit(1) from None
    click.echo(output, nl=False)

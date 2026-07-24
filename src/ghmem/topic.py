"""Topic synthesis-context assembly: `ghmem topic`'s library half.

`assemble` gathers everything a synthesis pass over one routed topic
needs — the routed items in heat order, each item's rendered thread,
and a content digest snapshotting what was routed — into one markdown
document behind a freshly minted token. `cli.topic` is the few lines of
plumbing around it, matching the seam `show.py`/`cli.show` already
set: the module holds the logic and its tests, the command wires it up.

The digest itself is `dispatch_store._topic_digest`'s job, not this
module's: `assemble` only asks `Store.topic_digest` for it and records
whatever comes back, so there is exactly one place in the codebase
that knows the digest's shape.
"""

import secrets
import string

from ghmem.aliases import IssueOrPRNumber
from ghmem.exceptions import PRDiffNotFoundError, TopicNotRoutedError
from ghmem.heat import HEAT_WEIGHTS
from ghmem.ledger import Ledger, RoutingEvent, SnapshotEvent
from ghmem.models import HeatScore, IssueOrPR, Route
from ghmem.show import render
from ghmem.standing import StandingTable, build_standing
from ghmem.store import Store

__all__ = ["assemble"]

_TOKEN_ALPHABET = string.digits + string.ascii_letters
_TOKEN_LENGTH = 8


def assemble(store: Store, routing: Ledger[RoutingEvent], name: str) -> str:
    """Assemble topic `name`'s synthesis context behind a minted snapshot token.

    Raises `TopicNotRoutedError` if no item is currently routed to
    `name` — nothing to synthesize, so no token is minted and no event
    is recorded.

    Mints a fresh token and computes the topic's content digest before
    rendering anything, then records both as a `SnapshotEvent` — so a
    snapshot recorded in the ledger always corresponds to output that
    was actually produced, never the other way round.

    Renders a `snapshot: <token>` header, a `## Item index` section
    (one line per routed item, hottest first), and a `## Threads`
    section carrying each routed item's full `show.render` at the
    default diff valve.
    """
    routed = store.routed_items(name)
    if not routed:
        raise TopicNotRoutedError(name)
    by_number: dict[IssueOrPRNumber, tuple[IssueOrPR, Route]] = {
        item.number: (item, route) for item, route in routed
    }
    scores = store.heat_scores_for(HEAT_WEIGHTS, list(by_number))

    digest = store.topic_digest(name)
    token = _mint_token()
    store.record_routing_event(
        routing,
        SnapshotEvent(
            type="snapshot", data={"token": token, "topic": name, "digest": digest}
        ),
    )

    standing = build_standing(store)
    lines = [f"snapshot: {token}", "", "## Item index", ""]
    for score in scores:
        item, route = by_number[score.number]
        lines.append(_render_item_line(store, standing, item, score, route))
    lines.extend(["", "## Threads", ""])
    lines.extend(render(store, standing, score.number) for score in scores)
    return "\n".join(lines).rstrip() + "\n"


def _mint_token() -> str:
    """Mint a fresh, unguessable 8-character base62 snapshot token.

    Its own constants, kept out of `triage`: a triage session token and
    a synthesis snapshot token are unrelated identifiers that merely
    share the shape, drawn the same way `triage.mint_session` draws its
    own — `secrets.choice` over `[0-9A-Za-z]` — for the same reason,
    not by importing that module's constants.
    """
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LENGTH))


def _render_item_line(
    store: Store,
    standing: StandingTable,
    item: IssueOrPR,
    score: HeatScore,
    route: Route,
) -> str:
    """Render one `## Item index` line: the settled field set and order.

    `title` and the four counts come from `score` (`HeatScore`); dates,
    state, and author come from `item` (`IssueOrPR`); `note` comes from
    `route`. `note` is always present here: `routed_items` excludes
    `NO_SIGNAL` routes, so every row it returns carries a real-topic
    verdict, and `record_verdict` requires a note for those.
    """
    who = standing.annotation(item.author, item.created_at.date())
    line = (
        f"- #{score.number} — {score.title} — {item.state.lower()}, "
        f"opened {item.created_at.date().isoformat()}, "
        f"last active {item.updated_at.date().isoformat()}, by {who} "
        f"\N{MIDDLE DOT} {score.comments} comments "
        f"\N{MIDDLE DOT} {score.reactions} reactions "
        f"\N{MIDDLE DOT} {score.participants} participants "
        f"\N{MIDDLE DOT} {score.related_items} related"
    )
    diffstat = _diffstat_segment(store, score.number)
    if diffstat is not None:
        line += f" \N{MIDDLE DOT} {diffstat}"
    return line + f" \N{MIDDLE DOT} note: {route.note}"


def _diffstat_segment(store: Store, number: IssueOrPRNumber) -> str | None:
    """Render "N files +A/-D" for a PR with a fetched diff, else None.

    An issue, or a PR whose diff was never fetched, simply carries no
    segment — `PRDiffNotFoundError` is the expected outcome for both,
    not a condition worth telling apart here.
    """
    try:
        diff = store.pr_diff_for(number)
    except PRDiffNotFoundError:
        return None
    adds = sum(f.additions for f in diff.diffstat)
    dels = sum(f.deletions for f in diff.diffstat)
    return f"{len(diff.diffstat)} files +{adds}/-{dels}"

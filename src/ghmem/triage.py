"""The triage session loop: mint a token, render, gate, reserve, emit.

`mint_session` opens a session; `serve_next` serves its next packet,
or a terminal message when there's nothing left to give it;
`record_verdict` writes one triage verdict — `route`'s library half.
Task 4 wraps these three in the `triage` and `route` commands: this
module owns the whole session's logic, matching the seam
`show.py`/`cli.show` already sets, where the module holds the logic
and its tests and the command is a few lines of plumbing.

The budget that ends a batch — `BATCH_ITEMS`, `BATCH_CHARS` — lives in
`budget.py`, mirroring how `heat.py` owns `HEAT_WEIGHTS` for the same
heat order this module walks. `Store.reserve_dispatch`
(`dispatch_store.py`) is what actually makes concurrent sessions safe;
this module's only job on that front is to retry the next candidate
when a reservation is lost, never to re-derive the race-freedom itself.
"""

import secrets
import string
from dataclasses import dataclass

from ghmem.aliases import IssueOrPRNumber
from ghmem.budget import BATCH_CHARS, BATCH_ITEMS
from ghmem.exceptions import IssueOrPRNotFoundError, TriageSessionError
from ghmem.heat import HEAT_WEIGHTS
from ghmem.ledger import (
    DispatchedEvent,
    DispatchFit,
    Ledger,
    RouteEvent,
    RoutingEvent,
)
from ghmem.show import render
from ghmem.show_diff import DIFF_BODY_MAX_CHARS, diff_body_dropped
from ghmem.standing import StandingTable, build_standing
from ghmem.store import Store

__all__ = ["ServeResult", "mint_session", "record_verdict", "serve_next"]


@dataclass(frozen=True, slots=True)
class ServeResult:
    """One `serve_next` outcome: a packet to dispatch, or a terminal message.

    Exactly one field is set. `packet` is a full triage packet, ready
    for an agent; `message` is one of `serve_next`'s three terminal
    strings, verbatim, for a caller to print and exit cleanly rather
    than treat as a failure.
    """

    packet: str | None
    message: str | None


_TOKEN_ALPHABET = string.digits + string.ascii_letters
_TOKEN_LENGTH = 8


def mint_session() -> str:
    """Mint a fresh, unguessable 8-character session token.

    Drawn by `secrets.choice` from `[0-9A-Za-z]`: 62**8 ~= 2.2e14
    against a lifetime of maybe 10**4 sessions. Not a UUID — a UUID's
    version and variant bits encode structure nothing here reads, and
    truncating one would throw away the only property that justified
    the format. `secrets` over `random` sidesteps any question about
    seeding across the processes an orchestrator spawns together.

    A token minted here but handed an empty queue serves nothing, so
    no `Dispatch` row ever carries it — indistinguishable from a token
    nobody minted. Harmless: an agent with nothing to route has
    nothing to prove the token real for. Adding a `session_opened`
    event purely to make an unusable token provably real is not worth
    a union member.
    """
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LENGTH))


def serve_next(
    store: Store, routing: Ledger[RoutingEvent], session_token: str
) -> ServeResult:
    """Serve the next packet under `session_token`, or a terminal message.

    Walks `Store.untriaged_heat_scores` in heat order. For each
    candidate: render it, check the budget, then `reserve_dispatch`. A
    `False` reservation means a concurrent session took the item
    first — move on and render the next candidate; a collision costs
    one wasted render, never a wasted triage.

    The item cap is checked once, before any rendering, since it needs
    no size; the char cap is checked per candidate, since only its
    render produces `packet_chars`. A candidate that fails the char
    gate is discarded with no `dispatched` event, so it stays queued
    for the next call — this *is* "the batch ends with that item still
    queued".

    The session's first item is exempt from the char gate: whatever
    its size, it is always served, so no single oversized item can
    deadlock the queue. When its packet at the default valve exceeds
    `BATCH_CHARS`, `_fit_first_item` truncates its diff to whatever
    the budget has left, or serves it oversized when even the
    uncuttable parts alone don't fit — either way the item is served
    and the outcome is tagged in the `dispatched` event.

    That event also records `diff_valve_dropped`, always measured at
    `DIFF_BODY_MAX_CHARS` regardless of the cap this packet was
    finally rendered at, so the valve and the batch budget stay
    separately calibratable. The item gate
    cannot fire on the first item regardless — it fires at
    `items_used >= BATCH_ITEMS`, and the first item's `items_used` is
    always 0 — so it needs no matching exemption.
    """
    items_used, chars_used = store.session_usage(session_token)
    candidates = store.untriaged_heat_scores(HEAT_WEIGHTS)
    if not candidates:
        return ServeResult(packet=None, message="triage complete: 0 pending")

    first_of_session = items_used == 0
    if items_used >= BATCH_ITEMS:
        return ServeResult(packet=None, message="batch complete: budget exhausted")

    standing = build_standing(store)
    topics = _topic_line(store)
    for candidate in candidates:
        packet, packet_chars = _render_packet(
            store, standing, session_token, candidate.number, topics=topics
        )
        fit: DispatchFit = "fit"
        if first_of_session and packet_chars > BATCH_CHARS:
            packet, packet_chars, fit = _fit_first_item(
                store, standing, session_token, candidate.number, topics
            )
        elif not first_of_session and chars_used + packet_chars > BATCH_CHARS:
            return ServeResult(packet=None, message="batch complete: budget exhausted")
        item_id = store.get_issue_or_pr(candidate.number).id
        event: DispatchedEvent = {
            "type": "dispatched",
            "data": {
                "session": session_token,
                "item_id": item_id,
                "packet_chars": packet_chars,
                "fit": fit,
                "diff_valve_dropped": diff_body_dropped(
                    store, candidate.number, DIFF_BODY_MAX_CHARS
                ),
            },
        }
        if store.reserve_dispatch(routing, event):
            return ServeResult(packet=packet, message=None)
        # Lost the race for this candidate to a concurrent session.
        # `first_of_session` stays keyed to `items_used` from the top
        # of this call, so the next candidate is still exempt from
        # the char gate if every attempt so far has been raced away.

    # Every candidate raced away to other sessions between our
    # snapshot and our reservations. Nothing is left for *this* call
    # — but the corpus still has pending work, unlike the empty-queue
    # return above, so this must not share that string. The prefix is
    # load-bearing, not just the suffix: "batch complete:" tells the
    # orchestrator this worker stops but more sessions are worth
    # spawning; "triage complete:" tells it the corpus is done and no
    # further session should be spawned at all. Conflating the two
    # would make a fully contended queue indistinguishable from a
    # finished one.
    return ServeResult(packet=None, message="batch complete: all candidates taken")


def _topic_line(store: Store) -> str:
    """Render the `topics:` line naming every topic minted so far.

    Named unconditionally, `(none)` included, matching
    `status.render_status`'s own empty marker: an agent reading the
    packet must never have to tell "nothing minted yet" from "the
    packet omitted the line", and it is the second that this line
    exists to rule out. `Store.topics` is already name-ordered, which
    puts near-duplicates that share a prefix next to each other —
    the scan the agent is actually asked to perform.
    """
    names = [topic.name for topic in store.topics()]
    return f"topics: {', '.join(names) or '(none)'}"


def _render_packet(  # noqa: PLR0913 — one argument per rendering input
    store: Store,
    standing: StandingTable,
    session_token: str,
    number: IssueOrPRNumber,
    *,
    topics: str,
    diff_max_chars: int = DIFF_BODY_MAX_CHARS,
    note: str | None = None,
) -> tuple[str, int]:
    """Render one packet and the char count that counts against the budget.

    `packet_chars` measures the item line, the topic line and the
    rendered thread — the two-line header above them is fixed overhead
    the budget doesn't need to track, and folding it in would make the
    reported size depend on the session token's own length for no
    reason a caller cares about. `note`, when set, joins the header as
    a `packet-note: ` line beside `packet-chars`, so recording a
    truncation never changes what the truncation was measured against.

    `topics` arrives rendered rather than as a `Store` read here, so
    that `_fit_first_item`'s floor render and the re-render it sizes
    from that floor cannot disagree about the list: one read per
    `serve_next` call, reused across every render it performs.
    """
    rendered = render(store, standing, number, diff_max_chars=diff_max_chars)
    body = f"item: {number}\n{topics}\n{rendered}"
    packet_chars = len(body)
    header = f"session: {session_token}\npacket-chars: {packet_chars}\n"
    if note is not None:
        header += f"packet-note: {note}\n"
    return header + body, packet_chars


_DIFF_CUT_NOTE = "diff truncated to fit the batch budget"
_OVERSIZED_NOTE = (
    "served without its diff body; thread text and file list leave no budget for it"
)


def _fit_first_item(
    store: Store,
    standing: StandingTable,
    session_token: str,
    number: IssueOrPRNumber,
    topics: str,
) -> tuple[str, int, DispatchFit]:
    """Fit a session's oversized first item to BATCH_CHARS by cutting its diff.

    Called only when `number` is a session's first item and its
    packet at the default valve already exceeds `BATCH_CHARS`. Renders
    the floor — the same packet at `diff_max_chars=0` — to measure
    thread text and the diffstat file list, neither of which may be
    cut; the diff body's allowance is exactly what `BATCH_CHARS` has
    left over that floor, with nothing subtracted for the newline
    joining the body to the marker, since `_render_diff` emits the
    body element even when empty and that separator is already inside
    the floor.

    A positive allowance re-renders at that cap: the result fits by
    construction, because the cut is a real ceiling. Outcome:
    `diff_cut`. A non-positive allowance means the uncuttable parts
    alone exceed the budget, so the floor is served as-is. Outcome:
    `oversized`. Either way the item is served: the first-item
    exemption exists so no single item can deadlock the queue, and the
    guarantee it delivers is that the queue advances, not that every
    packet fits.
    """
    _floor_packet, floor_chars = _render_packet(
        store, standing, session_token, number, topics=topics, diff_max_chars=0
    )
    allowance = BATCH_CHARS - floor_chars
    if allowance > 0:
        packet, packet_chars = _render_packet(
            store,
            standing,
            session_token,
            number,
            topics=topics,
            diff_max_chars=allowance,
            note=_DIFF_CUT_NOTE,
        )
        return packet, packet_chars, "diff_cut"
    packet, packet_chars = _render_packet(
        store,
        standing,
        session_token,
        number,
        topics=topics,
        diff_max_chars=0,
        note=_OVERSIZED_NOTE,
    )
    return packet, packet_chars, "oversized"


def record_verdict(  # noqa: PLR0913 — one argument per field of a route event
    store: Store,
    routing: Ledger[RoutingEvent],
    session_token: str,
    number: IssueOrPRNumber,
    verdict: str,
    note: str,
) -> None:
    """Record a triage verdict for `number` under `session_token`.

    Rejects with `TriageSessionError`, never touching the ledger:

    - any verdict with no note, `NO_SIGNAL` included — a verdict with
      no reasoning behind it is not auditable, and writing an item off
      is as much a decision as routing it. A `NO_SIGNAL` note has no
      topic to attach to; it stays on the `Route` row, which is the
      only place holding why the item was passed over;
    - `number` not currently dispatched to `session_token` — the real
      guard, catching an agent routing an id it was never handed, or
      reusing a token after a requeue deleted its `Dispatch` row. It
      is a consistency check against agent error, not authentication;
      the allowlist hook is what constrains which agents call `route`
      at all;
    - `number` already carrying a *fresh* `Route` (its stamped
      `item_updated_at` still matches or leads the item's live
      `updated_at`) — rejecting a second verdict for the same item is
      a deliberate B1 policy, not a consequence of the ownership
      lookup above: `ingest.apply_routing_event` already supports a
      live re-route, but wiring one through here is out of scope until
      a later part asks for it. The freshness comparison is what tells
      a live re-route attempt from a reentered item's re-triage apart:
      a live re-route still carries a fresh `Route` and is rejected
      exactly as before, while a reentered item's `Route` only clears
      this check once it has gone genuinely stale — which is re-entry's
      whole path back through this function;
    - `number` never fetched into the corpus at all — converted from
      `Store.get_issue_or_pr`'s `IssueOrPRNotFoundError`, so a caller
      maps one exception class for every rejection here, not two.
    """
    if not note:
        raise TriageSessionError.missing_note(verdict)

    try:
        item = store.get_issue_or_pr(number)
    except IssueOrPRNotFoundError as error:
        raise TriageSessionError.unknown_number(number) from error
    item_id = item.id

    dispatch = store.dispatch_for(item_id)
    if dispatch is None or dispatch.session != session_token:
        raise TriageSessionError.not_dispatched(number, session_token)
    existing_route = store.route_for(item_id)
    if existing_route is not None and existing_route.item_updated_at >= item.updated_at:
        raise TriageSessionError.already_routed(number)

    store.record_routing_event(
        routing,
        RouteEvent(
            type="route",
            data={
                "session": session_token,
                "item_id": item_id,
                "verdict": verdict,
                "note": note,
                "item_updated_at": item.updated_at.isoformat(),
            },
        ),
    )

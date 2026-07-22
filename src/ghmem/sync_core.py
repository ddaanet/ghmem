"""Shared machinery for the per-connection sync drivers.

The four connection modules (`sync_items`, `sync_commits`,
`sync_discussions`, tied together by `sync`) all lean on the same
pieces, gathered here:

- the wire-Struct bases they build their responses from;
- the `_Query` template their GraphQL strings interpolate;
- the `_Sync` context threaded through every helper;
- the `_raw_data` projection that turns a wire node into ingest's raw
  `data` contract;
- the parse-before-append / fetch-failed / truncation plumbing;
- the cursor bookkeeping that lets each connection resume
  independently.

The `_Wire` hierarchy types what GraphQL sends back, never what gets
appended — that is ingest.py's `_Raw*` family. Every node is projected
through `_raw_data` and passed through `ingest.validate_raw_node`
before `_append_validated` writes it, so a reshaping bug raises
`msgspec.ValidationError` at the offending call instead of reaching the
ledger.
"""

from collections import Counter
from collections.abc import Callable, Iterator
from string import Template
from typing import NamedTuple, cast

import msgspec

from ghmem.aliases import GraphQLId, Json, JsonObject
from ghmem.github import GitHubClient
from ghmem.ingest import validate_raw_node
from ghmem.ledger import CursorData, FetchEvent, Ledger, RawNodeEvent

_OWNER = "micro-editor"
_REPO = "micro"
_PAGE_SIZE = 50

# --- Wire Structs shared across connections. ---


class _Wire(msgspec.Struct, kw_only=True, rename="camel"):
    """Base for GraphQL response wire Structs: kw-only, camelCase keys."""


class _PageInfo(_Wire):
    """A connection's `{hasNextPage, endCursor}` pagination cursor."""

    has_next_page: bool
    end_cursor: str | None


class _Page[T](_Wire):
    """A paginated connection page: `{pageInfo, nodes}`.

    Every first-page-only nested connection (reviews, review comments,
    files, closing issues, timelineItems) selects `pageInfo` too, not
    just `nodes` — `_check_truncated` uses it to detect a first page
    that didn't fully drain the connection.
    """

    page_info: _PageInfo
    nodes: list[T]


class _Author(_Wire):
    """A raw `{login}` author node."""

    login: str


class _NodeRef(_Wire):
    """A bare `{id}` reference to another node."""

    id: GraphQLId


class _UserCount(_Wire):
    """A reacting-users connection's `{totalCount}`."""

    total_count: int


class _ReactionGroupNode(_Wire):
    """One aggregated `{content, users{totalCount}}` reaction group."""

    content: str
    users: _UserCount


_REACTION_GROUPS = "reactionGroups { content users { totalCount } }"
"""The reaction selection, shared by every connection that carries one.

Reactions come from `reactionGroups`, GitHub's own non-paginated
aggregate, rather than from counting the individual `reactions`
connection — one selection, no pagination, and the counts arrive
pre-grouped by content.
"""


# --- The GraphQL query template. ---


class _Query(Template):
    """A GraphQL query template, with `%name` placeholders.

    GraphQL is brace-dense and spells its own variables `$name`, which
    rules out both of the obvious notations: an f-string would need
    every literal brace in the query doubled, and `string.Template`'s
    default `$` delimiter would collide with `$after`/`$id`/`$since`.
    `%` is not a token in GraphQL's grammar, so a `%name` placeholder
    cannot be mistaken for anything in the query it sits in.

    Values that GraphQL can carry as real query variables are passed
    that way instead — see each query's `graphql(...)` call. What is
    left for this template is what GraphQL has no variables for: the
    repository coordinates and the composed field sets.

    A query built with this reads below the function that issues it, but
    the field-set fragments it composes must read above it: `substitute`
    runs at import, so unlike a name used inside a function body, these
    bind in definition order.
    """

    delimiter = "%"


# --- Per-call context. ---


class _Sync(NamedTuple):
    """Per-call state threaded through every helper below.

    Bundles the client, the fetch ledger, and the running per-kind counts into
    one value, so no helper needs more than a handful of parameters.
    """

    client: GitHubClient
    fetch: Ledger[FetchEvent]
    counts: Counter[str]


# --- Raw-node reshaping: wire Struct -> ingest.py's `data` contract. ---


def _raw_data[F: msgspec.Struct](
    node: _Wire, fields: type[F], **injected: Json
) -> JsonObject:
    """Project a wire node onto `fields` and encode it as raw `data`.

    `fields` is the contract-shaped base of `node`'s wire struct, so
    projecting onto it drops the nested connections the driver drains
    separately and keeps exactly the keys `ingest.py`'s matching
    `_Raw*` struct declares. Encoding rather than hand-copying is what
    keeps the two in step: `rename="camel"` applies on the way out, so
    the wire struct's own field list *is* the emitted key list, and a
    field added on one side cannot silently go missing on the other.

    `injected` carries the relation ids no GraphQL node supplies —
    `itemId`, `prId`, `reviewId`, `discussionId`, `parentId` — which
    the driver knows only from the parent it drained the connection
    from.
    """
    projected = msgspec.convert(node, fields, from_attributes=True)
    return cast("JsonObject", msgspec.to_builtins(projected)) | injected


# --- Shared plumbing: parse-before-append, follow-up draining, cursors. ---


def _append_validated(ctx: _Sync, event: RawNodeEvent) -> None:
    """Validate `event`'s data against the raw-node contract, then append.

    Raises on a contract violation instead of writing: a malformed
    `data` dict is this driver's bug, and must surface immediately as
    a sync error rather than silently corrupt the ledger.
    """
    validate_raw_node(event["type"], event["data"])
    ctx.fetch.append(event)
    ctx.counts[event["type"]] += 1


def _append_reactions(
    ctx: _Sync, subject_id: GraphQLId, groups: list[_ReactionGroupNode]
) -> None:
    """Append one `reaction` event per group that anyone actually used.

    `subjectId` is injected from the node the connection was selected
    on, since a reaction group carries no reference to what it hangs
    off.

    Empty groups are dropped: GitHub returns a group per reaction kind
    whether it has users or not, and storing the zeroes would be a row
    per kind per subject saying nothing.
    """
    for group in groups:
        if group.users.total_count > 0:
            _append_validated(
                ctx,
                {
                    "type": "reaction",
                    "data": {
                        "subjectId": subject_id,
                        "content": group.content,
                        "count": group.users.total_count,
                    },
                },
            )


def _append_fetch_failed(ctx: _Sync, node_id: str, reason: str) -> None:
    """Record a skipped node as `fetch_failed` — never aborts the page."""
    ctx.fetch.append(
        {"type": "fetch_failed", "data": {"id": node_id, "reason": reason}}
    )
    ctx.counts["fetch_failed"] += 1


def _check_truncated[T](
    ctx: _Sync, item_id: GraphQLId, name: str, page: _Page[T]
) -> None:
    """Record a `fetch_failed` if `page`'s first page didn't fully drain.

    Reviews, review comments, files, closing-issue references, and
    timelineItems are all taken as first-page-only by design (never
    drained past `pageInfo`) — this makes an oversized connection's
    truncation visible via `store.fetch_failures()` instead of
    silently losing data, without aborting the sync.

    `item_id` is the connection's owner — the PR for most, but the
    review for review comments. Keyed by `f"{item_id}:{name}"` so two
    truncated connections on one owner stay distinct rows instead of
    colliding on replay.
    """
    if page.page_info.has_next_page:
        count = len(page.nodes)
        reason = (
            f"{name} truncated at first page ({count} nodes, hasNextPage) on {item_id}"
        )
        _append_fetch_failed(ctx, f"{item_id}:{name}", reason)


def _drain_follow_up[T](
    page_info: _PageInfo, fetch_page: Callable[[str], tuple[list[T], _PageInfo]]
) -> Iterator[T]:
    """Yield nodes from follow-up pages after a connection's first page.

    `fetch_page(cursor)` must issue exactly one follow-up GraphQL call
    for `cursor` and return that page's nodes and `pageInfo`. Shared by
    every nested connection that can exceed one page: an item's
    comments, a discussion's comments, and a comment's replies.
    """
    while page_info.has_next_page:
        assert page_info.end_cursor is not None, "hasNextPage true without endCursor"
        nodes, page_info = fetch_page(page_info.end_cursor)
        yield from nodes


def _paginate[T, R: msgspec.Struct](
    ctx: _Sync,
    connection: str,
    query: str,
    response: type[R],
    page_of: Callable[[R], _Page[T]],
) -> Iterator[_Page[T]]:
    """Yield `connection`'s top-level pages, resuming where it left off.

    Owns the whole cursor-driven loop: where to start (`_resume_cursor`
    on the persisted ledger), when to stop (`hasNextPage`), and what to
    ask for next. `page_of` picks the connection out of the decoded
    response, which is the only part that differs between issues, pull
    requests and discussions.

    The caller drains each page as it arrives and appends its `cursor`
    event, because only the caller knows the `updated_at` high-water
    mark that event carries. Persisting it there also keeps the
    ordering that makes a resume safe: a page's cursor is written after
    that page is fully drained, never before.

    `_sync_commits` deliberately does not use this. Its connection is
    newest-first and bounded by a `since:` argument derived from the
    high-water mark, so it neither starts from a stored cursor nor
    stops on the same condition — folding both shapes into one helper
    would cost more in branches than the loop it saves.
    """
    after = _resume_cursor(ctx.fetch, connection)
    while True:
        page = page_of(
            ctx.client.graphql(
                query, {"first": _PAGE_SIZE, "after": after}, response=response
            )
        )
        yield page
        if not page.page_info.has_next_page:
            return
        after = page.page_info.end_cursor
        assert after is not None, "hasNextPage true without endCursor"


def _persist_cursor[T](ctx: _Sync, connection: str, page: _Page[T], hwm: str) -> None:
    """Record `connection`'s resume point after a page is fully drained.

    Called by each drain function once its page is completely
    appended — the ordering `_paginate` relies on, since a cursor
    written before its page finished would resume past nodes that were
    never recorded.

    A page with no `endCursor` writes nothing: there is no position to
    resume from, and a `cursor` event carrying `None` would be
    indistinguishable from starting over.
    """
    if page.page_info.end_cursor is None:
        return
    ctx.fetch.append(
        {
            "type": "cursor",
            "data": {
                "connection": connection,
                "top_level_cursor": page.page_info.end_cursor,
                "updated_at_hwm": hwm,
            },
        }
    )


def _last_cursor(fetch: Ledger[FetchEvent], connection: str) -> CursorData | None:
    """Return the most recently persisted cursor for `connection`, if any."""
    last: CursorData | None = None
    for event in fetch:
        if event["type"] == "cursor" and event["data"]["connection"] == connection:
            last = event["data"]
    return last


def _resume_cursor(fetch: Ledger[FetchEvent], connection: str) -> str | None:
    """Return the `after` cursor to resume `connection` from, or `None`."""
    cursor = _last_cursor(fetch, connection)
    return None if cursor is None else cursor["top_level_cursor"]


def _last_hwm(fetch: Ledger[FetchEvent], connection: str) -> str | None:
    """Return `connection`'s last persisted high-water mark, if any."""
    cursor = _last_cursor(fetch, connection)
    return None if cursor is None else cursor["updated_at_hwm"]

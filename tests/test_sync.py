"""Cross-cutting tests for the full sync driver over every connection."""

from collections import Counter
from pathlib import Path
from typing import cast

from pytest_mock import MockerFixture

from ghmem.aliases import IssueOrPRNumber, JsonObject
from ghmem.ledger import FetchEvent, Ledger
from ghmem.sync import sync
from tests.sync_helpers import _ALL_PAGES, _client, _rebuild


def test_full_sync_appends_expected_nodes(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A full sync appends every node kind the fixtures carry.

    When: `sync` drains all four connections over the fixture pages —
      a 2-page issues connection (one item with a second nested comment
      page, a `timelineItems` cross-referenced mention, and one null
      node), a PR page (a review, files, and a closing issue
      reference), a commit-history page, and a 2-page discussions
      connection (whose second page also carries a null node), with
      reactions on items, comments, reviews, review comments,
      discussion comments and discussion replies alike
    Then: the returned counts match the fixtures' node census — one
      `cross_ref` from the closing reference and one from the
      `timelineItems` mention, one `fetch_failed` per null node, and
      one `reaction` per non-empty reaction group across every subject
      kind
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    client = _client(mocker, _ALL_PAGES)

    result = sync(client, fetch)

    assert result.counts == Counter(
        item=3,
        comment=3,
        review=1,
        review_comment=1,
        reaction=13,
        cross_ref=2,
        commit=2,
        pr_diff=1,
        fetch_failed=2,
        discussion=3,
        discussion_comment=6,
    )


def test_appended_nodes_satisfy_raw_contract(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Every node a full sync appends parses back through `rebuild`.

    When: the store is rebuilt from a full sync's fetch ledger
    Then: rebuild raises nothing, and the fetched rows are queryable
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    client = _client(mocker, _ALL_PAGES)
    sync(client, fetch)

    store = _rebuild(fetch, tmp_path)

    assert store.get_issue_or_pr(IssueOrPRNumber(1)).title.startswith("Softwrap")
    assert len(store.commits()) == 2


def test_injected_relation_ids_attached(mocker: MockerFixture, tmp_path: Path) -> None:
    """Comment/review/review-comment/reaction nodes carry their parent id.

    GraphQL never supplies these; the driver attaches them from the
    parent it drained the connection from.

    When: a full sync appends a comment, a review, a review comment,
      a reaction, and both cross_ref kinds (a closing reference and a
      `timelineItems` mention)
    Then: each one's `data` carries the injected id GraphQL didn't
      send, and each cross_ref carries the right `srcId`/`dstId`/`kind`
    """
    fetch: Ledger[FetchEvent] = Ledger(tmp_path / "fetch.jsonl")
    client = _client(mocker, _ALL_PAGES)
    sync(client, fetch)

    by_type: dict[str, list[JsonObject]] = {}
    for event in fetch:
        if event["type"] not in ("cursor", "fetch_failed"):
            by_type.setdefault(event["type"], []).append(
                cast("JsonObject", event["data"])
            )

    comment = next(c for c in by_type["comment"] if c["id"] == "C_3")
    assert comment["itemId"] == "I_3"
    review = by_type["review"][0]
    assert review["prId"] == "I_3"
    review_comment = by_type["review_comment"][0]
    assert review_comment["reviewId"] == review["id"]
    reaction = next(r for r in by_type["reaction"] if r["subjectId"] == "I_1")
    assert reaction["content"] == "THUMBS_UP"
    closes = next(r for r in by_type["cross_ref"] if r["kind"] == "closes")
    assert closes == {"srcId": "I_3", "dstId": "I_1", "kind": "closes"}
    mention = next(r for r in by_type["cross_ref"] if r["kind"] == "cross_referenced")
    assert mention == {"srcId": "I_3", "dstId": "I_2", "kind": "cross_referenced"}

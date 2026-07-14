"""Compact single-thread markdown renderer for one issue or pull request.

`render` resolves one issue/PR by number and renders its whole thread —
header, body, timeline (comments merged with reviews and their nested
review comments), cross-references, reactions, and (for pull requests)
the diff — as deterministic markdown, annotating every author with
their standing as of that entry's own date. Offline and
snapshot-testable: it only ever reads from an already-populated
`Store`, never the network.
"""

from collections import defaultdict
from datetime import datetime

from ghmem.aliases import DiscussionNumber, GraphQLId, IssueOrPRNumber
from ghmem.exceptions import PRDiffNotFoundError
from ghmem.models import (
    Comment,
    Discussion,
    DiscussionComment,
    FileStat,
    IssueOrPR,
    Review,
)
from ghmem.standing import StandingTable
from ghmem.store import Store

DIFF_BODY_MAX_CHARS = 300  # PROVISIONAL — calibrated in subproject 4

__all__ = ["DIFF_BODY_MAX_CHARS", "render", "render_discussion"]


def render(store: Store, standing: StandingTable, number: IssueOrPRNumber) -> str:
    """Render issue/PR `number`'s full thread as deterministic markdown.

    Resolves the item via `store.get_issue_or_pr`, so an unknown
    `number` propagates `IssueOrPRNotFoundError` uncaught.
    """
    item = store.get_issue_or_pr(number)
    lines: list[str] = [*_render_header(item, standing), "", item.body, ""]
    lines.extend(_render_timeline(store, standing, item.id))
    lines.extend(_render_cross_refs(store, item.id))
    lines.extend(_render_reactions(store, item.id))
    lines.extend(_render_diff(store, number))
    return "\n".join(lines).rstrip() + "\n"


def _render_header(item: IssueOrPR, standing: StandingTable) -> list[str]:
    """Render the title line and the field summary block."""
    who = standing.annotation(item.author, item.created_at.date())
    return [
        f"# #{item.number} {item.title}",
        "",
        f"- state: {item.state}",
        f"- kind: {item.kind}",
        f"- author: {who}",
        f"- created: {item.created_at.isoformat()}",
        f"- updated: {item.updated_at.isoformat()}",
    ]


def _render_timeline(
    store: Store, standing: StandingTable, item_id: GraphQLId
) -> list[str]:
    """Render comments and reviews merged into one chronological stream."""
    entries: list[tuple[datetime, str, Comment | Review]] = [
        (c.created_at, c.id, c) for c in store.comments_for(item_id)
    ] + [(r.submitted_at, r.id, r) for r in store.reviews_for(item_id)]
    if not entries:
        return []
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    lines = ["## Timeline", ""]
    for _timestamp, _id, entry in entries:
        if isinstance(entry, Comment):
            lines.extend(_render_comment(entry, standing))
        else:
            lines.extend(_render_review(store, entry, standing))
        lines.append("")
    return lines


def _render_comment(comment: Comment, standing: StandingTable) -> list[str]:
    """Render one general comment entry."""
    who = standing.annotation(comment.author, comment.created_at.date())
    return [f"### {comment.created_at.isoformat()} — {who}", "", comment.body]


def _render_review(store: Store, review: Review, standing: StandingTable) -> list[str]:
    """Render one review entry, with its review comments nested below."""
    who = standing.annotation(review.author, review.submitted_at.date())
    lines = [
        f"### {review.submitted_at.isoformat()} — review by {who} [{review.state}]",
        "",
        review.body,
    ]
    for rc in store.review_comments_for(review.id):
        rc_who = standing.annotation(rc.author, rc.created_at.date())
        lines.append("")
        lines.append(f"- {rc.created_at.isoformat()} — {rc_who} [{rc.path}]")
        lines.append(f"  {rc.body}")
    return lines


def _render_cross_refs(store: Store, item_id: GraphQLId) -> list[str]:
    """Render cross-references, resolved to their number and title."""
    refs = store.cross_refs_for(item_id)
    if not refs:
        return []
    entries: list[tuple[tuple[str, int, int | str], str]] = []
    for ref in refs:
        other_id = ref.dst_id if ref.src_id == item_id else ref.src_id
        other = store.issue_or_pr_by_id(other_id)
        if other is None:
            entries.append(((ref.kind, 1, other_id), f"- {other_id} ({ref.kind})"))
        else:
            entries.append(
                (
                    (ref.kind, 0, other.number),
                    f"- #{other.number}: {other.title} ({ref.kind})",
                )
            )
    entries.sort(key=lambda entry: entry[0])
    return ["## Cross-references", "", *[line for _key, line in entries], ""]


def _render_reactions(store: Store, item_id: GraphQLId) -> list[str]:
    """Render reactions as content x count pairs, already content-ordered."""
    reactions = store.reactions_for(item_id)
    if not reactions:
        return []
    lines = ["## Reactions", ""]
    lines.extend(f"- {r.content}\N{MULTIPLICATION SIGN}{r.count}" for r in reactions)
    lines.append("")
    return lines


def render_discussion(
    store: Store, standing: StandingTable, number: DiscussionNumber
) -> str:
    """Render discussion `number`'s whole thread as deterministic markdown.

    Resolves the discussion via `store.get_discussion`, so an unknown
    `number` propagates `DiscussionNotFoundError` uncaught.
    """
    discussion = store.get_discussion(number)
    lines: list[str] = [
        *_render_discussion_header(discussion, standing),
        "",
        discussion.body,
        "",
    ]
    lines.extend(_render_discussion_comments(store, standing, discussion))
    lines.extend(_render_reactions(store, discussion.id))
    return "\n".join(lines).rstrip() + "\n"


def _render_discussion_header(
    discussion: Discussion, standing: StandingTable
) -> list[str]:
    """Render the title line and the category/author summary block."""
    who = standing.annotation(discussion.author, discussion.created_at.date())
    return [
        f"# discussion #{discussion.number} {discussion.title}",
        "",
        f"- category: {discussion.category}",
        f"- author: {who}",
    ]


def _render_discussion_comments(
    store: Store, standing: StandingTable, discussion: Discussion
) -> list[str]:
    """Render comments nested one level: top-level, each followed by its replies.

    GitHub discussions nest exactly one level, so this is a two-pass
    group-by-`parent_id` over the single `discussion_comments_for`
    list rather than a recursive render.
    """
    comments = store.discussion_comments_for(discussion.id)
    top_level = [c for c in comments if c.parent_id is None]
    if not top_level:
        return []
    replies_by_parent: dict[GraphQLId, list[DiscussionComment]] = defaultdict(list)
    for c in comments:
        if c.parent_id is not None:
            replies_by_parent[c.parent_id].append(c)
    lines = ["## Comments", ""]
    for comment in top_level:
        lines.extend(_render_discussion_comment(comment, standing, discussion))
        lines.append("")
        for reply in replies_by_parent[comment.id]:
            lines.extend(
                _render_discussion_comment(reply, standing, discussion, indent=True)
            )
            lines.append("")
    return lines


def _render_discussion_comment(
    comment: DiscussionComment,
    standing: StandingTable,
    discussion: Discussion,
    *,
    indent: bool = False,
) -> list[str]:
    """Render one discussion comment or reply, marking it if it's the answer."""
    prefix = "  " if indent else ""
    who = standing.annotation(comment.author, comment.created_at.date())
    lines = []
    if comment.id == discussion.answer_id:
        lines.append(f"{prefix}**\N{CHECK MARK} Accepted answer**")
    lines.append(f"{prefix}### {comment.created_at.isoformat()} — {who}")
    lines.append("")
    lines.append(f"{prefix}{comment.body}")
    return lines


def _render_diff(store: Store, number: IssueOrPRNumber) -> list[str]:
    """Render the diffstat and (possibly truncated) diff body for a PR.

    Issues have no diff — `PRDiffNotFoundError` means the section is
    omitted entirely.
    """
    try:
        diff = store.pr_diff_for(number)
    except PRDiffNotFoundError:
        return []
    lines = ["## Diff", "", *[_format_file_stat(f) for f in diff.diffstat], ""]
    cut = diff.diff_text.find("\n", DIFF_BODY_MAX_CHARS)
    lines.append("```diff")
    lines.append(diff.diff_text[: cut if cut != -1 else None])
    if cut != -1 or diff.truncated:
        lines.append("… [diff truncated] …")
    lines.append("```")
    lines.append("")
    return lines


def _format_file_stat(f: FileStat) -> str:
    """Render one diffstat line: `path +additions -deletions`."""
    return f"- {f.path} +{f.additions} -{f.deletions}"

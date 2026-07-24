"""Exceptions raised across ghmem: store lookup misses and sync errors.

`IssueOrPRNotFoundError`, `PRDiffNotFoundError`, and
`DiscussionNotFoundError` are the SQLite-mirror lookup misses.
`RateLimitError`, `AuthError` and `DiffFetchError` belong to the GitHub
sync client (`github.py`) — none is a lookup miss, so none subclasses
`LookupError`. `UnknownRoutingEventError` belongs to ledger replay
(`ingest.py`): a bad event tag is bad input, hence `ValueError`.
`TriageSessionError` belongs to the triage session loop
(`triage.py`): a `route` request inconsistent with current routing
state is bad input too. `TopicNotRoutedError` belongs to topic
synthesis (`topic.py`): assembling a topic with no routed items is bad
input too. `UnknownSnapshotError` belongs to topic close
(`dispatch_store.py`): closing a token with no matching `Snapshot` row
is bad input too.
"""

from datetime import datetime
from pathlib import Path

from ghmem.aliases import IssueOrPRNumber
from ghmem.ledger import NO_SIGNAL


class IssueOrPRNotFoundError(LookupError):
    """Raised when `Store.get_issue_or_pr` finds no matching row."""


class PRDiffNotFoundError(LookupError):
    """Raised when `Store.pr_diff_for` finds no diff for the given PR."""


class DiscussionNotFoundError(LookupError):
    """Raised when `Store.get_discussion` finds no matching row."""


class RateLimitError(Exception):
    """Raised when a GraphQL call's remaining budget falls below margin."""

    def __init__(self, reset_at: datetime) -> None:
        """Carry `reset_at`, the budget's next reset time."""
        super().__init__(f"GitHub rate limit exhausted; resets at {reset_at}")
        self.reset_at = reset_at


class AuthError(Exception):
    """Raised when no GitHub token is found in `$GITHUB_TOKEN` or `gh`."""


class DiffFetchError(Exception):
    """Raised when the REST diff fetch for one PR fails outright.

    Distinct from the oversized-diff refusal, which `pr_diff_text`
    absorbs into its `truncated` flag and is not an error at all. This
    is every other way that call can fail — a 404 on a PR GraphQL
    listed, a 5xx that outlived githubkit's retry chain.

    It exists so `sync_items` can record a `fetch_failed` for the one
    PR and carry on, instead of one bad diff aborting a whole
    connection leg. Catching githubkit's own `RequestFailed` there
    would work equally well and is exactly what this avoids:
    `github.py` is where the githubkit dependency stops.
    """

    def __init__(self, number: IssueOrPRNumber, status_code: int) -> None:
        """Name the PR and the status its diff fetch failed with."""
        super().__init__(f"diff fetch for #{number} failed with HTTP {status_code}")
        self.number = number
        self.status_code = status_code


class UnknownRoutingEventError(ValueError):
    """Raised when a routing ledger carries an event kind with no reducer.

    The `RoutingEvent` union grows a member per dispatch part, and the
    reducer must grow with it: a tag it does not handle means the
    ledger is ahead of the code, which is a hard stop rather than a
    verdict to guess at.
    """

    def __init__(self, event_type: str) -> None:
        """Name the unhandled event tag."""
        super().__init__(f"no reducer for routing event type {event_type!r}")
        self.event_type = event_type


class MalformedFrontmatterError(ValueError):
    """Raised when a topic doc's frontmatter is unreadable or lacks its hook.

    `ghmem index` reads each doc's `description:` as its index hook. A
    doc that does not open with a `---` frontmatter fence, whose fence
    never closes, or whose frontmatter is not a mapping carrying a
    string `description` is a synthesis bug the human should see. Like
    `UnknownRoutingEventError` it is bad input — `index` raises here
    rather than indexing a blank hook line.
    """

    def __init__(self, path: Path) -> None:
        """Name the doc whose frontmatter could not be read."""
        super().__init__(f"{path}: malformed frontmatter or missing description")
        self.path = path


class TriageSessionError(ValueError):
    """Raised when a `route` request is inconsistent with routing state.

    A topic verdict with no note, `no-signal` carrying a note, a
    number never dispatched to the calling session (or dispatched to
    a different one), a number already carrying a `Route`, or a
    number never fetched into the corpus at all — bad input, the same
    reasoning that puts `UnknownRoutingEventError` here. The ownership
    check is a consistency check against agent error, not
    authentication: the allowlist hook is what constrains which
    agents can call `route` at all.

    Rejecting a second `route` for an already-routed number is a
    deliberate B1 policy, not a side effect of the ownership lookup:
    `ingest.apply_routing_event` already supports latest-wins
    re-routing on replay, but wiring a live re-route through
    `record_verdict` is out of scope until a later part asks for it.

    Built through its named constructors below rather than a message
    assembled at the raise site in `triage.py`, so the reason for
    each rejection lives in one place next to the others. Each keeps
    its structured value as an attribute, matching
    `RateLimitError.reset_at` and `UnknownRoutingEventError.event_type`.
    """

    def __init__(
        self,
        message: str,
        *,
        verdict: str | None = None,
        number: IssueOrPRNumber | None = None,
        session_token: str | None = None,
    ) -> None:
        """Carry whichever structured value the raising constructor supplies."""
        super().__init__(message)
        self.verdict = verdict
        self.number = number
        self.session_token = session_token

    @classmethod
    def missing_note(cls, verdict: str) -> TriageSessionError:
        """Report a topic verdict (anything but `NO_SIGNAL`) with no note."""
        return cls(f"verdict {verdict!r} needs a note", verdict=verdict)

    @classmethod
    def stray_note(cls) -> TriageSessionError:
        """`NO_SIGNAL` carried a note with nothing for it to attach to."""
        return cls(f"{NO_SIGNAL!r} takes no note")

    @classmethod
    def not_dispatched(
        cls, number: IssueOrPRNumber, session_token: str
    ) -> TriageSessionError:
        """Report `number` has no outstanding `Dispatch` to `session_token`."""
        return cls(
            f"#{number} is not currently dispatched to session {session_token!r}",
            number=number,
            session_token=session_token,
        )

    @classmethod
    def already_routed(cls, number: IssueOrPRNumber) -> TriageSessionError:
        """Report `number` already carries a `Route` from an earlier verdict."""
        return cls(f"#{number} is already routed", number=number)

    @classmethod
    def unknown_number(cls, number: IssueOrPRNumber) -> TriageSessionError:
        """Report `number` was never fetched into the corpus.

        Converts `Store.get_issue_or_pr`'s `IssueOrPRNotFoundError` at
        the boundary, so a caller — Task 4's CLI included — maps one
        exception class for every way `record_verdict` can reject a
        request, not two.
        """
        return cls(f"#{number} was never fetched into the corpus", number=number)


class TopicNotRoutedError(ValueError):
    """Raised when `topic.assemble` finds no item currently routed to a topic.

    Mirrors `UnknownRoutingEventError`: nothing to synthesize is bad
    input to the command, not a lookup miss over a corpus row, hence
    `ValueError` rather than `LookupError`.
    """

    def __init__(self, name: str) -> None:
        """Name the topic that carries no routed items."""
        super().__init__(f"topic {name!r} has no routed items")
        self.name = name


class UnknownSnapshotError(ValueError):
    """Raised when `close_topic` is given a token with no matching Snapshot.

    Mirrors `TopicNotRoutedError`: a token `topic.assemble` never
    minted is bad input to the command, not a lookup miss over a
    corpus row, hence `ValueError` rather than `LookupError`.
    """

    def __init__(self, token: str) -> None:
        """Name the token that carries no snapshot."""
        super().__init__(f"no snapshot for token {token!r}")
        self.token = token

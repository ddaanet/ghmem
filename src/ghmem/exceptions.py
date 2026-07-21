"""Exceptions raised across ghmem: store lookup misses and sync errors.

`IssueOrPRNotFoundError`, `PRDiffNotFoundError`, and
`DiscussionNotFoundError` are the SQLite-mirror lookup misses.
`RateLimitError` and `AuthError` belong to the GitHub sync client
(`github.py`) — neither is a lookup miss, so neither subclasses
`LookupError`. `UnknownRoutingEventError` belongs to ledger replay
(`ingest.py`): a bad event tag is bad input, hence `ValueError`.
"""

from datetime import datetime


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

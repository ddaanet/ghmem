"""Exceptions raised across ghmem: store lookup misses and sync errors.

`IssueOrPRNotFoundError`, `PRDiffNotFoundError`, and
`DiscussionNotFoundError` are the SQLite-mirror lookup misses.
`RateLimitError` and `AuthError` belong to the GitHub sync client
(`github.py`) — neither is a lookup miss, so neither subclasses
`LookupError`.
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

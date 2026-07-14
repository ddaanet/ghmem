"""Lookup failures raised by `Store` query methods.

Later parts (the GitHub sync client) add their own exception types to this
module; this file defines only the SQLite-mirror lookup misses.
"""


class IssueOrPRNotFoundError(LookupError):
    """Raised when `Store.get_issue_or_pr` finds no row with the given number."""


class PRDiffNotFoundError(LookupError):
    """Raised when `Store.pr_diff_for` finds no diff for the given PR."""

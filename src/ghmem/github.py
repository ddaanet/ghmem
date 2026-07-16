"""Thin githubkit wrapper: GraphQL metering, REST diff fetch, auth.

Not a pagination driver — that's T2/T3. This module owns exactly three
things: turning a raw GraphQL response into a caller's `msgspec.Struct`
while metering the point budget, fetching one PR's diff text over
REST, and resolving a token. Every retry/backoff decision beyond that
is githubkit's own.

`GitHub(..., auto_retry=RETRY_SERVER_ERROR)` deliberately narrows
githubkit's default retry chain (`RETRY_DEFAULT = RETRY_RATE_LIMIT +
RETRY_SERVER_ERROR`) to the 5xx leg only. `RETRY_RATE_LIMIT` reacts to
a rate limit by blocking in `time.sleep(x-ratelimit-reset - now)` — up
to an hour on a primary limit — which would silently swallow the
persist-cursor-and-exit sync design and hang instead. Declining it
here means a rate limit surfaces as `RateLimitError` (see `graphql`
below), not a stall.
"""

import os
import subprocess
from datetime import datetime

import msgspec
from githubkit import GitHub
from githubkit.exception import RequestFailed
from githubkit.response import Response
from githubkit.retry import RETRY_SERVER_ERROR

from ghmem.aliases import IssueOrPRNumber, JsonObject
from ghmem.exceptions import AuthError, RateLimitError

_OWNER = "micro-editor"
_REPO = "micro"

_DIFF_ACCEPT = "application/vnd.github.v3.diff"

_RATE_LIMIT_SAFETY_MARGIN = 100
"""Points reserved below which `graphql` refuses another call.

GitHub's GraphQL primary limit is 5,000 points/hour; a single well-formed page
query rarely costs more than this margin, so falling under it means the *next*
call would likely overdraw the budget outright rather than complete.
"""


class _RateLimit(msgspec.Struct, rename="camel"):
    """The `rateLimit { cost remaining resetAt }` block every query selects."""

    remaining: int
    reset_at: datetime


class GitHubClient:
    """Authenticated wrapper around one `githubkit.GitHub` instance."""

    def __init__(self, token: str) -> None:
        """Construct a client authenticated with `token`."""
        self._gh = GitHub(token, auto_retry=RETRY_SERVER_ERROR)

    @classmethod
    def from_env(cls) -> GitHubClient:
        """Build a client from `$GITHUB_TOKEN`, else `gh auth token`.

        Raises `AuthError` if neither source yields a token.
        """
        token = os.environ.get("GITHUB_TOKEN") or _gh_auth_token()
        if not token:
            msg = "no GitHub token: set $GITHUB_TOKEN or run `gh auth login`"
            raise AuthError(msg)
        return cls(token)

    def graphql[R: msgspec.Struct](
        self, query: str, variables: JsonObject, response: type[R]
    ) -> R:
        """Run one GraphQL query and convert its `data` block into `response`.

        `query` must select `rateLimit { cost remaining resetAt }` —
        githubkit does no cost accounting of its own, and this is the
        only way to see the point budget. That budget is metered
        after every call: if `remaining` has fallen below the safety
        margin, this raises `RateLimitError(reset_at)` instead of
        returning, so a caller stops before a later call runs the
        budget dry mid-page.
        """
        data = self._gh.graphql(query, variables)
        rate_limit = msgspec.convert(data["rateLimit"], _RateLimit, strict=True)
        if rate_limit.remaining < _RATE_LIMIT_SAFETY_MARGIN:
            raise RateLimitError(rate_limit.reset_at)
        return msgspec.convert(data, response, strict=True)

    def pr_diff_text(self, number: IssueOrPRNumber) -> tuple[str, bool]:
        """Fetch the unified diff text for PR `number` over REST.

        Returns `(diff_text, truncated)`. `truncated` is `True` when
        GitHub refuses to generate an oversized diff — verified live
        against `micro-editor/micro#1392` (228,016 additions) on
        2026-07-16: HTTP 406 with a JSON body whose `errors[].code` is
        `"too_large"`. `diff_text` is `""` in that case; GitHub sends
        no diff body with the refusal. Per-file stats are not fetched
        here — that's the GraphQL `pullRequest.files` connection in
        T2, keeping this call to ~1 REST request per PR.
        """
        try:
            resp = self._gh.request(
                "GET",
                f"/repos/{_OWNER}/{_REPO}/pulls/{number}",
                headers={"Accept": _DIFF_ACCEPT},
            )
        except RequestFailed as exc:
            if _is_diff_too_large(exc.response):
                return "", True
            raise
        return resp.text, False


def _is_diff_too_large(response: Response) -> bool:
    """Match GitHub's refusal signal for an oversized PR diff."""
    if response.status_code != 406:
        return False
    errors = response.json().get("errors") or []
    return any(error.get("code") == "too_large" for error in errors)


def _gh_auth_token() -> str | None:
    """Return the token from `gh auth token`, or `None` if unavailable."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, check=False, text=True
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None

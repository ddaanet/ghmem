"""Test the githubkit client wrapper: metering, conversion, diff fetch."""

from datetime import UTC, datetime

import httpx
import msgspec
import pytest
from githubkit.exception import RequestFailed
from githubkit.response import Response as GHResponse
from githubkit.retry import RETRY_SERVER_ERROR
from pytest_mock import MockerFixture

from ghmem.aliases import IssueOrPRNumber, JsonObject
from ghmem.exceptions import AuthError, DiffFetchError, RateLimitError
from ghmem.github import GitHubClient


class _Payload(msgspec.Struct):
    """A minimal caller Struct standing in for a real query's response."""

    count: int


def _client(mocker: MockerFixture, **handle: object) -> GitHubClient:
    """Build a client whose githubkit handle is a mock configured by `handle`.

    Patches `githubkit.GitHub` where `ghmem.github` imported it, so the
    constructor hands back the mock and the client is built exactly as
    production builds it. Reaching in to reassign `client._gh` after
    construction would test the same behaviour while pinning a private
    attribute name, and would skip whatever `__init__` does — including
    the `auto_retry` narrowing that
    `test_from_env_builds_client_with_narrowed_retry` exists to pin.
    """
    mocker.patch("ghmem.github.GitHub", return_value=mocker.Mock(**handle))
    return GitHubClient("tok")


def _rate_limit(remaining: int) -> JsonObject:
    """Build a `rateLimit` block with the given remaining budget."""
    return {"cost": 1, "remaining": remaining, "resetAt": "2026-07-16T18:00:00Z"}


def _request_failed(status_code: int, body: JsonObject) -> RequestFailed:
    """Build a real `RequestFailed` carrying `status_code` and JSON `body`.

    No network involved: `httpx.Request`/`httpx.Response` are plain
    in-memory objects here, just like the ones githubkit's transport
    would have produced from a real refusal.
    """
    return _failed_response(httpx.Response(status_code, request=_REQUEST, json=body))


def _failed_response(raw_response: httpx.Response) -> RequestFailed:
    """Wrap an already-built `httpx.Response` as githubkit's `RequestFailed`."""
    return RequestFailed(GHResponse(raw_response, object))


_REQUEST = httpx.Request("GET", "https://api.github.com/repos/x/y/pulls/1")


def test_graphql_converts_into_struct(mocker: MockerFixture) -> None:
    """A well-formed response is converted into the passed Struct.

    When: `graphql` is called with a Struct type and githubkit returns a
      `data` block matching it plus a healthy `rateLimit`
    Then: the returned value is a populated instance of that Struct
    """
    client = _client(
        mocker,
        graphql=mocker.Mock(return_value={"rateLimit": _rate_limit(4999), "count": 42}),
    )

    result = client.graphql("query { ... }", {}, _Payload)

    assert result == _Payload(count=42)


def test_graphql_rejects_malformed_response(mocker: MockerFixture) -> None:
    """A wrong-typed field raises instead of returning junk.

    When: the `data` block's `count` field is a string where the Struct
      declares an int
    Then: `graphql` raises rather than returning a mistyped instance
    """
    client = _client(
        mocker,
        graphql=mocker.Mock(
            return_value={"rateLimit": _rate_limit(4999), "count": "not-an-int"}
        ),
    )

    with pytest.raises(msgspec.ValidationError):
        client.graphql("query { ... }", {}, _Payload)


def test_rate_limit_exhaustion_raises(mocker: MockerFixture) -> None:
    """A remaining budget below the safety margin raises `RateLimitError`.

    When: the `rateLimit` block's `remaining` is below the safety margin
    Then: `graphql` raises `RateLimitError` carrying the parsed `resetAt`
    """
    client = _client(
        mocker,
        graphql=mocker.Mock(return_value={"rateLimit": _rate_limit(0), "count": 42}),
    )

    with pytest.raises(RateLimitError) as exc_info:
        client.graphql("query { ... }", {}, _Payload)

    assert exc_info.value.reset_at == datetime(2026, 7, 16, 18, 0, tzinfo=UTC)


def test_pr_diff_text_sets_diff_accept_header(mocker: MockerFixture) -> None:
    """`pr_diff_text` requests the REST diff media type.

    When: `pr_diff_text` is called for a PR number
    Then: the underlying REST call carries the diff `Accept` header
    """
    request = mocker.Mock(return_value=mocker.Mock(text="diff --git a/x b/x\n"))
    client = _client(mocker, request=request)

    diff_text, truncated = client.pr_diff_text("acme", "widget", IssueOrPRNumber(1))

    assert diff_text == "diff --git a/x b/x\n"
    assert truncated is False
    _method, _url = request.call_args.args
    headers = request.call_args.kwargs["headers"]
    assert headers["Accept"] == "application/vnd.github.v3.diff"


def test_pr_diff_text_flags_truncated(mocker: MockerFixture) -> None:
    """An oversized diff is flagged truncated instead of raising.

    Verified live against `micro-editor/micro#1392` (a 228,016-addition
    PR) on 2026-07-16: the REST diff endpoint refuses with HTTP 406 and
    a JSON body whose `errors[0].code` is `"too_large"`.

    When: the REST call fails with that exact refusal signal
    Then: `pr_diff_text` returns `("", True)` instead of raising
    """
    body: JsonObject = {
        "message": "Sorry, the diff exceeded the maximum number of lines (20000)",
        "errors": [{"resource": "PullRequest", "field": "diff", "code": "too_large"}],
        "documentation_url": "https://docs.github.com/rest/pulls/pulls#get-a-pull-request",
        "status": "406",
    }
    client = _client(
        mocker, request=mocker.Mock(side_effect=_request_failed(406, body))
    )

    diff_text, truncated = client.pr_diff_text("acme", "widget", IssueOrPRNumber(1392))

    assert diff_text == ""
    assert truncated is True


def test_graphql_rejects_a_query_that_forgot_rate_limit(
    mocker: MockerFixture,
) -> None:
    """A response with no `rateLimit` block names the missing selection.

    Metering is the whole reason `graphql` exists rather than callers
    using githubkit directly, so a query that forgot the block is a bug
    in this repo. Indexing into the response would report it as a bare
    `KeyError('rateLimit')`, which reads like GitHub returned something
    odd.

    When: the response carries no `rateLimit` block
    Then: `graphql` raises `ValueError` naming the required selection
    """
    client = _client(mocker, graphql=mocker.Mock(return_value={"count": 42}))

    with pytest.raises(ValueError, match="must select `rateLimit"):
        client.graphql("query { ... }", {}, _Payload)


def test_non_json_406_does_not_mask_the_request_failure(
    mocker: MockerFixture,
) -> None:
    """A 406 with an unparseable body surfaces as the fetch failure it is.

    The too-large check has to read the body to classify a 406, and it
    runs inside the handler for the very exception it is classifying. A
    proxy answering 406 with HTML would make that read raise, and the
    decode error would replace the transport failure on the way out.

    When: the REST call fails with a 406 whose body is not JSON
    Then: `pr_diff_text` raises `DiffFetchError` for that PR, chained
      from the original `RequestFailed` — not a JSON decode error
    """
    raw = httpx.Response(406, request=_REQUEST, text="<html>not acceptable</html>")
    client = _client(mocker, request=mocker.Mock(side_effect=_failed_response(raw)))

    with pytest.raises(DiffFetchError) as exc_info:
        client.pr_diff_text("acme", "widget", IssueOrPRNumber(7))

    assert exc_info.value.status_code == 406
    assert isinstance(exc_info.value.__cause__, RequestFailed)


def test_failed_diff_fetch_raises_domain_error(mocker: MockerFixture) -> None:
    """A non-refusal request failure is reraised as `DiffFetchError`.

    `sync_items` catches this to record a `fetch_failed` and carry on.
    Letting githubkit's own `RequestFailed` reach that caller would work
    identically and put the githubkit import outside this module, which
    is the one place it belongs.

    When: the REST diff call fails with a 404
    Then: `pr_diff_text` raises `DiffFetchError` carrying the PR number
      and the status
    """
    client = _client(
        mocker, request=mocker.Mock(side_effect=_request_failed(404, {"message": "no"}))
    )

    with pytest.raises(DiffFetchError) as exc_info:
        client.pr_diff_text("acme", "widget", IssueOrPRNumber(7))

    assert exc_info.value.number == 7
    assert exc_info.value.status_code == 404


def test_from_env_builds_client_with_narrowed_retry(mocker: MockerFixture) -> None:
    """`$GITHUB_TOKEN` is used, with githubkit's rate-limit retry declined.

    `auto_retry=RETRY_SERVER_ERROR` is load-bearing, not a default:
    githubkit's `RETRY_DEFAULT` would answer a rate limit by sleeping
    until the reset — up to an hour — which silently defeats the
    persist-cursor-and-exit design the sync driver is built around.
    Nothing pinned it before this.

    When: `$GITHUB_TOKEN` is set
    Then: `from_env` constructs the githubkit handle with that token and
      the 5xx-only retry chain, and never shells out to `gh auth token`
    """
    mocker.patch.dict("os.environ", {"GITHUB_TOKEN": "env-token"})
    gh = mocker.patch("ghmem.github.GitHub")
    run = mocker.patch("ghmem.github.subprocess.run")

    GitHubClient.from_env()

    gh.assert_called_once_with("env-token", auto_retry=RETRY_SERVER_ERROR)
    run.assert_not_called()


def test_from_env_falls_back_to_gh_auth_token(mocker: MockerFixture) -> None:
    """With no `$GITHUB_TOKEN`, the token comes from `gh auth token`.

    When: `$GITHUB_TOKEN` is unset and `gh auth token` succeeds
    Then: `from_env` builds the handle with the token `gh` printed,
      stripped of its trailing newline
    """
    mocker.patch.dict("os.environ", {}, clear=True)
    gh = mocker.patch("ghmem.github.GitHub")
    mocker.patch(
        "ghmem.github.subprocess.run",
        return_value=mocker.Mock(returncode=0, stdout="gh-token\n"),
    )

    GitHubClient.from_env()

    gh.assert_called_once_with("gh-token", auto_retry=RETRY_SERVER_ERROR)


@pytest.mark.parametrize(
    "result",
    [None, (1, ""), (0, "\n")],
    ids=["gh is not installed", "gh is not logged in", "gh printed nothing"],
)
def test_from_env_without_any_token_raises_auth_error(
    mocker: MockerFixture, result: tuple[int, str] | None
) -> None:
    """Every way of having no token raises `AuthError`, not something opaque.

    `_gh_auth_token` returns `None` three distinct ways, and each one
    has to reach the same message telling the operator what to do about
    it. An empty `stdout` is the easiest to get wrong: it is falsy
    already, so a check that only looked at `returncode` would hand an
    empty-string token straight to githubkit.

    `result` is what `subprocess.run` does: `None` for the missing
    binary, otherwise the `(returncode, stdout)` it came back with.

    When: `$GITHUB_TOKEN` is unset and `gh auth token` fails the way
      this case's id describes
    Then: `from_env` raises `AuthError` naming both token sources
    """
    mocker.patch.dict("os.environ", {}, clear=True)
    if result is None:
        mocker.patch("ghmem.github.subprocess.run", side_effect=FileNotFoundError)
    else:
        returncode, stdout = result
        mocker.patch(
            "ghmem.github.subprocess.run",
            return_value=mocker.Mock(returncode=returncode, stdout=stdout),
        )

    with pytest.raises(AuthError, match="GITHUB_TOKEN"):
        GitHubClient.from_env()

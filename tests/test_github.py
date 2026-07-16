"""Test the githubkit client wrapper: metering, conversion, diff fetch."""

from datetime import UTC, datetime

import httpx
import msgspec
import pytest
from githubkit.exception import RequestFailed
from githubkit.response import Response as GHResponse
from pytest_mock import MockerFixture

from ghmem.aliases import IssueOrPRNumber, JsonObject
from ghmem.exceptions import RateLimitError
from ghmem.github import GitHubClient


class _Payload(msgspec.Struct):
    """A minimal caller Struct standing in for a real query's response."""

    count: int


def _rate_limit(remaining: int) -> JsonObject:
    """Build a `rateLimit` block with the given remaining budget."""
    return {"cost": 1, "remaining": remaining, "resetAt": "2026-07-16T18:00:00Z"}


def _request_failed(status_code: int, body: JsonObject) -> RequestFailed:
    """Build a real `RequestFailed` carrying `status_code` and JSON `body`.

    No network involved: `httpx.Request`/`httpx.Response` are plain
    in-memory objects here, just like the ones githubkit's transport
    would have produced from a real refusal.
    """
    request = httpx.Request("GET", "https://api.github.com/repos/x/y/pulls/1")
    raw_response = httpx.Response(status_code, request=request, json=body)
    return RequestFailed(GHResponse(raw_response, object))


def test_graphql_converts_into_struct(mocker: MockerFixture) -> None:
    """A well-formed response is converted into the passed Struct.

    When: `graphql` is called with a Struct type and githubkit returns a
      `data` block matching it plus a healthy `rateLimit`
    Then: the returned value is a populated instance of that Struct
    """
    client = GitHubClient("tok")
    client._gh = mocker.Mock(
        graphql=mocker.Mock(return_value={"rateLimit": _rate_limit(4999), "count": 42})
    )

    result = client.graphql("query { ... }", {}, _Payload)

    assert result == _Payload(count=42)


def test_graphql_rejects_malformed_response(mocker: MockerFixture) -> None:
    """A wrong-typed field raises instead of returning junk.

    When: the `data` block's `count` field is a string where the Struct
      declares an int
    Then: `graphql` raises rather than returning a mistyped instance
    """
    client = GitHubClient("tok")
    client._gh = mocker.Mock(
        graphql=mocker.Mock(
            return_value={"rateLimit": _rate_limit(4999), "count": "not-an-int"}
        )
    )

    with pytest.raises(msgspec.ValidationError):
        client.graphql("query { ... }", {}, _Payload)


def test_rate_limit_exhaustion_raises(mocker: MockerFixture) -> None:
    """A remaining budget below the safety margin raises `RateLimitError`.

    When: the `rateLimit` block's `remaining` is below the safety margin
    Then: `graphql` raises `RateLimitError` carrying the parsed `resetAt`
    """
    client = GitHubClient("tok")
    client._gh = mocker.Mock(
        graphql=mocker.Mock(return_value={"rateLimit": _rate_limit(0), "count": 42})
    )

    with pytest.raises(RateLimitError) as exc_info:
        client.graphql("query { ... }", {}, _Payload)

    assert exc_info.value.reset_at == datetime(2026, 7, 16, 18, 0, tzinfo=UTC)


def test_pr_diff_text_sets_diff_accept_header(mocker: MockerFixture) -> None:
    """`pr_diff_text` requests the REST diff media type.

    When: `pr_diff_text` is called for a PR number
    Then: the underlying REST call carries the diff `Accept` header
    """
    client = GitHubClient("tok")
    request = mocker.Mock(return_value=mocker.Mock(text="diff --git a/x b/x\n"))
    client._gh = mocker.Mock(request=request)

    diff_text, truncated = client.pr_diff_text(IssueOrPRNumber(1))

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
    client = GitHubClient("tok")
    body: JsonObject = {
        "message": "Sorry, the diff exceeded the maximum number of lines (20000)",
        "errors": [{"resource": "PullRequest", "field": "diff", "code": "too_large"}],
        "documentation_url": "https://docs.github.com/rest/pulls/pulls#get-a-pull-request",
        "status": "406",
    }
    client._gh = mocker.Mock(
        request=mocker.Mock(side_effect=_request_failed(406, body))
    )

    diff_text, truncated = client.pr_diff_text(IssueOrPRNumber(1392))

    assert diff_text == ""
    assert truncated is True


def test_from_env_prefers_env_token(mocker: MockerFixture) -> None:
    """`$GITHUB_TOKEN` is used without falling back to `gh auth token`.

    When: `$GITHUB_TOKEN` is set
    Then: `from_env` builds a client authenticated with it, and never
      shells out to `gh auth token`
    """
    mocker.patch.dict("os.environ", {"GITHUB_TOKEN": "env-token"})
    run = mocker.patch("ghmem.github.subprocess.run")

    client = GitHubClient.from_env()

    assert client._gh.auth.token == "env-token"  # noqa: S105 -- not a real secret
    run.assert_not_called()

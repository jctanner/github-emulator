"""GraphQL ``closeIssue`` and the reason an issue was closed.

On GitHub ``CloseIssueInput.stateReason`` is an ``IssueClosedStateReason``
(``COMPLETED``, ``NOT_PLANNED``, ``DUPLICATE``) and defaults to
``COMPLETED``. REST reports the reason in lowercase (``"not_planned"``);
GraphQL's ``Issue.stateReason`` is the ``IssueStateReason`` enum.
"""

import pytest
import pytest_asyncio

from tests.conftest import API, auth_headers

CLOSE = """
mutation($input: CloseIssueInput!) {
  closeIssue(input: $input) { issue { state stateReason } }
}
"""


async def _gql(client, token, query, variables=None):
    resp = await client.post(
        "/graphql", json={"query": query, "variables": variables or {}},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _issue(client, token, title="An issue"):
    resp = await client.post(
        f"{API}/repos/testuser/reasons/issues", json={"title": title}, headers=auth_headers(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def repo(client, test_user, test_token):
    resp = await client.post(
        f"{API}/user/repos", json={"name": "reasons"}, headers=auth_headers(test_token)
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason, rest",
    [("NOT_PLANNED", "not_planned"), ("DUPLICATE", "duplicate"), ("COMPLETED", "completed"), (None, "completed")],
)
async def test_close_issue_records_the_state_reason(client, test_token, repo, reason, rest):
    issue = await _issue(client, test_token)
    variables = {"input": {"issueId": issue["node_id"]}}
    if reason:
        variables["input"]["stateReason"] = reason

    result = await _gql(client, test_token, CLOSE, variables)

    assert "errors" not in result, result
    assert result["data"]["closeIssue"]["issue"] == {
        "state": "CLOSED", "stateReason": reason or "COMPLETED",
    }
    fetched = await client.get(
        f"{API}/repos/testuser/reasons/issues/{issue['number']}", headers=auth_headers(test_token)
    )
    assert fetched.json()["state_reason"] == rest

    query = """query($n: Int!) { repository(owner: "testuser", name: "reasons") {
      issue(number: $n) { stateReason } } }"""
    queried = await _gql(client, test_token, query, {"n": issue["number"]})
    assert queried["data"]["repository"]["issue"]["stateReason"] == (reason or "COMPLETED")


@pytest.mark.asyncio
async def test_close_issue_state_reason_is_an_enum(client, test_token, repo):
    issue = await _issue(client, test_token)
    literal = await _gql(
        client, test_token,
        'mutation($id: ID!) { closeIssue(input: {issueId: $id, stateReason: NOT_PLANNED}) '
        "{ issue { stateReason } } }",
        {"id": issue["node_id"]},
    )
    assert literal["data"]["closeIssue"]["issue"]["stateReason"] == "NOT_PLANNED"

    other = await _issue(client, test_token, "Another")
    bad = await _gql(
        client, test_token, CLOSE,
        {"input": {"issueId": other["node_id"], "stateReason": "WONTFIX"}},
    )
    assert bad.get("errors"), bad


@pytest.mark.asyncio
async def test_close_issue_as_a_duplicate_of_another(client, test_token, repo):
    original = await _issue(client, test_token, "Original")
    copy = await _issue(client, test_token, "Copy")

    result = await _gql(
        client, test_token, CLOSE,
        {"input": {"issueId": copy["node_id"], "duplicateIssueId": original["node_id"]}},
    )

    assert "errors" not in result, result
    assert result["data"]["closeIssue"]["issue"]["stateReason"] == "DUPLICATE"


@pytest.mark.asyncio
async def test_a_duplicate_the_caller_cannot_see_is_not_found(client, test_token, admin_token, repo):
    """An issue in a repository hidden from the caller answers as missing, so
    ``duplicateIssueId`` cannot reveal which hidden issues exist."""
    import base64

    resp = await client.post(
        f"{API}/user/repos", json={"name": "hidden-dups", "private": True}, headers=auth_headers(admin_token)
    )
    assert resp.status_code == 201, resp.text
    hidden = await client.post(
        f"{API}/repos/admin/hidden-dups/issues", json={"title": "Hidden"}, headers=auth_headers(admin_token)
    )
    assert hidden.status_code == 201, hidden.text
    copy = await _issue(client, test_token, "Copy")
    original = await _issue(client, test_token, "Original")
    original_db_id = base64.b64decode(original["node_id"]).decode().split(":")[1]

    for duplicate_id in (
        hidden.json()["node_id"],
        # Only an Issue id names an issue, not another type's id with the same number.
        base64.b64encode(f"PullRequest:{original_db_id}".encode()).decode(),
    ):
        result = await _gql(
            client, test_token, CLOSE, {"input": {"issueId": copy["node_id"], "duplicateIssueId": duplicate_id}}
        )
        assert result.get("errors"), result
        assert "not found" in result["errors"][0]["message"]

    still_open = await client.get(f"{API}/repos/testuser/reasons/issues/{copy['number']}", headers=auth_headers(test_token))
    assert still_open.json()["state"] == "open"

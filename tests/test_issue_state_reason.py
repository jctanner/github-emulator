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

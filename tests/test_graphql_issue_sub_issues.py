"""``Issue.subIssues`` and ``Issue.parent`` validate, empty and null.

GitHub's Issue has sub-issues. Clients ask ``subIssues(first: 1) { totalCount }``
before acting on an issue, and the emulator refused the query with "Cannot
query field 'subIssues' on type 'Issue'". Sub-issues are not modelled, so
the connection is always empty and ``parent`` is always null.
"""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"
pytestmark = pytest.mark.asyncio


async def test_sub_issues_is_an_empty_connection(client, test_token):
    await client.post(f"{API}/user/repos", json={"name": "gql-sub"}, headers=auth_headers(test_token))
    created = await client.post(f"{API}/repos/testuser/gql-sub/issues", json={"title": "t"}, headers=auth_headers(test_token))
    assert created.status_code == 201
    resp = await client.post(
        "/graphql",
        json={
            "query": """query($owner: String!, $repo: String!, $number: Int!) {
                repository(owner: $owner, name: $repo) {
                    issue(number: $number) {
                        subIssues(first: 1) { totalCount nodes { number } }
                        parent { number }
                    }
                }
            }""",
            "variables": {"owner": "testuser", "repo": "gql-sub", "number": 1},
        },
        headers=auth_headers(test_token),
    )
    body = resp.json()
    assert "errors" not in body, body
    issue = body["data"]["repository"]["issue"]
    assert issue["subIssues"] == {"totalCount": 0, "nodes": []}
    assert issue["parent"] is None

"""GraphQL holds integration tokens to their own grant on issue and PR writes.

REST refuses an installation token a write its permissions do not cover
("Resource not accessible by integration"), keyed off the repository in the
URL. ``/api/graphql`` has no repository in its URL, so its issue and pull
request mutations accepted any covered token regardless of permissions
(docs/bugs/open/graphql-write-authorization.md). They now ask the token's
grant: ``issues`` for issues, ``pull_requests`` for pull requests, and either
for a pull request's backing issue (its labels, comments, and state).
"""

import base64
import time

import pytest
from jose import jwt

from tests.conftest import API, auth_headers

pytestmark = pytest.mark.asyncio


def _node(kind: str, db_id: int) -> str:
    return base64.b64encode(f"{kind}:{db_id}".encode()).decode()


async def _gql(client, query, token, variables=None):
    response = await client.post(
        "/api/graphql", json={"query": query, "variables": variables or {}}, headers=auth_headers(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _fixture(client, token):
    """A public repository with an issue and a pull request; returns node IDs."""
    headers = auth_headers(token)
    created = await client.post(f"{API}/user/repos", json={"name": "shown", "auto_init": True}, headers=headers)
    assert created.status_code == 201, created.text
    issue = await client.post(f"{API}/repos/testuser/shown/issues", json={"title": "an issue"}, headers=headers)
    pull = await client.post(
        f"{API}/repos/testuser/shown/pulls", json={"title": "a pull", "head": "main", "base": "main"}, headers=headers
    )
    assert issue.status_code == 201 and pull.status_code == 201, (issue.text, pull.text)
    backing = await client.get(f"{API}/repos/testuser/shown/issues/{pull.json()['number']}", headers=headers)
    return {
        "repo": _node("Repository", created.json()["id"]),
        "issue": _node("Issue", issue.json()["id"]),
        "pr": _node("PullRequest", pull.json()["id"]),
        "pr_issue": _node("Issue", backing.json()["id"]),
    }


async def _installation_token(client, admin_token, app_id, permissions, repositories=("testuser/shown",)):
    created = await client.post(
        f"{API}/admin/apps", headers=auth_headers(admin_token),
        json={"app_id": app_id, "name": f"w-{app_id}", "slug": f"w-{app_id}", "permissions": permissions},
    )
    assert created.status_code == 201, created.text
    installation = await client.post(
        f"{API}/admin/apps/{app_id}/installations", headers=auth_headers(admin_token),
        json={"account_login": "testuser", "account_type": "User", "repositories": list(repositories),
              "permissions": permissions},
    )
    assert installation.status_code == 201, installation.text
    now = int(time.time())
    app_jwt = jwt.encode({"iss": app_id, "iat": now - 10, "exp": now + 60}, created.json()["private_key"], algorithm="RS256")
    minted = await client.post(
        f"{API}/app/installations/{installation.json()['id']}/access_tokens",
        headers={"Authorization": f"Bearer {app_jwt}"}, json={},
    )
    assert minted.status_code == 201, minted.text
    return minted.json()["token"]


MUTATIONS = {
    "createIssue": ('mutation($id: ID!) { createIssue(input: {repositoryId: $id, title: "x"}) { issue { title } } }', "repo"),
    "updateIssue": ('mutation($id: ID!) { updateIssue(input: {id: $id, title: "edited"}) { issue { title } } }', "issue"),
    "closeIssue": ("mutation($id: ID!) { closeIssue(input: {issueId: $id}) { issue { state } } }", "issue"),
    "commentOnIssue": ('mutation($id: ID!) { addComment(input: {subjectId: $id, body: "hi"}) { subject { id } } }', "issue"),
    "reactToIssue": ('mutation($id: ID!) { addReaction(input: {subjectId: $id, content: "heart"}) { reaction { content } } }', "issue"),
    "commentOnPullRequest": ('mutation($id: ID!) { addComment(input: {subjectId: $id, body: "hi"}) { subject { id } } }', "pr_issue"),
    "createPullRequest": (
        'mutation($id: ID!) { createPullRequest(input: {repositoryId: $id, title: "p", headRefName: "main",'
        ' baseRefName: "main"}) { pullRequest { title } } }', "repo",
    ),
    "updatePullRequest": ('mutation($id: ID!) { updatePullRequest(input: {pullRequestId: $id, title: "e"}) { pullRequest { title } } }', "pr"),
    "closePullRequest": ("mutation($id: ID!) { closePullRequest(input: {pullRequestId: $id}) { pullRequest { state } } }", "pr"),
    "reopenPullRequest": ("mutation($id: ID!) { reopenPullRequest(input: {pullRequestId: $id}) { pullRequest { state } } }", "pr"),
}


def _outcome(response) -> str:
    if not response.get("errors"):
        return "ok"
    return response["errors"][0].get("type") or response["errors"][0]["message"]


@pytest.mark.parametrize(
    "permissions, allowed",
    [
        ({"issues": "read", "pull_requests": "read"}, set()),
        ({"issues": "write"}, {"createIssue", "updateIssue", "closeIssue", "commentOnIssue", "reactToIssue", "commentOnPullRequest"}),
        ({"pull_requests": "write"}, {"commentOnPullRequest", "createPullRequest", "updatePullRequest", "closePullRequest", "reopenPullRequest"}),
    ],
    ids=["read-only", "issues-write", "pull-requests-write"],
)
async def test_installation_token_held_to_its_grant(client, test_user, test_token, admin_token, permissions, allowed):
    ids = await _fixture(client, test_token)
    token = await _installation_token(client, admin_token, "5001", permissions)
    for name, (query, target) in MUTATIONS.items():
        outcome = _outcome(await _gql(client, query, token, {"id": ids[target]}))
        expected = "ok" if name in allowed else "FORBIDDEN"
        assert outcome == expected, (name, outcome)


async def test_refused_write_changes_nothing(client, test_user, test_token, admin_token):
    ids = await _fixture(client, test_token)
    token = await _installation_token(client, admin_token, "5002", {"issues": "read"})
    query, _ = MUTATIONS["updateIssue"]
    response = await _gql(client, query, token, {"id": ids["issue"]})
    assert response["errors"][0]["type"] == "FORBIDDEN"
    assert response["errors"][0]["message"] == "Resource not accessible by integration"
    issue = await client.get(f"{API}/repos/testuser/shown/issues/1", headers=auth_headers(test_token))
    assert issue.json()["title"] == "an issue"


async def test_token_for_another_repository_finds_nothing(client, test_user, test_token, admin_token):
    ids = await _fixture(client, test_token)
    other = await client.post(f"{API}/user/repos", json={"name": "other"}, headers=auth_headers(test_token))
    assert other.status_code == 201, other.text
    token = await _installation_token(client, admin_token, "5003", {"issues": "write"}, ("testuser/other",))
    query, _ = MUTATIONS["closeIssue"]
    response = await _gql(client, query, token, {"id": ids["issue"]})
    assert response["errors"][0]["message"].endswith("not found"), response
    assert response["data"] is None


async def test_user_credentials_are_unchanged(client, test_user, test_token):
    ids = await _fixture(client, test_token)
    for name in ("updateIssue", "updatePullRequest", "closePullRequest", "reopenPullRequest"):
        query, target = MUTATIONS[name]
        assert _outcome(await _gql(client, query, test_token, {"id": ids[target]})) == "ok", name


async def test_merging_needs_pull_requests_write(client, test_user, test_token, admin_token):
    ids = await _fixture(client, test_token)
    token = await _installation_token(client, admin_token, "5004", {"issues": "write", "contents": "write"})
    for field in ("mergePullRequest", "enablePullRequestAutoMerge", "disablePullRequestAutoMerge"):
        query = f"mutation($id: ID!) {{ {field}(input: {{pullRequestId: $id}}) {{ clientMutationId }} }}"
        assert _outcome(await _gql(client, query, token, {"id": ids["pr"]})) == "FORBIDDEN", field

"""GraphQL inputs gh sends for pull request comments and new issues.

``gh pr close N --comment ...`` and ``gh pr comment`` call ``addComment``
with the pull request's node id; GitHub accepts an Issue or a PullRequest
as the subject. ``gh issue create`` sends ``projectIds`` in
``CreateIssueInput`` even when empty; GitHub's input has that field and
several others the emulator does not model.
"""

import pytest

from tests.conftest import auth_headers
from tests.test_pulls_api import _create_real_pr_with_diff

API = "/api/v3"
pytestmark = pytest.mark.asyncio


async def _gql(client, token, query, variables=None):
    resp = await client.post("/graphql", json={"query": query, "variables": variables or {}}, headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_add_comment_accepts_a_pull_request_subject(client, db_session, test_token):
    repo = await _create_real_pr_with_diff(client, db_session, test_token, "gql-pr-comment")
    pr = await _gql(client, test_token, 'query($r: String!) { repository(owner: "testuser", name: $r) { pullRequest(number: 1) { id } } }', {"r": repo})
    pr_id = pr["data"]["repository"]["pullRequest"]["id"]

    added = await _gql(
        client, test_token,
        "mutation($id: ID!) { addComment(input: {subjectId: $id, body: \"closing\"}) { commentEdge { node { body } } } }",
        {"id": pr_id},
    )
    assert "errors" not in added, added
    assert added["data"]["addComment"]["commentEdge"]["node"]["body"] == "closing"

    listed = await client.get(f"{API}/repos/testuser/{repo}/issues/1/comments", headers=auth_headers(test_token))
    assert [c["body"] for c in listed.json()] == ["closing"]


async def test_add_reaction_accepts_a_pull_request_subject(client, db_session, test_token):
    repo = await _create_real_pr_with_diff(client, db_session, test_token, "gql-pr-react")
    pr = await _gql(client, test_token, 'query($r: String!) { repository(owner: "testuser", name: $r) { pullRequest(number: 1) { id } } }', {"r": repo})
    reacted = await _gql(
        client, test_token,
        "mutation($input: AddReactionInput!) { addReaction(input: $input) { reaction { content } } }",
        {"input": {"subjectId": pr["data"]["repository"]["pullRequest"]["id"], "content": "THUMBS_UP"}},
    )
    assert "errors" not in reacted, reacted


async def test_create_issue_accepts_the_fields_gh_sends(client, test_token):
    await client.post(f"{API}/user/repos", json={"name": "gql-new-issue"}, headers=auth_headers(test_token))
    repo = await _gql(client, test_token, '{ repository(owner: "testuser", name: "gql-new-issue") { id } }')
    created = await _gql(
        client, test_token,
        """mutation($input: CreateIssueInput!) { createIssue(input: $input) { issue { number title } } }""",
        {"input": {
            "repositoryId": repo["data"]["repository"]["id"], "title": "proposal", "body": "b",
            "projectIds": [], "projectV2Ids": [], "issueTemplate": None, "issueTypeId": None, "parentIssueId": None,
        }},
    )
    assert "errors" not in created, created
    assert created["data"]["createIssue"]["issue"]["title"] == "proposal"

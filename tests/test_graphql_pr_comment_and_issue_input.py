"""GraphQL inputs gh sends for pull request comments and new issues.

``gh pr close N --comment ...`` and ``gh pr comment`` call ``addComment``
with the pull request's node id; GitHub accepts an Issue or a PullRequest
as the subject. ``gh issue create`` sends ``projectIds`` in
``CreateIssueInput`` even when empty; GitHub's input has that field and
several others the emulator does not model.
"""

import pytest

from tests.conftest import auth_headers
from tests.test_private_repository_access import outsider_token  # noqa: F401
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


async def test_add_comment_returns_the_pull_request_it_was_given(client, db_session, test_token):
    repo = await _create_real_pr_with_diff(client, db_session, test_token, "gql-pr-subject")
    pr = await _gql(client, test_token, 'query($r: String!) { repository(owner: "testuser", name: $r) { pullRequest(number: 1) { id } } }', {"r": repo})
    pr_id = pr["data"]["repository"]["pullRequest"]["id"]

    added = await _gql(
        client, test_token,
        'mutation($id: ID!) { addComment(input: {subjectId: $id, body: "hi"}) {'
        " subject { id __typename ... on PullRequest { number } } } }",
        {"id": pr_id},
    )

    assert "errors" not in added, added
    assert added["data"]["addComment"]["subject"] == {"id": pr_id, "__typename": "PullRequest", "number": 1}



async def test_a_missing_and_a_hidden_subject_fail_alike(client, db_session, test_token, outsider_token):  # noqa: F811
    """Both errors name the id the caller gave, so neither tells a hidden pull
    request from one that does not exist."""
    import base64

    repo = await _create_real_pr_with_diff(client, db_session, test_token, "gql-hidden-subject")
    made_private = await client.patch(f"{API}/repos/testuser/{repo}", json={"private": True}, headers=auth_headers(test_token))
    assert made_private.status_code == 200, made_private.text
    pr = await _gql(client, test_token, 'query($r: String!) { repository(owner: "testuser", name: $r) { pullRequest(number: 1) { id } } }', {"r": repo})
    hidden_id = pr["data"]["repository"]["pullRequest"]["id"]
    missing_id = base64.b64encode(b"PullRequest:999999").decode()

    for mutation in (
        'mutation($id: ID!) { addComment(input: {subjectId: $id, body: "x"}) { subject { id } } }',
        "mutation($id: ID!) { addReaction(input: {subjectId: $id, content: \"THUMBS_UP\"}) { subjectId } }",
    ):
        errors = []
        for subject_id in (hidden_id, missing_id):
            result = await _gql(client, outsider_token, mutation, {"id": subject_id})
            errors.append(result["errors"][0]["message"].replace(subject_id, "<id>"))
        assert errors[0] == errors[1] == "Subject with id <id> not found"

"""Tests for GitHub-compatible pull-request closing keywords."""

import pytest
from sqlalchemy import select

from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.services import closing_issue_service
from app.services.closing_issue_service import (
    parse_closing_references,
    resolve_closing_issues,
)
from tests.conftest import API, auth_headers


async def _graphql(client, token, query, variables):
    response = await client.post(
        "/graphql",
        json={"query": query, "variables": variables},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    payload = response.json()
    assert "errors" not in payload, payload.get("errors")
    return payload["data"]


def test_parse_all_supported_closing_keyword_shapes():
    assert parse_closing_references(
        "Close #1, closes: #2; CLOSED #3. Fix #4, fixes #5, fixed #6. "
        "Resolve #7, resolves #8, RESOLVED: Octo-Org/Repo.Name#9."
    ) == [
        (None, None, 1),
        (None, None, 2),
        (None, None, 3),
        (None, None, 4),
        (None, None, 5),
        (None, None, 6),
        (None, None, 7),
        (None, None, 8),
        ("Octo-Org", "Repo.Name", 9),
    ]


@pytest.mark.asyncio
async def test_commit_message_references_apply_only_during_merge_resolution(
    client, db_session, test_token, tmp_path, monkeypatch
):
    headers = auth_headers(test_token)
    await client.post(
        f"{API}/user/repos",
        json={"name": "commit-closing-links"},
        headers=headers,
    )
    await client.post(
        f"{API}/repos/testuser/commit-closing-links/issues",
        json={"title": "Closed by a commit"},
        headers=headers,
    )
    await client.post(
        f"{API}/repos/testuser/commit-closing-links/pulls",
        json={"title": "Commit-linked work", "head": "feature", "base": "main"},
        headers=headers,
    )
    repository = (
        await db_session.execute(
            select(Repository).where(
                Repository.full_name == "testuser/commit-closing-links"
            )
        )
    ).scalar_one()
    pull_request = (
        await db_session.execute(
            select(PullRequest)
            .join(Issue, PullRequest.issue_id == Issue.id)
            .where(PullRequest.repo_id == repository.id, Issue.number == 2)
        )
    ).scalar_one()
    repository.disk_path = str(tmp_path)
    await db_session.commit()

    async def fake_get_log(*_args, **_kwargs):
        return [{"message": "Implement request", "body": "Fixed #1"}]

    monkeypatch.setattr(closing_issue_service, "get_log", fake_get_log)
    assert await resolve_closing_issues(
        db_session,
        pull_request,
        repository,
        include_commit_messages=False,
    ) == []
    linked = await resolve_closing_issues(
        db_session,
        pull_request,
        repository,
        include_commit_messages=True,
    )
    assert [issue.number for issue in linked] == [1]


@pytest.mark.asyncio
async def test_auto_merge_closes_body_link_and_exposes_graphql_reference(
    client, test_token
):
    headers = auth_headers(test_token)
    repo = await client.post(
        f"{API}/user/repos",
        json={"name": "closing-links"},
        headers=headers,
    )
    assert repo.status_code == 201
    issue = await client.post(
        f"{API}/repos/testuser/closing-links/issues",
        json={"title": "Linked work"},
        headers=headers,
    )
    assert issue.status_code == 201
    pull = await client.post(
        f"{API}/repos/testuser/closing-links/pulls",
        json={
            "title": "Implement linked work",
            "body": "This implements the request.\n\nCloses: #1",
            "head": "feature",
            "base": "main",
        },
        headers=headers,
    )
    assert pull.status_code == 201

    linked = await _graphql(
        client,
        test_token,
        """
        query {
          repository(owner: "testuser", name: "closing-links") {
            pullRequest(number: 2) {
              closingIssuesReferences(first: 10) {
                nodes { number title state }
              }
            }
          }
        }
        """,
        {},
    )
    assert linked["repository"]["pullRequest"]["closingIssuesReferences"]["nodes"] == [
        {"number": 1, "title": "Linked work", "state": "OPEN"}
    ]

    await _graphql(
        client,
        test_token,
        """
        mutation($input: EnablePullRequestAutoMergeInput!) {
          enablePullRequestAutoMerge(input: $input) {
            pullRequest { merged }
          }
        }
        """,
        {
            "input": {
                "pullRequestId": pull.json()["node_id"],
                "mergeMethod": "SQUASH",
            }
        },
    )
    closed = await client.get(
        f"{API}/repos/testuser/closing-links/issues/1",
        headers=headers,
    )
    assert closed.json()["state"] == "closed"
    assert closed.json()["state_reason"] == "completed"
    assert closed.json()["closed_by"]["login"] == "testuser"


@pytest.mark.asyncio
async def test_non_default_base_does_not_link_or_close_issue(client, test_token):
    headers = auth_headers(test_token)
    await client.post(
        f"{API}/user/repos",
        json={"name": "non-default-closing-links"},
        headers=headers,
    )
    await client.post(
        f"{API}/repos/testuser/non-default-closing-links/issues",
        json={"title": "Remain open"},
        headers=headers,
    )
    pull = await client.post(
        f"{API}/repos/testuser/non-default-closing-links/pulls",
        json={
            "title": "Intermediate branch",
            "body": "Fixes #1",
            "head": "feature",
            "base": "release",
        },
        headers=headers,
    )
    await _graphql(
        client,
        test_token,
        """
        mutation($input: EnablePullRequestAutoMergeInput!) {
          enablePullRequestAutoMerge(input: $input) { pullRequest { merged } }
        }
        """,
        {"input": {"pullRequestId": pull.json()["node_id"], "mergeMethod": "SQUASH"}},
    )
    issue = await client.get(
        f"{API}/repos/testuser/non-default-closing-links/issues/1",
        headers=headers,
    )
    assert issue.json()["state"] == "open"


@pytest.mark.asyncio
async def test_cross_repository_closing_reference(client, test_token):
    headers = auth_headers(test_token)
    for name in ("closing-source", "closing-target"):
        await client.post(
            f"{API}/user/repos",
            json={"name": name},
            headers=headers,
        )
    await client.post(
        f"{API}/repos/testuser/closing-target/issues",
        json={"title": "Cross-repository work"},
        headers=headers,
    )
    pull = await client.post(
        f"{API}/repos/testuser/closing-source/pulls",
        json={
            "title": "Implement external issue",
            "body": "Resolves testuser/closing-target#1",
            "head": "feature",
            "base": "main",
        },
        headers=headers,
    )
    await _graphql(
        client,
        test_token,
        """
        mutation($input: EnablePullRequestAutoMergeInput!) {
          enablePullRequestAutoMerge(input: $input) { pullRequest { merged } }
        }
        """,
        {"input": {"pullRequestId": pull.json()["node_id"], "mergeMethod": "SQUASH"}},
    )
    issue = await client.get(
        f"{API}/repos/testuser/closing-target/issues/1",
        headers=headers,
    )
    assert issue.json()["state"] == "closed"

"""Tests for the Pull Requests REST API endpoints."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.fixture
async def repo_with_branch(client, test_user, test_token):
    """Create a repo for PR tests."""
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "pr-repo"},
        headers=auth_headers(test_token),
    )
    return resp.json()


@pytest.mark.asyncio
async def test_create_pull_request(client, test_user, test_token, repo_with_branch):
    """POST /repos/{owner}/{repo}/pulls creates a PR."""
    resp = await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={
            "title": "Add feature",
            "body": "This adds a new feature",
            "head": "feature-branch",
            "base": "main",
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Add feature"
    assert data["body"] == "This adds a new feature"
    assert data["state"] == "open"
    assert data["number"] == 1
    assert data["draft"] is False
    assert data["merged"] is False
    assert data["head"]["ref"] == "feature-branch"
    assert data["base"]["ref"] == "main"
    assert data["user"]["login"] == "testuser"


@pytest.mark.asyncio
async def test_create_pr_requires_auth(client, test_user, test_token, repo_with_branch):
    """POST /repos/{owner}/{repo}/pulls without auth returns 401."""
    resp = await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={"title": "Test", "head": "feature", "base": "main"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_pr_requires_fields(client, test_user, test_token, repo_with_branch):
    """POST /repos/{owner}/{repo}/pulls without required fields returns 422."""
    resp = await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={"title": "Test"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_pull_request(client, test_user, test_token, repo_with_branch):
    """GET /repos/{owner}/{repo}/pulls/{number} returns the PR."""
    await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={"title": "Get test", "head": "feature", "base": "main"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/repos/testuser/pr-repo/pulls/1")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Get test"


@pytest.mark.asyncio
async def test_get_nonexistent_pull(client, test_user, test_token, repo_with_branch):
    """GET /repos/{owner}/{repo}/pulls/{number} returns 404 for missing PR."""
    resp = await client.get(f"{API}/repos/testuser/pr-repo/pulls/999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_pull_request(client, test_user, test_token, repo_with_branch):
    """PATCH /repos/{owner}/{repo}/pulls/{number} updates the PR."""
    await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={"title": "Original", "head": "feature", "base": "main"},
        headers=auth_headers(test_token),
    )
    resp = await client.patch(
        f"{API}/repos/testuser/pr-repo/pulls/1",
        json={"title": "Updated title", "body": "New body"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Updated title"
    assert data["body"] == "New body"


@pytest.mark.asyncio
async def test_close_pull_request(client, test_user, test_token, repo_with_branch):
    """PATCH to close a PR sets state to closed."""
    await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={"title": "To close", "head": "feature", "base": "main"},
        headers=auth_headers(test_token),
    )
    resp = await client.patch(
        f"{API}/repos/testuser/pr-repo/pulls/1",
        json={"state": "closed"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "closed"


@pytest.mark.asyncio
async def test_merge_pull_request(client, test_user, test_token, repo_with_branch):
    """PUT /repos/{owner}/{repo}/pulls/{number}/merge merges the PR."""
    await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={"title": "To merge", "head": "feature", "base": "main"},
        headers=auth_headers(test_token),
    )
    resp = await client.put(
        f"{API}/repos/testuser/pr-repo/pulls/1/merge",
        json={},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["merged"] is True

    # Verify the PR is now closed and merged
    pr = await client.get(f"{API}/repos/testuser/pr-repo/pulls/1")
    pr_data = pr.json()
    assert pr_data["state"] == "closed"
    assert pr_data["merged"] is True
    assert pr_data["merged_at"] is not None


@pytest.mark.asyncio
async def test_merge_already_merged(client, test_user, test_token, repo_with_branch):
    """Merging an already-merged PR returns 405."""
    await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={"title": "Double merge", "head": "feature", "base": "main"},
        headers=auth_headers(test_token),
    )
    await client.put(
        f"{API}/repos/testuser/pr-repo/pulls/1/merge",
        json={},
        headers=auth_headers(test_token),
    )
    resp = await client.put(
        f"{API}/repos/testuser/pr-repo/pulls/1/merge",
        json={},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 405


@pytest.mark.asyncio
async def test_list_pulls(client, test_user, test_token, repo_with_branch):
    """GET /repos/{owner}/{repo}/pulls lists PRs."""
    for i in range(3):
        await client.post(
            f"{API}/repos/testuser/pr-repo/pulls",
            json={"title": f"PR {i+1}", "head": f"branch-{i}", "base": "main"},
            headers=auth_headers(test_token),
        )
    resp = await client.get(f"{API}/repos/testuser/pr-repo/pulls")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3


@pytest.mark.asyncio
async def test_list_pulls_filter_state(client, test_user, test_token, repo_with_branch):
    """List PRs can filter by state."""
    await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={"title": "Open PR", "head": "branch-a", "base": "main"},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={"title": "Closed PR", "head": "branch-b", "base": "main"},
        headers=auth_headers(test_token),
    )
    await client.patch(
        f"{API}/repos/testuser/pr-repo/pulls/2",
        json={"state": "closed"},
        headers=auth_headers(test_token),
    )

    resp = await client.get(f"{API}/repos/testuser/pr-repo/pulls?state=open")
    assert len(resp.json()) == 1

    resp = await client.get(f"{API}/repos/testuser/pr-repo/pulls?state=closed")
    assert len(resp.json()) == 1

    resp = await client.get(f"{API}/repos/testuser/pr-repo/pulls?state=all")
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_pr_shares_issue_numbering(client, test_user, test_token, repo_with_branch):
    """PRs and issues share the same numbering sequence."""
    # Create an issue first
    await client.post(
        f"{API}/repos/testuser/pr-repo/issues",
        json={"title": "Issue 1"},
        headers=auth_headers(test_token),
    )
    # Create a PR — should get number 2
    resp = await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={"title": "PR 1", "head": "feature", "base": "main"},
        headers=auth_headers(test_token),
    )
    assert resp.json()["number"] == 2


@pytest.mark.asyncio
async def test_pr_list_commits(client, test_user, test_token, repo_with_branch):
    """GET /repos/{owner}/{repo}/pulls/{number}/commits returns commits."""
    await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={"title": "Commit test", "head": "feature", "base": "main"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/repos/testuser/pr-repo/pulls/1/commits")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_pr_list_files(client, test_user, test_token, repo_with_branch):
    """GET /repos/{owner}/{repo}/pulls/{number}/files returns files list."""
    await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={"title": "Files test", "head": "feature", "base": "main"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/repos/testuser/pr-repo/pulls/1/files")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_create_draft_pr(client, test_user, test_token, repo_with_branch):
    """Creating a draft PR sets draft=True."""
    resp = await client.post(
        f"{API}/repos/testuser/pr-repo/pulls",
        json={
            "title": "Draft PR",
            "head": "feature",
            "base": "main",
            "draft": True,
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    assert resp.json()["draft"] is True

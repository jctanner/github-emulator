"""Tests for the Issues REST API endpoints."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.fixture
async def repo_with_issues(client, test_user, test_token):
    """Create a repo for issue tests."""
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "issue-repo"},
        headers=auth_headers(test_token),
    )
    return resp.json()


@pytest.mark.asyncio
async def test_create_issue(client, test_user, test_token, repo_with_issues):
    """POST /repos/{owner}/{repo}/issues creates an issue."""
    resp = await client.post(
        f"{API}/repos/testuser/issue-repo/issues",
        json={"title": "Bug report", "body": "Something is broken"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Bug report"
    assert data["body"] == "Something is broken"
    assert data["state"] == "open"
    assert data["number"] == 1
    assert data["user"]["login"] == "testuser"


@pytest.mark.asyncio
async def test_issue_numbering(client, test_user, test_token, repo_with_issues):
    """Issues are numbered sequentially."""
    resp1 = await client.post(
        f"{API}/repos/testuser/issue-repo/issues",
        json={"title": "First"},
        headers=auth_headers(test_token),
    )
    resp2 = await client.post(
        f"{API}/repos/testuser/issue-repo/issues",
        json={"title": "Second"},
        headers=auth_headers(test_token),
    )
    assert resp1.json()["number"] == 1
    assert resp2.json()["number"] == 2


@pytest.mark.asyncio
async def test_get_issue(client, test_user, test_token, repo_with_issues):
    """GET /repos/{owner}/{repo}/issues/{number} returns the issue."""
    await client.post(
        f"{API}/repos/testuser/issue-repo/issues",
        json={"title": "Get test"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/repos/testuser/issue-repo/issues/1")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Get test"


@pytest.mark.asyncio
async def test_update_issue(client, test_user, test_token, repo_with_issues):
    """PATCH /repos/{owner}/{repo}/issues/{number} updates the issue."""
    await client.post(
        f"{API}/repos/testuser/issue-repo/issues",
        json={"title": "Original"},
        headers=auth_headers(test_token),
    )
    resp = await client.patch(
        f"{API}/repos/testuser/issue-repo/issues/1",
        json={"title": "Updated", "state": "closed"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Updated"
    assert data["state"] == "closed"


@pytest.mark.asyncio
async def test_list_issues(client, test_user, test_token, repo_with_issues):
    """GET /repos/{owner}/{repo}/issues lists issues."""
    for i in range(3):
        await client.post(
            f"{API}/repos/testuser/issue-repo/issues",
            json={"title": f"Issue {i+1}"},
            headers=auth_headers(test_token),
        )
    resp = await client.get(f"{API}/repos/testuser/issue-repo/issues")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3


@pytest.mark.asyncio
async def test_list_issues_filter_state(client, test_user, test_token, repo_with_issues):
    """List issues can filter by state."""
    await client.post(
        f"{API}/repos/testuser/issue-repo/issues",
        json={"title": "Open issue"},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/repos/testuser/issue-repo/issues",
        json={"title": "Closed issue"},
        headers=auth_headers(test_token),
    )
    await client.patch(
        f"{API}/repos/testuser/issue-repo/issues/2",
        json={"state": "closed"},
        headers=auth_headers(test_token),
    )

    resp = await client.get(f"{API}/repos/testuser/issue-repo/issues?state=open")
    assert len(resp.json()) == 1

    resp = await client.get(f"{API}/repos/testuser/issue-repo/issues?state=closed")
    assert len(resp.json()) == 1

    resp = await client.get(f"{API}/repos/testuser/issue-repo/issues?state=all")
    assert len(resp.json()) == 2

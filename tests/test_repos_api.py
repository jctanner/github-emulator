"""Tests for the Repository REST API endpoints."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.mark.asyncio
async def test_create_repo(client, test_user, test_token):
    """POST /user/repos creates a repository."""
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "test-repo", "description": "A test repo"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "test-repo"
    assert data["full_name"] == "testuser/test-repo"
    assert data["private"] is False
    assert data["description"] == "A test repo"
    assert data["owner"]["login"] == "testuser"
    assert "clone_url" in data
    assert "html_url" in data


@pytest.mark.asyncio
async def test_create_repo_requires_auth(client):
    """POST /user/repos without auth returns 401."""
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "test-repo"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_duplicate_repo(client, test_user, test_token):
    """Creating a repo with the same name returns 422."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "dupe-repo"},
        headers=auth_headers(test_token),
    )
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "dupe-repo"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_repo(client, test_user, test_token):
    """GET /repos/{owner}/{repo} returns repo details."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "get-test"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/repos/testuser/get-test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["full_name"] == "testuser/get-test"
    # Verify URL template fields
    assert "forks_url" in data
    assert "branches_url" in data
    assert "permissions" in data


@pytest.mark.asyncio
async def test_get_nonexistent_repo(client):
    """GET /repos/{owner}/{repo} returns 404 for missing repo."""
    resp = await client.get(f"{API}/repos/nobody/nothing")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_repo(client, test_user, test_token):
    """PATCH /repos/{owner}/{repo} updates repo fields."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "update-test"},
        headers=auth_headers(test_token),
    )
    resp = await client.patch(
        f"{API}/repos/testuser/update-test",
        json={"description": "Updated!", "has_wiki": False},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["description"] == "Updated!"
    assert data["has_wiki"] is False


@pytest.mark.asyncio
async def test_delete_repo(client, test_user, test_token):
    """DELETE /repos/{owner}/{repo} removes the repo."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "delete-test"},
        headers=auth_headers(test_token),
    )
    resp = await client.delete(
        f"{API}/repos/testuser/delete-test",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 204

    # Verify it's gone
    resp = await client.get(f"{API}/repos/testuser/delete-test")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_user_repos(client, test_user, test_token):
    """GET /users/{username}/repos lists repos."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "list-test-1"},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/user/repos",
        json={"name": "list-test-2"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/users/testuser/repos")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2


@pytest.mark.asyncio
async def test_list_authenticated_user_repos(client, test_user, test_token):
    """GET /user/repos lists repos for authenticated user."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "my-repo"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(
        f"{API}/user/repos",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert any(r["name"] == "my-repo" for r in data)


@pytest.mark.asyncio
async def test_repo_response_format(client, test_user, test_token):
    """Verify repo response matches GitHub's format."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "format-test"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/repos/testuser/format-test")
    data = resp.json()

    # Check all required fields exist
    required_fields = [
        "id", "node_id", "name", "full_name", "private", "owner",
        "html_url", "description", "fork", "url", "created_at",
        "updated_at", "pushed_at", "clone_url", "default_branch",
        "forks_count", "stargazers_count", "watchers_count",
        "has_issues", "has_wiki", "has_projects", "has_pages",
        "has_downloads", "has_discussions", "archived", "disabled",
        "visibility", "forks", "open_issues", "watchers",
        "permissions",
    ]
    for field in required_fields:
        assert field in data, f"Missing field: {field}"

    # Check URL template fields
    url_fields = [
        "forks_url", "keys_url", "collaborators_url", "teams_url",
        "hooks_url", "events_url", "branches_url", "tags_url",
        "blobs_url", "git_tags_url", "git_refs_url", "trees_url",
        "statuses_url", "languages_url", "stargazers_url",
        "commits_url", "git_commits_url", "comments_url",
        "issue_comment_url", "contents_url", "compare_url",
        "merges_url", "archive_url", "issues_url", "pulls_url",
        "milestones_url", "labels_url", "releases_url",
        "deployments_url",
    ]
    for field in url_fields:
        assert field in data, f"Missing URL field: {field}"

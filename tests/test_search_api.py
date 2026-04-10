"""Tests for the Search REST API endpoints."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.fixture
async def search_data(client, test_user, test_token):
    """Create repos and issues to support search tests."""
    # Create multiple repos
    for name, desc in [
        ("alpha-project", "The alpha project"),
        ("beta-project", "The beta project"),
        ("gamma-tools", "Utility tools"),
    ]:
        await client.post(
            f"{API}/user/repos",
            json={"name": name, "description": desc},
            headers=auth_headers(test_token),
        )

    # Create issues in the first repo
    for title in [
        "Login page is broken",
        "Add search feature",
        "Fix homepage layout",
    ]:
        await client.post(
            f"{API}/repos/testuser/alpha-project/issues",
            json={"title": title, "body": f"Details about: {title}"},
            headers=auth_headers(test_token),
        )

    return True


# ---------------------------------------------------------------------------
# Search repositories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_repositories_by_name(client, test_user, test_token, search_data):
    """GET /search/repositories?q=... finds repos by name."""
    resp = await client.get(
        f"{API}/search/repositories?q=alpha",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total_count" in data
    assert "incomplete_results" in data
    assert "items" in data
    assert data["total_count"] >= 1
    names = [r["name"] for r in data["items"]]
    assert "alpha-project" in names


@pytest.mark.asyncio
async def test_search_repositories_by_description(client, test_user, test_token, search_data):
    """Search repositories matches on description."""
    resp = await client.get(
        f"{API}/search/repositories?q=utility",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] >= 1
    names = [r["name"] for r in data["items"]]
    assert "gamma-tools" in names


@pytest.mark.asyncio
async def test_search_repositories_no_results(client, test_user, test_token, search_data):
    """Search with no matches returns zero results."""
    resp = await client.get(
        f"{API}/search/repositories?q=zzzznonexistent",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 0
    assert data["items"] == []
    assert data["incomplete_results"] is False


@pytest.mark.asyncio
async def test_search_repositories_pagination(client, test_user, test_token, search_data):
    """Search repositories respects per_page parameter."""
    resp = await client.get(
        f"{API}/search/repositories?q=project&per_page=1",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 1
    assert data["total_count"] >= 2


# ---------------------------------------------------------------------------
# Search issues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_issues_by_title(client, test_user, test_token, search_data):
    """GET /search/issues?q=... finds issues by title."""
    resp = await client.get(
        f"{API}/search/issues?q=broken",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] >= 1
    titles = [i["title"] for i in data["items"]]
    assert "Login page is broken" in titles


@pytest.mark.asyncio
async def test_search_issues_by_body(client, test_user, test_token, search_data):
    """Search issues matches on body text."""
    resp = await client.get(
        f"{API}/search/issues?q=search+feature",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] >= 1


@pytest.mark.asyncio
async def test_search_issues_no_results(client, test_user, test_token, search_data):
    """Search issues with no matches returns zero results."""
    resp = await client.get(
        f"{API}/search/issues?q=xxxxxxxxnothing",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_search_issues_response_shape(client, test_user, test_token, search_data):
    """Verify the search issues response has the expected structure."""
    resp = await client.get(
        f"{API}/search/issues?q=Login",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total_count" in data
    assert "incomplete_results" in data
    assert "items" in data
    if data["items"]:
        item = data["items"][0]
        assert "title" in item
        assert "number" in item
        assert "state" in item


# ---------------------------------------------------------------------------
# Search users
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_users_by_login(client, test_user, test_token):
    """GET /search/users?q=... finds users by login."""
    resp = await client.get(
        f"{API}/search/users?q=testuser",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] >= 1
    logins = [u["login"] for u in data["items"]]
    assert "testuser" in logins


@pytest.mark.asyncio
async def test_search_users_by_name(client, test_user, test_token):
    """Search users matches on display name."""
    resp = await client.get(
        f"{API}/search/users?q=Test+User",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] >= 1


@pytest.mark.asyncio
async def test_search_users_no_results(client, test_user, test_token):
    """Search users with no matches returns zero results."""
    resp = await client.get(
        f"{API}/search/users?q=zzzznoone",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_search_users_response_shape(client, test_user, test_token):
    """Verify the search users response has the expected structure."""
    resp = await client.get(
        f"{API}/search/users?q=testuser",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total_count" in data
    assert "incomplete_results" in data
    assert "items" in data
    if data["items"]:
        user = data["items"][0]
        assert "login" in user
        assert "id" in user

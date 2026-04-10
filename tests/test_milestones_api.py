"""Tests for the Milestone REST API endpoints."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.mark.asyncio
async def test_create_milestone(client, test_user, test_token):
    """POST /repos/{owner}/{repo}/milestones creates a milestone."""
    await client.post(
        f"{API}/user/repos", json={"name": "ms-repo"}, headers=auth_headers(test_token)
    )
    resp = await client.post(
        f"{API}/repos/testuser/ms-repo/milestones",
        json={"title": "v1.0", "description": "First release"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "v1.0"
    assert data["number"] == 1
    assert data["state"] == "open"
    assert data["description"] == "First release"


@pytest.mark.asyncio
async def test_list_milestones(client, test_user, test_token):
    """GET /repos/{owner}/{repo}/milestones lists milestones."""
    await client.post(
        f"{API}/user/repos", json={"name": "ms-list"}, headers=auth_headers(test_token)
    )
    await client.post(
        f"{API}/repos/testuser/ms-list/milestones",
        json={"title": "v1.0"},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/repos/testuser/ms-list/milestones",
        json={"title": "v2.0"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/repos/testuser/ms-list/milestones")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2


@pytest.mark.asyncio
async def test_get_milestone(client, test_user, test_token):
    """GET /repos/{owner}/{repo}/milestones/{number} returns milestone."""
    await client.post(
        f"{API}/user/repos", json={"name": "ms-get"}, headers=auth_headers(test_token)
    )
    await client.post(
        f"{API}/repos/testuser/ms-get/milestones",
        json={"title": "v1.0"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/repos/testuser/ms-get/milestones/1")
    assert resp.status_code == 200
    assert resp.json()["title"] == "v1.0"


@pytest.mark.asyncio
async def test_get_milestone_not_found(client, test_user, test_token):
    """GET /repos/{owner}/{repo}/milestones/{number} returns 404 for missing."""
    await client.post(
        f"{API}/user/repos", json={"name": "ms-404"}, headers=auth_headers(test_token)
    )
    resp = await client.get(f"{API}/repos/testuser/ms-404/milestones/99")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_milestone(client, test_user, test_token):
    """PATCH /repos/{owner}/{repo}/milestones/{number} updates milestone."""
    await client.post(
        f"{API}/user/repos", json={"name": "ms-upd"}, headers=auth_headers(test_token)
    )
    await client.post(
        f"{API}/repos/testuser/ms-upd/milestones",
        json={"title": "v1.0"},
        headers=auth_headers(test_token),
    )
    resp = await client.patch(
        f"{API}/repos/testuser/ms-upd/milestones/1",
        json={"title": "v1.1", "state": "closed"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "v1.1"
    assert data["state"] == "closed"


@pytest.mark.asyncio
async def test_delete_milestone(client, test_user, test_token):
    """DELETE /repos/{owner}/{repo}/milestones/{number} removes milestone."""
    await client.post(
        f"{API}/user/repos", json={"name": "ms-del"}, headers=auth_headers(test_token)
    )
    await client.post(
        f"{API}/repos/testuser/ms-del/milestones",
        json={"title": "v1.0"},
        headers=auth_headers(test_token),
    )
    resp = await client.delete(
        f"{API}/repos/testuser/ms-del/milestones/1",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 204

    # Verify gone
    resp = await client.get(f"{API}/repos/testuser/ms-del/milestones/1")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_milestone_numbering(client, test_user, test_token):
    """Milestones get auto-incrementing numbers per repo."""
    await client.post(
        f"{API}/user/repos", json={"name": "ms-num"}, headers=auth_headers(test_token)
    )
    r1 = await client.post(
        f"{API}/repos/testuser/ms-num/milestones",
        json={"title": "First"},
        headers=auth_headers(test_token),
    )
    r2 = await client.post(
        f"{API}/repos/testuser/ms-num/milestones",
        json={"title": "Second"},
        headers=auth_headers(test_token),
    )
    assert r1.json()["number"] == 1
    assert r2.json()["number"] == 2


@pytest.mark.asyncio
async def test_milestone_with_due_date(client, test_user, test_token):
    """Milestones support due_on field."""
    await client.post(
        f"{API}/user/repos", json={"name": "ms-due"}, headers=auth_headers(test_token)
    )
    resp = await client.post(
        f"{API}/repos/testuser/ms-due/milestones",
        json={"title": "v1.0", "due_on": "2025-12-31T00:00:00Z"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    assert resp.json()["due_on"] is not None


@pytest.mark.asyncio
async def test_list_milestones_filter_state(client, test_user, test_token):
    """Milestones can be filtered by state."""
    await client.post(
        f"{API}/user/repos", json={"name": "ms-filter"}, headers=auth_headers(test_token)
    )
    await client.post(
        f"{API}/repos/testuser/ms-filter/milestones",
        json={"title": "Open"},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/repos/testuser/ms-filter/milestones",
        json={"title": "Closed", "state": "closed"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/repos/testuser/ms-filter/milestones?state=open")
    assert resp.status_code == 200
    data = resp.json()
    assert all(m["state"] == "open" for m in data)


@pytest.mark.asyncio
async def test_milestone_response_format(client, test_user, test_token):
    """Milestone response has required fields."""
    await client.post(
        f"{API}/user/repos", json={"name": "ms-fmt"}, headers=auth_headers(test_token)
    )
    resp = await client.post(
        f"{API}/repos/testuser/ms-fmt/milestones",
        json={"title": "v1.0"},
        headers=auth_headers(test_token),
    )
    data = resp.json()
    for field in ["id", "number", "title", "state", "url", "html_url",
                  "created_at", "updated_at", "open_issues", "closed_issues"]:
        assert field in data, f"Missing field: {field}"

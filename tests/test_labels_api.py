"""Tests for the Labels REST API endpoints."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.fixture
async def label_repo(client, test_user, test_token):
    """Create a repo for label tests."""
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "label-repo"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    return resp.json()


@pytest.fixture
async def label_repo_with_issue(client, test_user, test_token, label_repo):
    """Create a repo with an issue for issue-label tests."""
    resp = await client.post(
        f"{API}/repos/testuser/label-repo/issues",
        json={"title": "Label test issue"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    return label_repo


# ---------------------------------------------------------------------------
# Repo-level label CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_label(client, test_user, test_token, label_repo):
    """POST /repos/{owner}/{repo}/labels creates a label."""
    resp = await client.post(
        f"{API}/repos/testuser/label-repo/labels",
        json={"name": "bug", "color": "d73a4a", "description": "Something isn't working"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "bug"
    assert data["color"] == "d73a4a"
    assert data["description"] == "Something isn't working"
    assert "id" in data
    assert "node_id" in data
    assert "url" in data


@pytest.mark.asyncio
async def test_create_label_strips_hash(client, test_user, test_token, label_repo):
    """Creating a label with '#' prefix in color strips the hash."""
    resp = await client.post(
        f"{API}/repos/testuser/label-repo/labels",
        json={"name": "enhancement", "color": "#a2eeef"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    assert resp.json()["color"] == "a2eeef"


@pytest.mark.asyncio
async def test_create_duplicate_label(client, test_user, test_token, label_repo):
    """Creating a label with the same name returns 422."""
    await client.post(
        f"{API}/repos/testuser/label-repo/labels",
        json={"name": "duplicate", "color": "000000"},
        headers=auth_headers(test_token),
    )
    resp = await client.post(
        f"{API}/repos/testuser/label-repo/labels",
        json={"name": "duplicate", "color": "111111"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_labels(client, test_user, test_token, label_repo):
    """GET /repos/{owner}/{repo}/labels lists all labels."""
    for name in ["bug", "feature", "docs"]:
        await client.post(
            f"{API}/repos/testuser/label-repo/labels",
            json={"name": name, "color": "ededed"},
            headers=auth_headers(test_token),
        )
    resp = await client.get(
        f"{API}/repos/testuser/label-repo/labels",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 3
    names = [l["name"] for l in data]
    assert "bug" in names
    assert "feature" in names
    assert "docs" in names


@pytest.mark.asyncio
async def test_get_label_by_name(client, test_user, test_token, label_repo):
    """GET /repos/{owner}/{repo}/labels/{name} returns a single label."""
    await client.post(
        f"{API}/repos/testuser/label-repo/labels",
        json={"name": "important", "color": "ff0000"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(
        f"{API}/repos/testuser/label-repo/labels/important",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "important"
    assert data["color"] == "ff0000"


@pytest.mark.asyncio
async def test_get_nonexistent_label(client, test_user, test_token, label_repo):
    """GET /repos/{owner}/{repo}/labels/{name} returns 404 for missing label."""
    resp = await client.get(
        f"{API}/repos/testuser/label-repo/labels/nonexistent",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_label(client, test_user, test_token, label_repo):
    """PATCH /repos/{owner}/{repo}/labels/{name} updates the label."""
    await client.post(
        f"{API}/repos/testuser/label-repo/labels",
        json={"name": "old-name", "color": "aabbcc"},
        headers=auth_headers(test_token),
    )
    resp = await client.patch(
        f"{API}/repos/testuser/label-repo/labels/old-name",
        json={"new_name": "new-name", "color": "112233", "description": "Updated desc"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "new-name"
    assert data["color"] == "112233"
    assert data["description"] == "Updated desc"


@pytest.mark.asyncio
async def test_update_nonexistent_label(client, test_user, test_token, label_repo):
    """PATCH for a nonexistent label returns 404."""
    resp = await client.patch(
        f"{API}/repos/testuser/label-repo/labels/does-not-exist",
        json={"color": "ffffff"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_label(client, test_user, test_token, label_repo):
    """DELETE /repos/{owner}/{repo}/labels/{name} removes the label."""
    await client.post(
        f"{API}/repos/testuser/label-repo/labels",
        json={"name": "to-delete", "color": "000000"},
        headers=auth_headers(test_token),
    )
    resp = await client.delete(
        f"{API}/repos/testuser/label-repo/labels/to-delete",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 204

    # Verify it's gone
    resp = await client.get(
        f"{API}/repos/testuser/label-repo/labels/to-delete",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_label(client, test_user, test_token, label_repo):
    """DELETE for a nonexistent label returns 404."""
    resp = await client.delete(
        f"{API}/repos/testuser/label-repo/labels/nope",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Issue-level label management
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_labels_to_issue(client, test_user, test_token, label_repo_with_issue):
    """POST /repos/{owner}/{repo}/issues/{number}/labels adds labels."""
    # Create two labels on the repo first
    for name in ["bug", "urgent"]:
        await client.post(
            f"{API}/repos/testuser/label-repo/labels",
            json={"name": name, "color": "ededed"},
            headers=auth_headers(test_token),
        )

    resp = await client.post(
        f"{API}/repos/testuser/label-repo/issues/1/labels",
        json={"labels": ["bug", "urgent"]},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    names = [l["name"] for l in data]
    assert "bug" in names
    assert "urgent" in names


@pytest.mark.asyncio
async def test_list_issue_labels(client, test_user, test_token, label_repo_with_issue):
    """GET /repos/{owner}/{repo}/issues/{number}/labels lists labels on the issue."""
    # Create and add a label
    await client.post(
        f"{API}/repos/testuser/label-repo/labels",
        json={"name": "listed", "color": "aaaaaa"},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/repos/testuser/label-repo/issues/1/labels",
        json={"labels": ["listed"]},
        headers=auth_headers(test_token),
    )

    resp = await client.get(
        f"{API}/repos/testuser/label-repo/issues/1/labels",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(l["name"] == "listed" for l in data)


@pytest.mark.asyncio
async def test_remove_label_from_issue(client, test_user, test_token, label_repo_with_issue):
    """DELETE /repos/{owner}/{repo}/issues/{number}/labels/{name} removes a label."""
    # Create and add a label
    await client.post(
        f"{API}/repos/testuser/label-repo/labels",
        json={"name": "removable", "color": "bbbbbb"},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/repos/testuser/label-repo/issues/1/labels",
        json={"labels": ["removable"]},
        headers=auth_headers(test_token),
    )

    resp = await client.delete(
        f"{API}/repos/testuser/label-repo/issues/1/labels/removable",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    names = [l["name"] for l in data]
    assert "removable" not in names


@pytest.mark.asyncio
async def test_remove_nonexistent_label_from_issue(client, test_user, test_token, label_repo_with_issue):
    """Removing a label that doesn't exist from an issue returns 404."""
    resp = await client.delete(
        f"{API}/repos/testuser/label-repo/issues/1/labels/nonexistent",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404

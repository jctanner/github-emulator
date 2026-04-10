"""Tests for the Issue Comments REST API endpoints."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.fixture
async def comment_repo(client, test_user, test_token):
    """Create a repo with an issue for comment tests."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "comment-repo"},
        headers=auth_headers(test_token),
    )
    resp = await client.post(
        f"{API}/repos/testuser/comment-repo/issues",
        json={"title": "Comment test issue"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# Comment CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_comment(client, test_user, test_token, comment_repo):
    """POST /repos/{owner}/{repo}/issues/{number}/comments creates a comment."""
    resp = await client.post(
        f"{API}/repos/testuser/comment-repo/issues/1/comments",
        json={"body": "This is a comment"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["body"] == "This is a comment"
    assert "id" in data
    assert "node_id" in data
    assert "url" in data
    assert "html_url" in data
    assert "created_at" in data
    assert "updated_at" in data
    assert data["user"]["login"] == "testuser"


@pytest.mark.asyncio
async def test_create_comment_requires_body(client, test_user, test_token, comment_repo):
    """POST without a body field returns 422."""
    resp = await client.post(
        f"{API}/repos/testuser/comment-repo/issues/1/comments",
        json={},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_comment_requires_auth(client, test_user, test_token, comment_repo):
    """POST without auth returns 401."""
    resp = await client.post(
        f"{API}/repos/testuser/comment-repo/issues/1/comments",
        json={"body": "No auth"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_comment_on_nonexistent_issue(client, test_user, test_token, comment_repo):
    """POST to a nonexistent issue returns 404."""
    resp = await client.post(
        f"{API}/repos/testuser/comment-repo/issues/999/comments",
        json={"body": "Ghost issue"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_comments(client, test_user, test_token, comment_repo):
    """GET /repos/{owner}/{repo}/issues/{number}/comments lists comments."""
    for i in range(3):
        await client.post(
            f"{API}/repos/testuser/comment-repo/issues/1/comments",
            json={"body": f"Comment {i + 1}"},
            headers=auth_headers(test_token),
        )
    resp = await client.get(
        f"{API}/repos/testuser/comment-repo/issues/1/comments",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 3
    assert data[0]["body"] == "Comment 1"
    assert data[2]["body"] == "Comment 3"


@pytest.mark.asyncio
async def test_list_comments_empty(client, test_user, test_token, comment_repo):
    """Listing comments on an issue with no comments returns an empty list."""
    resp = await client.get(
        f"{API}/repos/testuser/comment-repo/issues/1/comments",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_comment_by_id(client, test_user, test_token, comment_repo):
    """GET /repos/{owner}/{repo}/issues/comments/{id} returns a single comment."""
    create_resp = await client.post(
        f"{API}/repos/testuser/comment-repo/issues/1/comments",
        json={"body": "Specific comment"},
        headers=auth_headers(test_token),
    )
    comment_id = create_resp.json()["id"]

    resp = await client.get(
        f"{API}/repos/testuser/comment-repo/issues/comments/{comment_id}",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == comment_id
    assert data["body"] == "Specific comment"


@pytest.mark.asyncio
async def test_get_nonexistent_comment(client, test_user, test_token, comment_repo):
    """GET for a nonexistent comment ID returns 404."""
    resp = await client.get(
        f"{API}/repos/testuser/comment-repo/issues/comments/999999",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_comment(client, test_user, test_token, comment_repo):
    """PATCH /repos/{owner}/{repo}/issues/comments/{id} updates the comment."""
    create_resp = await client.post(
        f"{API}/repos/testuser/comment-repo/issues/1/comments",
        json={"body": "Original body"},
        headers=auth_headers(test_token),
    )
    comment_id = create_resp.json()["id"]

    resp = await client.patch(
        f"{API}/repos/testuser/comment-repo/issues/comments/{comment_id}",
        json={"body": "Updated body"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["body"] == "Updated body"
    assert data["id"] == comment_id


@pytest.mark.asyncio
async def test_update_nonexistent_comment(client, test_user, test_token, comment_repo):
    """PATCH for a nonexistent comment returns 404."""
    resp = await client.patch(
        f"{API}/repos/testuser/comment-repo/issues/comments/999999",
        json={"body": "Updated"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_comment(client, test_user, test_token, comment_repo):
    """DELETE /repos/{owner}/{repo}/issues/comments/{id} removes the comment."""
    create_resp = await client.post(
        f"{API}/repos/testuser/comment-repo/issues/1/comments",
        json={"body": "To be deleted"},
        headers=auth_headers(test_token),
    )
    comment_id = create_resp.json()["id"]

    resp = await client.delete(
        f"{API}/repos/testuser/comment-repo/issues/comments/{comment_id}",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 204

    # Verify it's gone
    resp = await client.get(
        f"{API}/repos/testuser/comment-repo/issues/comments/{comment_id}",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_comment(client, test_user, test_token, comment_repo):
    """DELETE for a nonexistent comment returns 404."""
    resp = await client.delete(
        f"{API}/repos/testuser/comment-repo/issues/comments/999999",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_comment_response_shape(client, test_user, test_token, comment_repo):
    """Verify the comment response includes all expected fields."""
    create_resp = await client.post(
        f"{API}/repos/testuser/comment-repo/issues/1/comments",
        json={"body": "Shape test"},
        headers=auth_headers(test_token),
    )
    data = create_resp.json()

    required_fields = [
        "id", "node_id", "url", "html_url", "body", "user",
        "created_at", "updated_at", "issue_url",
        "author_association", "reactions",
    ]
    for field in required_fields:
        assert field in data, f"Missing field: {field}"

    # Verify reactions sub-object shape
    reactions = data["reactions"]
    assert "url" in reactions
    assert "total_count" in reactions
    assert "+1" in reactions
    assert "-1" in reactions

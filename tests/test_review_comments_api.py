"""Tests for the PR Review Comments REST API endpoints."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


async def _create_pr(client, token, repo_name="rc-repo"):
    """Helper to create a repo, issue, and PR for review comment tests."""
    await client.post(
        f"{API}/user/repos", json={"name": repo_name}, headers=auth_headers(token)
    )
    # Create an issue first (PRs are linked to issues)
    await client.post(
        f"{API}/repos/testuser/{repo_name}/issues",
        json={"title": "Test Issue"},
        headers=auth_headers(token),
    )
    # Create a PR
    resp = await client.post(
        f"{API}/repos/testuser/{repo_name}/pulls",
        json={
            "title": "Test PR",
            "head": "feature",
            "base": "main",
            "body": "Test PR body",
        },
        headers=auth_headers(token),
    )
    return resp.json()


@pytest.mark.asyncio
async def test_create_review_comment(client, test_user, test_token):
    """POST /repos/{owner}/{repo}/pulls/{n}/comments creates a review comment."""
    pr = await _create_pr(client, test_token, "rc-create")
    pr_number = pr["number"]
    resp = await client.post(
        f"{API}/repos/testuser/rc-create/pulls/{pr_number}/comments",
        json={
            "body": "Great code!",
            "path": "src/main.py",
            "position": 1,
            "commit_id": "abc123",
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["body"] == "Great code!"
    assert data["path"] == "src/main.py"
    assert data["position"] == 1


@pytest.mark.asyncio
async def test_list_review_comments(client, test_user, test_token):
    """GET /repos/{owner}/{repo}/pulls/{n}/comments lists comments."""
    pr = await _create_pr(client, test_token, "rc-list")
    pr_number = pr["number"]
    await client.post(
        f"{API}/repos/testuser/rc-list/pulls/{pr_number}/comments",
        json={"body": "Comment 1", "path": "file.py", "commit_id": "abc"},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/repos/testuser/rc-list/pulls/{pr_number}/comments",
        json={"body": "Comment 2", "path": "file.py", "commit_id": "abc"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/repos/testuser/rc-list/pulls/{pr_number}/comments")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2


@pytest.mark.asyncio
async def test_get_review_comment(client, test_user, test_token):
    """GET /repos/{owner}/{repo}/pulls/comments/{id} returns a comment."""
    pr = await _create_pr(client, test_token, "rc-get")
    pr_number = pr["number"]
    create = await client.post(
        f"{API}/repos/testuser/rc-get/pulls/{pr_number}/comments",
        json={"body": "Single comment", "path": "file.py", "commit_id": "abc"},
        headers=auth_headers(test_token),
    )
    comment_id = create.json()["id"]
    resp = await client.get(
        f"{API}/repos/testuser/rc-get/pulls/comments/{comment_id}",
    )
    assert resp.status_code == 200
    assert resp.json()["body"] == "Single comment"


@pytest.mark.asyncio
async def test_update_review_comment(client, test_user, test_token):
    """PATCH /repos/{owner}/{repo}/pulls/comments/{id} updates a comment."""
    pr = await _create_pr(client, test_token, "rc-upd")
    pr_number = pr["number"]
    create = await client.post(
        f"{API}/repos/testuser/rc-upd/pulls/{pr_number}/comments",
        json={"body": "Original", "path": "file.py", "commit_id": "abc"},
        headers=auth_headers(test_token),
    )
    comment_id = create.json()["id"]
    resp = await client.patch(
        f"{API}/repos/testuser/rc-upd/pulls/comments/{comment_id}",
        json={"body": "Updated body"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert resp.json()["body"] == "Updated body"


@pytest.mark.asyncio
async def test_delete_review_comment(client, test_user, test_token):
    """DELETE /repos/{owner}/{repo}/pulls/comments/{id} deletes a comment."""
    pr = await _create_pr(client, test_token, "rc-del")
    pr_number = pr["number"]
    create = await client.post(
        f"{API}/repos/testuser/rc-del/pulls/{pr_number}/comments",
        json={"body": "To delete", "path": "file.py", "commit_id": "abc"},
        headers=auth_headers(test_token),
    )
    comment_id = create.json()["id"]
    resp = await client.delete(
        f"{API}/repos/testuser/rc-del/pulls/comments/{comment_id}",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_review_comment_requires_body(client, test_user, test_token):
    """Creating review comment without body returns 422."""
    pr = await _create_pr(client, test_token, "rc-nobody")
    pr_number = pr["number"]
    resp = await client.post(
        f"{API}/repos/testuser/rc-nobody/pulls/{pr_number}/comments",
        json={"path": "file.py", "commit_id": "abc"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_review_comment_requires_path(client, test_user, test_token):
    """Creating review comment without path returns 422."""
    pr = await _create_pr(client, test_token, "rc-nopath")
    pr_number = pr["number"]
    resp = await client.post(
        f"{API}/repos/testuser/rc-nopath/pulls/{pr_number}/comments",
        json={"body": "No path", "commit_id": "abc"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_review_comment_not_found(client, test_user, test_token):
    """GET non-existent review comment returns 404."""
    await client.post(
        f"{API}/user/repos", json={"name": "rc-404"}, headers=auth_headers(test_token)
    )
    resp = await client.get(f"{API}/repos/testuser/rc-404/pulls/comments/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_review_comment_response_format(client, test_user, test_token):
    """Review comment response has required fields."""
    pr = await _create_pr(client, test_token, "rc-fmt")
    pr_number = pr["number"]
    resp = await client.post(
        f"{API}/repos/testuser/rc-fmt/pulls/{pr_number}/comments",
        json={"body": "Format test", "path": "file.py", "commit_id": "abc"},
        headers=auth_headers(test_token),
    )
    data = resp.json()
    for field in ["id", "node_id", "url", "body", "path", "position",
                  "commit_id", "user", "created_at", "updated_at",
                  "html_url", "pull_request_url", "_links", "reactions"]:
        assert field in data, f"Missing field: {field}"

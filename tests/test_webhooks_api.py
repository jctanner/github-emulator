"""Tests for the Webhook REST API endpoints."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.mark.asyncio
async def test_create_webhook(client, test_user, test_token):
    """POST /repos/{owner}/{repo}/hooks creates a webhook."""
    await client.post(
        f"{API}/user/repos", json={"name": "hook-repo"}, headers=auth_headers(test_token)
    )
    resp = await client.post(
        f"{API}/repos/testuser/hook-repo/hooks",
        json={
            "config": {"url": "https://example.com/webhook", "content_type": "json"},
            "events": ["push", "pull_request"],
            "active": True,
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["active"] is True
    assert data["config"]["url"] == "https://example.com/webhook"
    assert "push" in data["events"]


@pytest.mark.asyncio
async def test_list_webhooks(client, test_user, test_token):
    """GET /repos/{owner}/{repo}/hooks lists webhooks."""
    await client.post(
        f"{API}/user/repos", json={"name": "hook-list"}, headers=auth_headers(test_token)
    )
    await client.post(
        f"{API}/repos/testuser/hook-list/hooks",
        json={"config": {"url": "https://example.com/hook1"}},
        headers=auth_headers(test_token),
    )
    resp = await client.get(
        f"{API}/repos/testuser/hook-list/hooks", headers=auth_headers(test_token)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_get_webhook(client, test_user, test_token):
    """GET /repos/{owner}/{repo}/hooks/{id} returns a webhook."""
    await client.post(
        f"{API}/user/repos", json={"name": "hook-get"}, headers=auth_headers(test_token)
    )
    create = await client.post(
        f"{API}/repos/testuser/hook-get/hooks",
        json={"config": {"url": "https://example.com/hook"}},
        headers=auth_headers(test_token),
    )
    hook_id = create.json()["id"]
    resp = await client.get(
        f"{API}/repos/testuser/hook-get/hooks/{hook_id}",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == hook_id


@pytest.mark.asyncio
async def test_update_webhook(client, test_user, test_token):
    """PATCH /repos/{owner}/{repo}/hooks/{id} updates a webhook."""
    await client.post(
        f"{API}/user/repos", json={"name": "hook-upd"}, headers=auth_headers(test_token)
    )
    create = await client.post(
        f"{API}/repos/testuser/hook-upd/hooks",
        json={"config": {"url": "https://example.com/hook"}, "active": True},
        headers=auth_headers(test_token),
    )
    hook_id = create.json()["id"]
    resp = await client.patch(
        f"{API}/repos/testuser/hook-upd/hooks/{hook_id}",
        json={"active": False, "events": ["issues"]},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] is False
    assert "issues" in data["events"]


@pytest.mark.asyncio
async def test_delete_webhook(client, test_user, test_token):
    """DELETE /repos/{owner}/{repo}/hooks/{id} removes a webhook."""
    await client.post(
        f"{API}/user/repos", json={"name": "hook-del"}, headers=auth_headers(test_token)
    )
    create = await client.post(
        f"{API}/repos/testuser/hook-del/hooks",
        json={"config": {"url": "https://example.com/hook"}},
        headers=auth_headers(test_token),
    )
    hook_id = create.json()["id"]
    resp = await client.delete(
        f"{API}/repos/testuser/hook-del/hooks/{hook_id}",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_webhook_config_shape(client, test_user, test_token):
    """Webhook config has url, content_type, insecure_ssl."""
    await client.post(
        f"{API}/user/repos", json={"name": "hook-cfg"}, headers=auth_headers(test_token)
    )
    resp = await client.post(
        f"{API}/repos/testuser/hook-cfg/hooks",
        json={"config": {"url": "https://example.com/hook", "content_type": "form"}},
        headers=auth_headers(test_token),
    )
    data = resp.json()
    assert "config" in data
    config = data["config"]
    assert "url" in config
    assert "content_type" in config
    assert "insecure_ssl" in config


@pytest.mark.asyncio
async def test_webhook_requires_url(client, test_user, test_token):
    """Webhook creation requires config.url."""
    await client.post(
        f"{API}/user/repos", json={"name": "hook-nourl"}, headers=auth_headers(test_token)
    )
    resp = await client.post(
        f"{API}/repos/testuser/hook-nourl/hooks",
        json={"config": {}},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_webhook_not_found(client, test_user, test_token):
    """GET webhook with invalid ID returns 404."""
    await client.post(
        f"{API}/user/repos", json={"name": "hook-nf"}, headers=auth_headers(test_token)
    )
    resp = await client.get(
        f"{API}/repos/testuser/hook-nf/hooks/99999",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404

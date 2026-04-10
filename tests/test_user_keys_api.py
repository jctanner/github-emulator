"""Tests for the SSH/GPG Key REST API endpoints."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


# --- SSH Keys ---

@pytest.mark.asyncio
async def test_create_ssh_key(client, test_user, test_token):
    """POST /user/keys creates an SSH key."""
    resp = await client.post(
        f"{API}/user/keys",
        json={
            "title": "My Laptop",
            "key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC test@laptop",
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "My Laptop"
    assert data["key"].startswith("ssh-rsa")
    assert data["verified"] is True


@pytest.mark.asyncio
async def test_list_ssh_keys(client, test_user, test_token):
    """GET /user/keys lists SSH keys."""
    await client.post(
        f"{API}/user/keys",
        json={"title": "Key 1", "key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC1 test@1"},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/user/keys",
        json={"title": "Key 2", "key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC2 test@2"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/user/keys", headers=auth_headers(test_token))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2


@pytest.mark.asyncio
async def test_get_ssh_key(client, test_user, test_token):
    """GET /user/keys/{id} returns a specific key."""
    create = await client.post(
        f"{API}/user/keys",
        json={"title": "Get Test", "key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCget test@get"},
        headers=auth_headers(test_token),
    )
    key_id = create.json()["id"]
    resp = await client.get(
        f"{API}/user/keys/{key_id}", headers=auth_headers(test_token)
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Get Test"


@pytest.mark.asyncio
async def test_delete_ssh_key(client, test_user, test_token):
    """DELETE /user/keys/{id} removes a key."""
    create = await client.post(
        f"{API}/user/keys",
        json={"title": "Del Test", "key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCdel test@del"},
        headers=auth_headers(test_token),
    )
    key_id = create.json()["id"]
    resp = await client.delete(
        f"{API}/user/keys/{key_id}", headers=auth_headers(test_token)
    )
    assert resp.status_code == 204

    # Verify gone
    resp = await client.get(
        f"{API}/user/keys/{key_id}", headers=auth_headers(test_token)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ssh_key_requires_key_field(client, test_user, test_token):
    """POST /user/keys without key returns 422."""
    resp = await client.post(
        f"{API}/user/keys",
        json={"title": "No Key"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ssh_key_not_found(client, test_user, test_token):
    """GET /user/keys/{id} returns 404 for non-existent key."""
    resp = await client.get(
        f"{API}/user/keys/99999", headers=auth_headers(test_token)
    )
    assert resp.status_code == 404


# --- GPG Keys ---

@pytest.mark.asyncio
async def test_create_gpg_key(client, test_user, test_token):
    """POST /user/gpg_keys creates a GPG key."""
    resp = await client.post(
        f"{API}/user/gpg_keys",
        json={
            "armored_public_key": "-----BEGIN PGP PUBLIC KEY BLOCK-----\ntest\n-----END PGP PUBLIC KEY BLOCK-----",
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["public_key"].startswith("-----BEGIN PGP")
    assert data["can_sign"] is True


@pytest.mark.asyncio
async def test_list_gpg_keys(client, test_user, test_token):
    """GET /user/gpg_keys lists GPG keys."""
    await client.post(
        f"{API}/user/gpg_keys",
        json={"armored_public_key": "-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey1\n-----END PGP PUBLIC KEY BLOCK-----"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/user/gpg_keys", headers=auth_headers(test_token))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_get_gpg_key(client, test_user, test_token):
    """GET /user/gpg_keys/{id} returns a specific key."""
    create = await client.post(
        f"{API}/user/gpg_keys",
        json={"armored_public_key": "-----BEGIN PGP PUBLIC KEY BLOCK-----\ngetkey\n-----END PGP PUBLIC KEY BLOCK-----"},
        headers=auth_headers(test_token),
    )
    key_id = create.json()["id"]
    resp = await client.get(
        f"{API}/user/gpg_keys/{key_id}", headers=auth_headers(test_token)
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_delete_gpg_key(client, test_user, test_token):
    """DELETE /user/gpg_keys/{id} removes a GPG key."""
    create = await client.post(
        f"{API}/user/gpg_keys",
        json={"armored_public_key": "-----BEGIN PGP PUBLIC KEY BLOCK-----\ndelkey\n-----END PGP PUBLIC KEY BLOCK-----"},
        headers=auth_headers(test_token),
    )
    key_id = create.json()["id"]
    resp = await client.delete(
        f"{API}/user/gpg_keys/{key_id}", headers=auth_headers(test_token)
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_gpg_key_requires_armored_key(client, test_user, test_token):
    """POST /user/gpg_keys without armored_public_key returns 422."""
    resp = await client.post(
        f"{API}/user/gpg_keys",
        json={},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422


# --- Public endpoint ---

@pytest.mark.asyncio
async def test_list_user_public_keys(client, test_user, test_token):
    """GET /users/{username}/keys lists public SSH keys."""
    await client.post(
        f"{API}/user/keys",
        json={"title": "Public", "key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCpub test@pub"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(f"{API}/users/testuser/keys")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    # Public endpoint shows minimal info
    assert "id" in data[0]
    assert "key" in data[0]


@pytest.mark.asyncio
async def test_list_user_public_keys_not_found(client):
    """GET /users/{username}/keys returns 404 for non-existent user."""
    resp = await client.get(f"{API}/users/nonexistent_user_xyz/keys")
    assert resp.status_code == 404

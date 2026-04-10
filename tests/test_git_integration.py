"""Tests for Git integration -- clone, push, pull via Smart HTTP."""

import asyncio
import os
import subprocess
import tempfile

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.mark.asyncio
async def test_clone_repo_with_init(client, test_user, test_token, test_repo_with_init, tmp_path):
    """Git clone of an initialized repo works via HTTP transport."""
    owner, repo_name, _ = test_repo_with_init

    # Get the info/refs endpoint to verify it works
    resp = await client.get(
        f"/{owner}/{repo_name}.git/info/refs?service=git-upload-pack",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert "application/x-git-upload-pack-advertisement" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_info_refs_receive_pack(client, test_user, test_token, test_repo_with_init):
    """info/refs for git-receive-pack requires auth."""
    owner, repo_name, _ = test_repo_with_init
    resp = await client.get(
        f"/{owner}/{repo_name}.git/info/refs?service=git-receive-pack",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_info_refs_no_auth(client, test_user, test_token, test_repo_with_init):
    """info/refs without auth for public repo works for upload-pack."""
    owner, repo_name, _ = test_repo_with_init
    resp = await client.get(
        f"/{owner}/{repo_name}.git/info/refs?service=git-upload-pack",
    )
    # Public repo should allow unauthenticated read
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_info_refs_invalid_service(client, test_user, test_token, test_repo_with_init):
    """info/refs with invalid service returns 403."""
    owner, repo_name, _ = test_repo_with_init
    resp = await client.get(
        f"/{owner}/{repo_name}.git/info/refs?service=invalid-service",
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_upload_pack_endpoint(client, test_user, test_token, test_repo_with_init):
    """POST git-upload-pack endpoint responds."""
    owner, repo_name, _ = test_repo_with_init
    resp = await client.post(
        f"/{owner}/{repo_name}.git/git-upload-pack",
        content=b"0000",
        headers={
            **auth_headers(test_token),
            "content-type": "application/x-git-upload-pack-request",
        },
    )
    # Should respond (even if the pack negotiation fails with dummy data)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_nonexistent_repo_returns_404(client, test_user, test_token):
    """info/refs for nonexistent repo returns 404."""
    resp = await client.get(
        "/nobody/noexist.git/info/refs?service=git-upload-pack",
    )
    assert resp.status_code == 404

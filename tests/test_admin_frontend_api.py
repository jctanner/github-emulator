"""Contracts for the API-client site-administration surface."""

import pytest

from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_admin_summary_and_inventory(client, admin_token):
    headers = auth_headers(admin_token)
    summary = await client.get("/admin/api/summary", headers=headers)
    users = await client.get("/admin/api/users", headers=headers)
    repositories = await client.get("/admin/api/repositories", headers=headers)

    assert summary.status_code == 200
    assert summary.json()["users"] >= 1
    assert users.status_code == 200
    assert repositories.status_code == 200


@pytest.mark.asyncio
async def test_admin_user_and_organization_lifecycle(client, admin_token):
    headers = auth_headers(admin_token)
    user = await client.post(
        "/admin/api/users",
        json={"login": "frontend-admin-test", "password": "secret"},
        headers=headers,
    )
    organization = await client.post(
        "/admin/api/organizations",
        json={"login": "frontend-admin-org"},
        headers=headers,
    )

    assert user.status_code == 201
    assert organization.status_code == 201
    assert (
        await client.delete(
            f"/admin/api/organizations/{organization.json()['id']}", headers=headers
        )
    ).status_code == 204
    assert (
        await client.delete(
            f"/admin/api/users/{user.json()['id']}", headers=headers
        )
    ).status_code == 204


@pytest.mark.asyncio
async def test_repository_installations_list_is_typed(
    client, test_token, test_repo_with_init
):
    owner, repo, _ = test_repo_with_init
    response = await client.get(
        f"/api/v3/repos/{owner}/{repo}/installations",
        headers=auth_headers(test_token),
    )
    assert response.status_code == 200
    assert response.json() == []

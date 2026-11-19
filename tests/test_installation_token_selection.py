"""A token narrowed to named repositories reports repository_selection "selected"."""

import time

import pytest
from jose import jwt

from tests.conftest import API, auth_headers


async def _org_wide_app(client, admin_token, owner, app_id):
    created = await client.post(
        f"{API}/admin/apps", headers=auth_headers(admin_token),
        json={"app_id": app_id, "name": f"Selection {app_id}", "slug": f"selection-{app_id}",
              "permissions": {"issues": "write", "metadata": "read"}},
    )
    assert created.status_code == 201
    installation = await client.post(
        f"{API}/admin/apps/{app_id}/installations", headers=auth_headers(admin_token),
        json={"account_login": owner, "account_type": "User", "repositories": []},
    )
    assert installation.status_code == 201
    now = int(time.time())
    assertion = jwt.encode({"iss": app_id, "iat": now - 10, "exp": now + 60},
                           created.json()["private_key"], algorithm="RS256")
    return installation.json()["id"], {"Authorization": f"Bearer {assertion}"}


@pytest.mark.asyncio
async def test_named_repositories_make_the_token_selected(client, admin_token, test_repo_with_init):
    owner, repo_name, _repo = test_repo_with_init
    installation_id, headers = await _org_wide_app(client, admin_token, owner, "1101")
    narrowed = await client.post(
        f"{API}/app/installations/{installation_id}/access_tokens", headers=headers,
        json={"repositories": [repo_name]},
    )
    assert narrowed.status_code == 201, narrowed.text
    assert narrowed.json()["repository_selection"] == "selected"
    assert [item["full_name"] for item in narrowed.json()["repositories"]] == [f"{owner}/{repo_name}"]


async def _second_repo(client, test_token):
    resp = await client.post(f"{API}/user/repos", json={"name": "other-repo", "auto_init": True},
                             headers=auth_headers(test_token))
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_repository_ids_narrow_the_token_like_names(client, admin_token, test_token, test_repo_with_init):
    owner, repo_name, repo = test_repo_with_init
    await _second_repo(client, test_token)
    installation_id, headers = await _org_wide_app(client, admin_token, owner, "1103")
    narrowed = await client.post(
        f"{API}/app/installations/{installation_id}/access_tokens", headers=headers,
        json={"repository_ids": [repo["id"]]},
    )
    assert narrowed.status_code == 201, narrowed.text
    assert narrowed.json()["repository_selection"] == "selected"
    assert [item["full_name"] for item in narrowed.json()["repositories"]] == [f"{owner}/{repo_name}"]
    # The token reaches only that repository.
    listed = await client.get(f"{API}/installation/repositories",
                              headers={"Authorization": f"token {narrowed.json()['token']}"})
    assert listed.status_code == 200, listed.text
    assert [item["full_name"] for item in listed.json()["repositories"]] == [f"{owner}/{repo_name}"]


@pytest.mark.asyncio
async def test_names_and_ids_together_give_their_union(client, admin_token, test_token, test_repo_with_init):
    owner, repo_name, _repo = test_repo_with_init
    other = await _second_repo(client, test_token)
    installation_id, headers = await _org_wide_app(client, admin_token, owner, "1104")
    both = await client.post(
        f"{API}/app/installations/{installation_id}/access_tokens", headers=headers,
        json={"repositories": [repo_name], "repository_ids": [other["id"]]},
    )
    assert both.status_code == 201, both.text
    assert sorted(item["full_name"] for item in both.json()["repositories"]) == sorted(
        [f"{owner}/{repo_name}", f"{owner}/other-repo"])


@pytest.mark.asyncio
async def test_an_unknown_repository_id_is_refused(client, admin_token, test_repo_with_init):
    owner, _repo_name, _repo = test_repo_with_init
    installation_id, headers = await _org_wide_app(client, admin_token, owner, "1105")
    for body in ({"repository_ids": [987654321]}, {"repository_ids": ["1"]}):
        refused = await client.post(
            f"{API}/app/installations/{installation_id}/access_tokens", headers=headers, json=body)
        assert refused.status_code == 422, (body, refused.text)


@pytest.mark.asyncio
async def test_a_malformed_narrowing_is_refused_not_widened(client, admin_token, test_repo_with_init):
    owner, _repo_name, _repo = test_repo_with_init
    installation_id, headers = await _org_wide_app(client, admin_token, owner, "1106")
    for body in (
        {"repository_ids": ""}, {"repository_ids": 0}, {"repository_ids": False}, {"repository_ids": {}},
        {"repositories": ""}, {"repository_ids": [0]}, {"repository_ids": [2**70]},
    ):
        refused = await client.post(
            f"{API}/app/installations/{installation_id}/access_tokens", headers=headers, json=body)
        assert refused.status_code == 422, (body, refused.text)


@pytest.mark.asyncio
async def test_an_unnarrowed_token_on_an_all_installation_stays_all(client, admin_token, test_repo_with_init):
    owner, _repo_name, _repo = test_repo_with_init
    installation_id, headers = await _org_wide_app(client, admin_token, owner, "1102")
    whole = await client.post(f"{API}/app/installations/{installation_id}/access_tokens", headers=headers, json={})
    assert whole.status_code == 201, whole.text
    assert whole.json()["repository_selection"] == "all"

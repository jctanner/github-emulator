"""A runner sees and changes only the pool agents in its own scope.

On GitHub a runner belongs to the repository, organization or enterprise it
registered with, and its credential lists, updates and removes only the
agents there.
"""

import pytest

from tests.conftest import API, auth_headers


async def _registration_token(client, path, token):
    resp = await client.post(f"{API}{path}/actions/runners/registration-token", headers=auth_headers(token))
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["token"]


async def _config_sh_register(client, reg_token, name):
    """What config.sh does: runner-registration, then the agent add."""
    resp = await client.post(
        f"{API}/actions/runner-registration",
        json={"url": "http://test/x", "runner_event": "register"},
        headers={"Authorization": f"RemoteAuth {reg_token}"},
    )
    assert resp.status_code == 200, resp.text
    credential = resp.json()["token"]
    added = await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={"name": name, "labels": [{"name": "self-hosted"}]},
        headers={"Authorization": f"Bearer {credential}"},
    )
    assert added.status_code == 200, added.text
    return credential, added.json()["id"]


@pytest.mark.asyncio
async def test_a_runner_sees_and_changes_only_runners_in_its_own_scope(client, test_token):
    h = auth_headers(test_token)
    for name in ("scope-a", "scope-b"):
        resp = await client.post(f"{API}/user/repos", json={"name": name}, headers=h)
        assert resp.status_code == 201, resp.text
    a_cred, a_id = await _config_sh_register(
        client, await _registration_token(client, "/repos/testuser/scope-a", test_token), "same-name")
    b_cred, b_id = await _config_sh_register(
        client, await _registration_token(client, "/repos/testuser/scope-b", test_token), "same-name")
    a_headers = {"Authorization": f"Bearer {a_cred}"}

    found = await client.get(
        "/_apis/distributedtask/pools/1/agents", params={"agentName": "same-name"}, headers=a_headers)
    assert found.status_code == 200
    assert [agent["id"] for agent in found.json()["value"]] == [a_id]

    replaced = await client.put(
        f"/_apis/distributedtask/pools/1/agents/{b_id}", json={"name": "renamed"}, headers=a_headers)
    assert replaced.status_code == 404
    removed = await client.delete(f"/_apis/distributedtask/pools/1/agents/{b_id}", headers=a_headers)
    assert removed.status_code == 404

    b_view = await client.get(
        "/_apis/distributedtask/pools/1/agents", params={"agentName": "same-name"},
        headers={"Authorization": f"Bearer {b_cred}"})
    assert [agent["id"] for agent in b_view.json()["value"]] == [b_id]

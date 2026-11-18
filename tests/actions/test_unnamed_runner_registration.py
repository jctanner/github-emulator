"""Runners that register without a name each get their own row and token.

actions/runner's config.sh posts only ``url`` and ``runner_event`` to
/actions/runner-registration; the runner's name comes later, with the agent
add. Registering several runners before any of them had added its agent
gave them all the placeholder name, each registration re-keyed the same row,
and every runner but the last got 401 on its next call.
"""

import pytest

from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_unnamed_registrations_keep_their_own_tokens(client, admin_token):
    tokens = []
    for _ in range(4):
        reg = await client.post(
            f"{API}/enterprises/breadboard/actions/runners/registration-token",
            headers=auth_headers(admin_token),
        )
        assert reg.status_code == 200, reg.text
        resp = await client.post(
            f"{API}/actions/runner-registration",
            json={"url": "http://test/enterprises/breadboard", "runner_event": "register"},
            headers={"Authorization": f"RemoteAuth {reg.json()['token']}"},
        )
        assert resp.status_code == 200, resp.text
        tokens.append(resp.json()["token"])

    for token in tokens:
        pools = await client.get(
            "/enterprises/breadboard/_apis/distributedtask/pools",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert pools.status_code == 200, pools.text


@pytest.mark.asyncio
async def test_a_named_registration_still_reuses_its_row(client, admin_token):
    ids = []
    for _ in range(2):
        reg = await client.post(
            f"{API}/enterprises/breadboard/actions/runners/registration-token",
            headers=auth_headers(admin_token),
        )
        resp = await client.post(
            f"{API}/actions/runner-registration",
            json={"registrationToken": reg.json()["token"], "agentName": "pod-runner"},
        )
        assert resp.status_code == 200, resp.text
        ids.append(resp.json()["runner"]["id"])
    assert ids[0] == ids[1]


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
async def test_one_registration_token_registers_several_runners(client, admin_token):
    reg_token = await _registration_token(client, "/enterprises/breadboard", admin_token)
    credentials = [
        (await _config_sh_register(client, reg_token, f"shared-{i}"))[0] for i in range(3)
    ]
    assert len(set(credentials)) == 3
    for credential in credentials:
        pools = await client.get(
            "/enterprises/breadboard/_apis/distributedtask/pools",
            headers={"Authorization": f"Bearer {credential}"},
        )
        assert pools.status_code == 200, pools.text


@pytest.mark.asyncio
async def test_a_repository_token_registers_several_runners_on_every_route(client, test_token):
    resp = await client.post(f"{API}/user/repos", json={"name": "many-runners"}, headers=auth_headers(test_token))
    assert resp.status_code == 201, resp.text
    reg_token = await _registration_token(client, "/repos/testuser/many-runners", test_token)
    for i in range(2):
        pool = await client.post(
            "/_apis/distributedtask/pools/1/agents",
            json={"token": reg_token, "name": f"pool-{i}"},
        )
        assert pool.status_code == 200, pool.text
        pipelines = await client.post(
            "/_services/pipelines/testuser/many-runners/_apis/pipelines/runs/register",
            json={"token": reg_token, "name": f"pipelines-{i}"},
        )
        assert pipelines.status_code == 200, pipelines.text

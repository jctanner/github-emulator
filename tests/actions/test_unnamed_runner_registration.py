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


async def _remove_token(client, token):
    resp = await client.post(f"{API}/enterprises/breadboard/actions/runners/remove-token", headers=auth_headers(token))
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["token"]


@pytest.mark.asyncio
async def test_a_remove_token_serves_remove_and_registers_no_runner(client, admin_token):
    remove_token = await _remove_token(client, admin_token)
    for body in ({"url": "http://test/x", "runner_event": "register"}, {"url": "http://test/x"}):
        refused = await client.post(
            f"{API}/actions/runner-registration", json=body,
            headers={"Authorization": f"RemoteAuth {remove_token}"},
        )
        assert refused.status_code == 401, refused.text
    for path in ("/_apis/distributedtask/pools/1/agents",
                 "/_services/pipelines/enterprises/breadboard/_apis/pipelines/runs/register"):
        refused = await client.post(path, json={"token": remove_token, "name": "nope"})
        assert refused.status_code == 401, refused.text
    remove = await client.post(
        f"{API}/actions/runner-registration", json={"url": "http://test/x", "runner_event": "remove"},
        headers={"Authorization": f"RemoteAuth {remove_token}"},
    )
    assert remove.status_code == 200, remove.text


@pytest.mark.asyncio
async def test_config_sh_remove_leaves_no_runner_behind(client, admin_token):
    """config.sh remove: runner-registration with the remove token, then delete the agent."""
    listed = f"{API}/enterprises/breadboard/actions/runners"
    before = (await client.get(listed, headers=auth_headers(admin_token))).json()["total_count"]
    reg_token = await _registration_token(client, "/enterprises/breadboard", admin_token)
    for i in range(2):
        _, agent_id = await _config_sh_register(client, reg_token, f"short-lived-{i}")
        remove = await client.post(
            f"{API}/actions/runner-registration", json={"url": "http://test/x", "runner_event": "remove"},
            headers={"Authorization": f"RemoteAuth {await _remove_token(client, admin_token)}"},
        )
        assert remove.status_code == 200, remove.text
        deleted = await client.delete(
            f"/_apis/distributedtask/pools/1/agents/{agent_id}",
            headers={"Authorization": f"Bearer {remove.json()['token']}"},
        )
        assert deleted.status_code == 200, deleted.text
    after = (await client.get(listed, headers=auth_headers(admin_token))).json()["total_count"]
    assert after == before


@pytest.mark.asyncio
async def test_expired_registration_tokens_are_pruned(client, db_session, admin_token):
    from datetime import datetime, timedelta

    from sqlalchemy import select

    from app.models.actions import EnterpriseRunnerRegistrationToken

    db_session.add(EnterpriseRunnerRegistrationToken(
        token="AAAAAAexpired", enterprise_slug="breadboard",
        expires_at=datetime.utcnow() - timedelta(minutes=1),
    ))
    await db_session.commit()
    live = await _registration_token(client, "/enterprises/breadboard", admin_token)
    tokens = (await db_session.execute(select(EnterpriseRunnerRegistrationToken.token))).scalars().all()
    assert "AAAAAAexpired" not in tokens
    assert live in tokens

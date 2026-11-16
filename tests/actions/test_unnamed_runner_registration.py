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

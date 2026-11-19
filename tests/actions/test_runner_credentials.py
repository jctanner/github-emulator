"""The runner's registered key guards the token exchange and deregistration.

actions/runner registers an RSA public key and, on the OAuth token exchange,
proves itself with a JWT client assertion signed by the private half. These
tests drive the same HTTP calls config.sh and run.sh make.
"""

import base64
import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import select

from app.models.actions import Runner
from app.services.runner_credentials import (
    CLIENT_ASSERTION_TYPE,
    InvalidClientAssertion,
    public_key_pem_from_agent,
    verify_client_assertion,
)
from tests.actions.test_execution import executable_workflow  # noqa: F401 (fixture)
from tests.conftest import API, auth_headers


def _new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _public_key_json(key: rsa.RSAPrivateKey) -> dict:
    """TaskAgentPublicKey as the runner serializes it: base64 byte arrays."""
    numbers = key.public_key().public_numbers()

    def as_base64(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.b64encode(raw).decode("ascii")

    return {"exponent": as_base64(numbers.e), "modulus": as_base64(numbers.n)}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _assertion(
    key: rsa.RSAPrivateKey,
    client_id: str,
    audience: str,
    *,
    expires_in: int = 300,
    subject: str | None = None,
) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"typ": "JWT", "alg": "RS256"}).encode())
    payload = _b64url(json.dumps({
        "iss": client_id,
        "sub": subject or client_id,
        "aud": audience,
        "jti": str(uuid.uuid4()),
        "nbf": now - 5,
        "iat": now,
        "exp": now + expires_in,
    }).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


async def _configure_runner(client, test_token, name: str, key) -> dict:
    """config.sh: registration token, broker registration, then the agent
    registration that carries the public key."""
    token_resp = await client.post(
        f"{API}/repos/testuser/actions-exec-repo/actions/runners/registration-token",
        headers=auth_headers(test_token),
    )
    assert token_resp.status_code == 200
    broker = await client.post(
        f"{API}/actions/runner-registration",
        json={"url": "http://testserver/testuser/actions-exec-repo", "runner_event": "register"},
        headers={"Authorization": f"RemoteAuth {token_resp.json()['token']}"},
    )
    assert broker.status_code == 200, broker.text
    agent = await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={
            "name": name,
            "labels": [{"name": "self-hosted"}, {"name": "linux"}],
            "osDescription": "Linux",
            "authorization": {"publicKey": _public_key_json(key)},
        },
        headers={"Authorization": f"Bearer {broker.json()['token']}"},
    )
    assert agent.status_code == 200, agent.text
    return agent.json()


async def _exchange(client, runner_id: int, assertion: str | None):
    form = {"grant_type": "client_credentials"}
    if assertion is not None:
        form["client_assertion_type"] = CLIENT_ASSERTION_TYPE
        form["client_assertion"] = assertion
    return await client.post(
        f"/_apis/oauth2/token?runner_id={runner_id}",
        data=form,
    )


def _assert_invalid_client(resp) -> None:
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "invalid_client"


@pytest.mark.asyncio
async def test_registered_key_signs_the_token_exchange_end_to_end(
    client, executable_workflow, test_token,  # noqa: F811
):
    key = _new_key()
    agent = await _configure_runner(client, test_token, "keyed-runner", key)
    client_id = agent["authorization"]["clientId"]
    audience = agent["authorization"]["authorizationUrl"]

    exchanged = await _exchange(client, agent["id"], _assertion(key, client_id, audience))
    assert exchanged.status_code == 200, exchanged.text
    runner_headers = {"Authorization": f"Bearer {exchanged.json()['access_token']}"}

    session = await client.post(
        "/_apis/distributedtask/pools/1/sessions",
        json={"agent": {"id": agent["id"]}},
        headers=runner_headers,
    )
    assert session.status_code == 200
    session_id = session.json()["sessionId"]
    message = await client.get(
        f"/_apis/distributedtask/pools/1/sessions/{session_id}/messages",
        params={"lastMessageId": 0},
        headers=runner_headers,
    )
    assert message.status_code == 200
    assert message.json()["messageType"] == "PipelineAgentJobRequest"

    # config.sh remove: the runner deletes its own agent registration.
    ended = await client.delete(
        f"/_apis/distributedtask/pools/1/sessions/{session_id}",
        headers=runner_headers,
    )
    assert ended.status_code == 200
    removed = await client.delete(
        f"/_apis/distributedtask/pools/1/agents/{agent['id']}",
        headers=runner_headers,
    )
    assert removed.status_code == 200


@pytest.mark.asyncio
async def test_token_exchange_refuses_assertions_that_do_not_prove_the_key(
    client, executable_workflow, test_token,  # noqa: F811
):
    key = _new_key()
    agent = await _configure_runner(client, test_token, "first-runner", key)
    client_id = agent["authorization"]["clientId"]
    audience = agent["authorization"]["authorizationUrl"]
    other_key = _new_key()
    other = await _configure_runner(client, test_token, "second-runner", other_key)

    # Signed with a key that was never registered for this runner.
    _assert_invalid_client(
        await _exchange(client, agent["id"], _assertion(_new_key(), client_id, audience))
    )
    # Expired.
    _assert_invalid_client(
        await _exchange(
            client, agent["id"], _assertion(key, client_id, audience, expires_in=-3600)
        )
    )
    # Another runner's id: its own valid assertion, presented for this runner.
    other_assertion = _assertion(
        other_key,
        other["authorization"]["clientId"],
        other["authorization"]["authorizationUrl"],
    )
    _assert_invalid_client(await _exchange(client, agent["id"], other_assertion))
    # Claiming this runner's client id, but signed with the other runner's key.
    _assert_invalid_client(
        await _exchange(client, agent["id"], _assertion(other_key, client_id, audience))
    )
    # The right key and id, for a different audience.
    _assert_invalid_client(
        await _exchange(
            client, agent["id"], _assertion(key, client_id, "https://elsewhere.example/token")
        )
    )
    # No assertion at all.
    _assert_invalid_client(await _exchange(client, agent["id"], None))
    # Garbage.
    _assert_invalid_client(await _exchange(client, agent["id"], "not.a.jwt"))

    # None of the refusals disturbed the runner: its real assertion still works.
    accepted = await _exchange(client, agent["id"], _assertion(key, client_id, audience))
    assert accepted.status_code == 200


@pytest.mark.asyncio
async def test_runner_without_a_registered_key_cannot_exchange(
    client, db_session, executable_workflow, test_token,  # noqa: F811
):
    token_resp = await client.post(
        f"{API}/repos/testuser/actions-exec-repo/actions/runners/registration-token",
        headers=auth_headers(test_token),
    )
    registered = await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={"token": token_resp.json()["token"], "name": "keyless-runner"},
    )
    assert registered.status_code == 200
    runner = (await db_session.execute(
        select(Runner).where(Runner.id == registered.json()["id"])
    )).scalar_one()
    assert runner.public_key is None

    key = _new_key()
    authorization = registered.json()["Authorization"]
    _assert_invalid_client(await _exchange(
        client,
        runner.id,
        _assertion(key, authorization["clientId"], authorization["authorizationUrl"]),
    ))


@pytest.mark.asyncio
async def test_replacing_a_runner_takes_the_new_key(
    client, executable_workflow, test_token,  # noqa: F811
):
    old_key = _new_key()
    agent = await _configure_runner(client, test_token, "replaced-runner", old_key)
    client_id = agent["authorization"]["clientId"]
    audience = agent["authorization"]["authorizationUrl"]

    # config.sh --replace: a broker identity updates the existing agent.
    token_resp = await client.post(
        f"{API}/repos/testuser/actions-exec-repo/actions/runners/registration-token",
        headers=auth_headers(test_token),
    )
    broker = await client.post(
        f"{API}/actions/runner-registration",
        json={"url": "http://testserver/testuser/actions-exec-repo", "runner_event": "register"},
        headers={"Authorization": f"RemoteAuth {token_resp.json()['token']}"},
    )
    new_key = _new_key()
    updated = await client.put(
        f"/_apis/distributedtask/pools/1/agents/{agent['id']}",
        json={
            "name": "replaced-runner",
            "authorization": {"publicKey": _public_key_json(new_key)},
        },
        headers={"Authorization": f"Bearer {broker.json()['token']}"},
    )
    assert updated.status_code == 200

    _assert_invalid_client(
        await _exchange(client, agent["id"], _assertion(old_key, client_id, audience))
    )
    accepted = await _exchange(client, agent["id"], _assertion(new_key, client_id, audience))
    assert accepted.status_code == 200


async def _pipelines_runner(client, path: str, token: str, name: str) -> dict:
    resp = await client.post(
        f"/_services/pipelines/{path}/_apis/pipelines/runs/register",
        json={"token": token, "name": name},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_repository_pipelines_deregister_requires_the_runners_own_token(
    client, executable_workflow, test_token,  # noqa: F811
):
    async def registration_token() -> str:
        resp = await client.post(
            f"{API}/repos/testuser/actions-exec-repo/actions/runners/registration-token",
            headers=auth_headers(test_token),
        )
        return resp.json()["token"]

    path = "testuser/actions-exec-repo"
    runner = await _pipelines_runner(client, path, await registration_token(), "pipe-a")
    other = await _pipelines_runner(client, path, await registration_token(), "pipe-b")
    url = f"/_services/pipelines/{path}/_apis/pipelines/runs/{runner['id']}"

    assert (await client.delete(url)).status_code == 401
    assert (await client.delete(
        url, headers={"Authorization": f"Bearer {other['token']}"}
    )).status_code == 403
    assert (await client.delete(
        url, headers={"Authorization": f"Bearer {runner['token']}"}
    )).status_code == 200


@pytest.mark.asyncio
async def test_enterprise_pipelines_deregister_requires_the_runners_own_token(
    client, admin_token,
):
    async def registration_token() -> str:
        resp = await client.post(
            f"{API}/enterprises/breadboard/actions/runners/registration-token",
            headers=auth_headers(admin_token),
        )
        return resp.json()["token"]

    path = "enterprises/breadboard"
    runner = await _pipelines_runner(client, path, await registration_token(), "ent-a")
    other = await _pipelines_runner(client, path, await registration_token(), "ent-b")
    url = f"/_services/pipelines/{path}/_apis/pipelines/runs/{runner['id']}"

    assert (await client.delete(url)).status_code == 401
    assert (await client.delete(
        url, headers={"Authorization": f"Bearer {other['token']}"}
    )).status_code == 403
    assert (await client.delete(
        url, headers={"Authorization": f"Bearer {runner['token']}"}
    )).status_code == 200


def test_public_key_is_read_from_the_agent_in_either_casing():
    key = _new_key()
    lower = public_key_pem_from_agent({"authorization": {"publicKey": _public_key_json(key)}})
    pascal = {k.capitalize(): v for k, v in _public_key_json(key).items()}
    upper = public_key_pem_from_agent({"Authorization": {"PublicKey": pascal}})
    assert lower is not None and lower == upper
    assert public_key_pem_from_agent({}) is None
    assert public_key_pem_from_agent({"authorization": {"publicKey": {"exponent": "!", "modulus": "?"}}}) is None


def test_assertion_without_expiry_is_refused():
    key = _new_key()
    pem = public_key_pem_from_agent({"authorization": {"publicKey": _public_key_json(key)}})
    header = _b64url(json.dumps({"alg": "RS256"}).encode())
    payload = _b64url(json.dumps({"iss": "c", "sub": "c", "aud": "a"}).encode())
    signature = key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
    with pytest.raises(InvalidClientAssertion):
        verify_client_assertion(
            f"{header}.{payload}.{_b64url(signature)}",
            public_key_pem=pem,
            client_id="c",
            audiences={"a"},
        )

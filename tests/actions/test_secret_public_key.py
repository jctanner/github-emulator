"""Actions secrets are libsodium sealed boxes, as they are on GitHub.

A client fetches the repository public key, seals the value against it, and
uploads base64 ciphertext with the key id. Without the public-key endpoint the
client gets a 404 naming the endpoint rather than the secret, which is how the
Fullsend CLI failed here.
"""

import pytest
from nacl.encoding import Base64Encoder
from nacl.public import PrivateKey, PublicKey, SealedBox

from app.models.actions import Secret
from sqlalchemy import select
from tests.conftest import API, auth_headers


async def _public_key(client, token, owner="testuser", repo="init-repo"):
    resp = await client.get(
        f"{API}/repos/{owner}/{repo}/actions/secrets/public-key",
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _seal(public_key_b64: str, plaintext: str) -> str:
    box = SealedBox(PublicKey(Base64Encoder.decode(public_key_b64.encode())))
    return Base64Encoder.encode(box.encrypt(plaintext.encode())).decode()


@pytest.mark.asyncio
async def test_public_key_is_served_and_shaped_like_githubs(client, test_token, test_repo_with_init):
    body = await _public_key(client, test_token)
    assert set(body) == {"key_id", "key"}
    assert Base64Encoder.decode(body["key"].encode()), "key must be base64"


@pytest.mark.asyncio
async def test_public_key_is_stable_across_calls(client, test_token, test_repo_with_init):
    # Derived rather than generated, so a restart or a data wipe does not
    # invalidate a key a client already sealed against.
    first = await _public_key(client, test_token)
    second = await _public_key(client, test_token)
    assert first == second


@pytest.mark.asyncio
async def test_a_sealed_secret_round_trips(client, db_session, test_token, test_repo_with_init):
    key = await _public_key(client, test_token)
    resp = await client.put(
        f"{API}/repos/testuser/init-repo/actions/secrets/MY_SECRET",
        headers=auth_headers(test_token),
        json={"encrypted_value": _seal(key["key"], "hunter2"), "key_id": key["key_id"]},
    )
    assert resp.status_code == 201, resp.text
    stored = (await db_session.execute(select(Secret).where(Secret.name == "MY_SECRET"))).scalar_one()
    assert stored.value == "hunter2", "the emulator must open the sealed box"


@pytest.mark.asyncio
async def test_the_value_is_never_returned_by_the_api(client, test_token, test_repo_with_init):
    key = await _public_key(client, test_token)
    await client.put(
        f"{API}/repos/testuser/init-repo/actions/secrets/SHOWN",
        headers=auth_headers(test_token),
        json={"encrypted_value": _seal(key["key"], "topsecret"), "key_id": key["key_id"]},
    )
    got = await client.get(
        f"{API}/repos/testuser/init-repo/actions/secrets/SHOWN", headers=auth_headers(test_token)
    )
    assert got.status_code == 200
    assert "topsecret" not in got.text
    listed = await client.get(
        f"{API}/repos/testuser/init-repo/actions/secrets", headers=auth_headers(test_token)
    )
    assert "topsecret" not in listed.text


@pytest.mark.asyncio
async def test_sealing_against_the_wrong_key_is_refused(client, test_token, test_repo_with_init):
    # A box sealed to someone else's key must not be stored as if it worked.
    other = PrivateKey.generate().public_key.encode(Base64Encoder).decode()
    resp = await client.put(
        f"{API}/repos/testuser/init-repo/actions/secrets/WRONG",
        headers=auth_headers(test_token),
        json={"encrypted_value": _seal(other, "nope")},
    )
    assert resp.status_code == 422
    assert "public-key" in resp.json()["message"], "the error should say how to get the right key"


@pytest.mark.asyncio
async def test_a_non_sealed_payload_is_refused_clearly(client, test_token, test_repo_with_init):
    resp = await client.put(
        f"{API}/repos/testuser/init-repo/actions/secrets/GARBAGE",
        headers=auth_headers(test_token),
        json={"encrypted_value": "!!! not base64 !!!"},
    )
    assert resp.status_code == 422
    assert "base64" in resp.json()["message"]


@pytest.mark.asyncio
async def test_public_key_is_not_treated_as_a_secret_name(client, test_token, test_repo_with_init):
    # Route ordering: /secrets/{secret_name} would otherwise capture it.
    resp = await client.get(
        f"{API}/repos/testuser/init-repo/actions/secrets/public-key", headers=auth_headers(test_token)
    )
    assert resp.status_code == 200
    assert "key_id" in resp.json()

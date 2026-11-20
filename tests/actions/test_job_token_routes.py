"""Job-server routes accept only a job token the emulator issued.

The upstream runner authenticates its timeline, log and action-download calls
with the job's own token. Each route must check that token's signature,
issuer and lifetime, not just read the job id out of its payload.
"""

import base64
import hmac
import json
import time

import pytest

from app.config import settings
from tests.actions.test_uses_steps import SHA, _claim_job, uses_workflow  # noqa: F401


def _b64(value: dict | list) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign(payload: dict | list, key: str) -> str:
    signing_input = f"{_b64({'typ': 'JWT', 'alg': 'HS256'})}.{_b64(payload)}"
    digest = hmac.digest(key.encode(), signing_input.encode(), "sha256")
    signature = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"{signing_input}.{signature}"


def _claims_of(token: str) -> dict:
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def _bad_tokens(job_token: str) -> dict[str, str]:
    claims = _claims_of(job_token)
    now = int(time.time())
    return {
        "bad signature": _sign(claims, "not-the-secret-key"),
        "unsigned": job_token.rsplit(".", 1)[0] + ".",
        "wrong issuer": _sign({**claims, "iss": "someone-else"}, settings.SECRET_KEY),
        "expired": _sign({**claims, "nbf": now - 7200, "iat": now - 7200, "exp": now - 3600},
                         settings.SECRET_KEY),
        "a header that is not an object": f"{_b64([])}.{job_token.split('.')[1]}.x",
        "a payload that is not an object": _sign([], settings.SECRET_KEY),
    }


def _requests(body: dict) -> dict[str, tuple[str, str, dict]]:
    plan = body["plan"]["planId"]
    timeline = body["timeline"]["id"]
    return {
        "timeline record feed": (
            "POST",
            f"/_apis/distributedtask/hubs/Build/plans/{plan}/timelines/{timeline}/records/{timeline}/feed",
            {"json": {"value": ["line"], "count": 1}},
        ),
        "timeline records": (
            "PATCH",
            f"/_apis/distributedtask/hubs/Build/plans/{plan}/timelines/{timeline}/records",
            {"json": {"value": [], "count": 0}},
        ),
        "create log": (
            "POST", f"/_apis/distributedtask/hubs/Build/plans/{plan}/logs",
            {"json": {"path": "logs/1"}},
        ),
        "append log": (
            "POST", f"/_apis/distributedtask/hubs/Build/plans/{plan}/logs/1",
            {"content": b"line\n", "headers": {"content-type": "application/octet-stream"}},
        ),
        "action download info": (
            "POST", f"/_apis/distributedtask/hubs/Build/plans/{plan}/actionsdownloadinfo",
            {"json": {"actions": []}},
        ),
    }


async def _send(client, method, path, kwargs, token):
    headers = {**kwargs.get("headers", {}), "Authorization": f"Bearer {token}"}
    rest = {k: v for k, v in kwargs.items() if k != "headers"}
    return await client.request(method, path, headers=headers, **rest)


@pytest.mark.asyncio
async def test_job_server_routes_refuse_tokens_the_emulator_did_not_issue(
    client, uses_workflow, test_token  # noqa: F811
):
    body = await _claim_job(client, test_token)
    job_token = body["variables"]["system.github.token"]["Value"]
    for route, (method, path, kwargs) in _requests(body).items():
        for kind, token in _bad_tokens(job_token).items():
            resp = await _send(client, method, path, kwargs, token)
            assert resp.status_code == 401, f"{route} accepted a token with {kind}"
        resp = await _send(client, method, path, kwargs, job_token)
        assert resp.status_code != 401, f"{route} refused the job's own token: {resp.text}"


@pytest.mark.asyncio
async def test_action_archive_refuses_tokens_the_emulator_did_not_issue(
    client, uses_workflow, test_token  # noqa: F811
):
    body = await _claim_job(client, test_token)
    job_token = body["variables"]["system.github.token"]["Value"]
    path = f"/_apis/distributedtask/actions/archives/actions/checkout/{SHA}/archive.tar.gz"
    for kind, token in _bad_tokens(job_token).items():
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        resp = await client.get(path, headers={"Authorization": f"Basic {basic}"})
        assert resp.status_code == 401, f"archive accepted a token with {kind}"

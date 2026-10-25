"""Repository webhooks receive repository activity, as on GitHub."""

import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from app.models.repository import Repository
from app.services import webhook_service
from app.services.workflow_service import process_push_event
from tests.conftest import API, auth_headers

SECRET = "hook-secret"


async def _repo(client, token, name):
    resp = await client.post(
        f"{API}/user/repos", json={"name": name}, headers=auth_headers(token)
    )
    assert resp.status_code == 201


async def _hook(client, token, repo, events, **config):
    resp = await client.post(
        f"{API}/repos/testuser/{repo}/hooks",
        json={
            "config": {"url": f"http://receiver.test/{repo}", "secret": SECRET, **config},
            "events": events,
        },
        headers=auth_headers(token),
    )
    assert resp.status_code == 201
    return resp.json()


async def _settle(requests):
    """Wait for background deliveries; return them without the creation pings."""
    await webhook_service.wait_for_deliveries()
    return [r for r in requests if r.headers["x-github-event"] != "ping"]


def _signature_ok(request: httpx.Request) -> bool:
    expected = hmac.new(SECRET.encode(), request.content, hashlib.sha256).hexdigest()
    return request.headers["x-hub-signature-256"] == f"sha256={expected}"


@pytest.mark.asyncio
async def test_opening_an_issue_delivers_a_signed_issues_event(
    client, test_user, test_token, webhook_requests
):
    await _repo(client, test_token, "hooked")
    hook = await _hook(client, test_token, "hooked", ["issues"])
    resp = await client.post(
        f"{API}/repos/testuser/hooked/issues",
        json={"title": "deliver me"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201

    (request,) = await _settle(webhook_requests)
    assert str(request.url) == "http://receiver.test/hooked"
    assert request.headers["x-github-event"] == "issues"
    assert request.headers["x-github-hook-id"] == str(hook["id"])
    assert request.headers["content-type"] == "application/json"
    assert request.headers["x-github-delivery"]
    assert _signature_ok(request)
    payload = json.loads(request.content)
    assert payload["action"] == "opened"
    assert payload["issue"]["title"] == "deliver me"
    assert payload["repository"]["full_name"] == "testuser/hooked"
    assert payload["sender"]["login"] == "testuser"
    # Actions adds ref and after to its own copy; the hook's payload is GitHub's.
    assert "ref" not in payload and "after" not in payload

    deliveries = await client.get(
        f"{API}/repos/testuser/hooked/hooks/{hook['id']}/deliveries",
        headers=auth_headers(test_token),
    )
    events = [(d["event"], d["action"], d["status_code"], d["success"]) for d in deliveries.json()]
    assert ("issues", "opened", 202, True) in events
    got = await client.get(
        f"{API}/repos/testuser/hooked/hooks/{hook['id']}", headers=auth_headers(test_token)
    )
    assert got.json()["last_response"] == {"code": 202, "status": "active", "message": "OK"}


@pytest.mark.asyncio
async def test_hooks_receive_only_their_events(client, test_user, test_token, webhook_requests):
    await _repo(client, test_token, "filtered")
    await _hook(client, test_token, "filtered", ["push"])
    everything = await _hook(client, test_token, "filtered", ["*"])
    inactive = await _hook(client, test_token, "filtered", ["issues"])
    await client.patch(
        f"{API}/repos/testuser/filtered/hooks/{inactive['id']}",
        json={"active": False},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/repos/testuser/filtered/issues",
        json={"title": "only for star"},
        headers=auth_headers(test_token),
    )
    requests = await _settle(webhook_requests)
    assert [r.headers["x-github-hook-id"] for r in requests] == [str(everything["id"])]


@pytest.mark.asyncio
async def test_comments_and_pull_requests_use_github_event_names(
    client, test_user, test_token, webhook_requests
):
    await _repo(client, test_token, "names")
    await _hook(client, test_token, "names", ["*"])
    await client.post(
        f"{API}/repos/testuser/names/issues",
        json={"title": "discuss"},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/repos/testuser/names/issues/1/comments",
        json={"body": "hello"},
        headers=auth_headers(test_token),
    )
    resp = await client.post(
        f"{API}/repos/testuser/names/pulls",
        json={"title": "change", "head": "feature", "base": "main"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201

    requests = await _settle(webhook_requests)
    seen = [(r.headers["x-github-event"], json.loads(r.content).get("action")) for r in requests]
    assert ("issue_comment", "created") in seen
    # Actions runs pull_request_target for this activity; the webhook is
    # GitHub's pull_request event.
    assert ("pull_request", "opened") in seen
    assert all(event != "pull_request_target" for event, _ in seen)
    pr = next(json.loads(r.content) for r in requests if r.headers["x-github-event"] == "pull_request")
    assert pr["pull_request"]["title"] == "change"


@pytest.mark.asyncio
async def test_new_hooks_are_pinged_and_can_be_pinged_and_tested(
    client, test_user, test_token, test_repo_with_init, webhook_requests
):
    owner, repo, _ = test_repo_with_init
    hook = await _hook(client, test_token, repo, ["push"])
    await webhook_service.wait_for_deliveries()
    (ping,) = webhook_requests
    assert ping.headers["x-github-event"] == "ping"
    body = json.loads(ping.content)
    assert body["hook_id"] == hook["id"]
    assert body["zen"]
    assert body["hook"]["config"]["url"] == "http://receiver.test/init-repo"
    assert _signature_ok(ping)

    base = f"{API}/repos/{owner}/{repo}/hooks/{hook['id']}"
    assert (await client.post(f"{base}/pings", headers=auth_headers(test_token))).status_code == 204
    assert (await client.post(f"{base}/tests", headers=auth_headers(test_token))).status_code == 204
    await webhook_service.wait_for_deliveries()
    assert [r.headers["x-github-event"] for r in webhook_requests] == ["ping", "ping", "push"]
    push = json.loads(webhook_requests[2].content)
    assert push["ref"] == "refs/heads/main"
    assert len(push["after"]) == 40 and push["after"] != "0" * 40

    # Test sends nothing to a hook not subscribed to push, as on GitHub.
    issues_only = await _hook(client, test_token, repo, ["issues"])
    await client.post(
        f"{API}/repos/{owner}/{repo}/hooks/{issues_only['id']}/tests",
        headers=auth_headers(test_token),
    )
    await webhook_service.wait_for_deliveries()
    assert [r.headers["x-github-event"] for r in webhook_requests][3:] == ["ping"]
    missing = await client.post(f"{API}/repos/{owner}/{repo}/hooks/999/pings", headers=auth_headers(test_token))
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_pushes_deliver_push_and_branch_create_and_delete(
    client, db_session, test_user, test_token, test_repo_with_init, webhook_requests
):
    owner, repo, _ = test_repo_with_init
    await _hook(client, test_token, repo, ["push", "create", "delete"])
    repository = (
        await db_session.execute(select(Repository).where(Repository.full_name == f"{owner}/{repo}"))
    ).scalar_one()

    await process_push_event(db_session, repository, test_user, ref_name="topic", after_sha="b" * 40, created=True)
    await process_push_event(db_session, repository, test_user, ref_name="topic", before_sha="b" * 40, after_sha="0" * 40, deleted=True)

    requests = await _settle(webhook_requests)
    # Separate deliveries, so their order is not fixed, as on GitHub.
    seen = {}
    for r in requests:
        seen.setdefault(r.headers["x-github-event"], []).append(json.loads(r.content))
    assert sorted((event, len(bodies)) for event, bodies in seen.items()) == [
        ("create", 1), ("delete", 1), ("push", 2),
    ]
    push = next(p for p in seen["push"] if p["created"])
    assert push["ref"] == "refs/heads/topic" and push["after"] == "b" * 40
    assert push["repository"]["full_name"] == f"{owner}/{repo}"
    assert push["sender"]["login"] == "testuser"
    assert any(p["deleted"] for p in seen["push"])
    (create,) = seen["create"]
    assert (create["ref"], create["ref_type"], create["master_branch"]) == ("topic", "branch", "main")
    assert seen["delete"][0]["ref"] == "topic"
    assert all(_signature_ok(r) for r in requests)


@pytest.mark.asyncio
async def test_a_slow_receiver_does_not_delay_the_api(client, test_user, test_token, webhook_requests):
    async def slow(request):
        await asyncio.sleep(1.5)
        webhook_requests.append(request)
        return httpx.Response(200)

    await _repo(client, test_token, "slow")
    await _hook(client, test_token, "slow", ["issues"])
    await webhook_service.wait_for_deliveries()
    webhook_service.transport = httpx.MockTransport(slow)

    started = time.monotonic()
    resp = await client.post(
        f"{API}/repos/testuser/slow/issues", json={"title": "fast"}, headers=auth_headers(test_token)
    )
    elapsed = time.monotonic() - started
    assert resp.status_code == 201
    assert elapsed < 1.0, elapsed
    (request,) = await _settle(webhook_requests)
    assert json.loads(request.content)["issue"]["title"] == "fast"


@pytest.mark.asyncio
async def test_form_content_type_and_failed_deliveries(client, test_user, test_token, webhook_requests):
    def refuse(request):
        webhook_requests.append(request)
        return httpx.Response(500, text="nope")

    await _repo(client, test_token, "forms")
    hook = await _hook(client, test_token, "forms", ["issues"], content_type="form")
    await webhook_service.wait_for_deliveries()
    webhook_service.transport = httpx.MockTransport(refuse)
    await client.post(
        f"{API}/repos/testuser/forms/issues", json={"title": "as a form"}, headers=auth_headers(test_token)
    )
    (request,) = await _settle(webhook_requests)
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    payload = json.loads(parse_qs(request.content.decode())["payload"][0])
    assert payload["issue"]["title"] == "as a form"
    # The signature covers the body as sent.
    assert _signature_ok(request)

    got = await client.get(
        f"{API}/repos/testuser/forms/hooks/{hook['id']}", headers=auth_headers(test_token)
    )
    assert got.json()["last_response"] == {
        "code": 500, "status": "active", "message": "Invalid HTTP Response: 500",
    }
    deliveries = (await client.get(
        f"{API}/repos/testuser/forms/hooks/{hook['id']}/deliveries", headers=auth_headers(test_token)
    )).json()
    assert deliveries[0]["success"] is False and deliveries[0]["status_code"] == 500

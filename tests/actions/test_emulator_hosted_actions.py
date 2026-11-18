"""`uses: owner/repo@ref` resolves against repositories on this emulator first.

As on GHES, an action pushed to a repository here is used from here, and a
name the emulator does not hold goes upstream. GHES goes upstream only for a
repository it lacks; this emulator also does when the repository is here but
lacks the ref. Before, every reference was resolved upstream, so an action in
a repository here failed with "does not exist upstream".

As on GitHub, a job may use an action from a public repository, its own
repository, or a private repository with the same owner. Another owner's
private repository is not resolvable, and its name is not looked up upstream.
"""

import base64
import io
import tarfile

import pytest

from app.api import actions_distributed_task as dt
from app.services.action_download_service import UpstreamActionError
from tests.actions.test_uses_steps import SHA, _claim_job, uses_workflow  # noqa: F401
from tests.conftest import API, auth_headers


async def _download_info(client, body, token, name, ref):
    return await client.post(
        f"/_apis/distributedtask/hubs/Build/plans/{body['plan']['planId']}/actionsdownloadinfo",
        json={"actions": [{"nameWithOwner": name, "ref": ref}]},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_an_action_in_a_repository_here_is_served_from_here(
    client, uses_workflow, test_token, monkeypatch  # noqa: F811
):
    resp = await client.post(
        f"{API}/user/repos", json={"name": "my-action", "auto_init": True},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201

    async def no_upstream(name, ref):
        raise AssertionError(f"looked up {name}@{ref} upstream")
    monkeypatch.setattr(dt, "resolve_action_sha", no_upstream)

    body = await _claim_job(client, test_token)
    token = body["variables"]["system.github.token"]["Value"]
    resp = await _download_info(client, body, token, "testuser/my-action", "main")
    assert resp.status_code == 200, resp.text
    info = resp.json()["actions"]["testuser/my-action@main"]
    sha = info["resolvedSha"]
    assert len(sha) == 40

    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    path = info["tarballUrl"].split("://", 1)[1].split("/", 1)[1]
    archive = await client.get(f"/{path}", headers={"Authorization": f"Basic {basic}"})
    assert archive.status_code == 200, archive.text
    with tarfile.open(fileobj=io.BytesIO(archive.content), mode="r:gz") as tar:
        names = tar.getnames()
    assert f"testuser-my-action-{sha[:7]}/README.md" in names


@pytest.mark.asyncio
async def test_a_name_not_held_here_still_goes_upstream(
    client, uses_workflow, test_token, monkeypatch  # noqa: F811
):
    looked_up = []

    async def upstream(name, ref):
        looked_up.append((name, ref))
        return SHA
    monkeypatch.setattr(dt, "resolve_action_sha", upstream)

    body = await _claim_job(client, test_token)
    token = body["variables"]["system.github.token"]["Value"]
    resp = await _download_info(client, body, token, "actions/checkout", "v4")
    assert resp.status_code == 200
    assert looked_up == [("actions/checkout", "v4")]
    assert resp.json()["actions"]["actions/checkout@v4"]["resolvedSha"] == SHA


@pytest.mark.asyncio
async def test_a_ref_missing_here_falls_back_upstream(
    client, uses_workflow, test_token, monkeypatch  # noqa: F811
):
    await client.post(
        f"{API}/user/repos", json={"name": "my-action", "auto_init": True},
        headers=auth_headers(test_token),
    )

    async def upstream(name, ref):
        raise UpstreamActionError(f"{name}@{ref} does not exist upstream", not_found=True)
    monkeypatch.setattr(dt, "resolve_action_sha", upstream)

    body = await _claim_job(client, test_token)
    token = body["variables"]["system.github.token"]["Value"]
    resp = await _download_info(client, body, token, "testuser/my-action", "no-such-tag")
    assert resp.status_code == 404
    assert resp.json()["typeKey"] == "UnresolvableActionDownloadInfoException"


async def _private_repository(client, token, name):
    resp = await client.post(
        f"{API}/user/repos", json={"name": name, "auto_init": True, "private": True},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text


def _no_upstream(monkeypatch):
    async def resolve(name, ref):
        raise AssertionError(f"looked up {name}@{ref} upstream")

    async def fetch(name, sha, fmt):
        raise AssertionError(f"fetched {name}@{sha} upstream")
    monkeypatch.setattr(dt, "resolve_action_sha", resolve)
    monkeypatch.setattr(dt, "fetch_action_archive", fetch)


async def _archive(client, token, name, sha):
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return await client.get(
        f"/_apis/distributedtask/actions/archives/{name}/{sha}/archive.tar.gz",
        headers={"Authorization": f"Basic {basic}"},
    )


@pytest.mark.asyncio
async def test_another_owners_private_repository_is_not_an_action_here(
    client, uses_workflow, test_token, admin_token, monkeypatch  # noqa: F811
):
    await _private_repository(client, admin_token, "private-action")
    _no_upstream(monkeypatch)
    sha = (await client.get(
        f"{API}/repos/admin/private-action/commits/main", headers=auth_headers(admin_token),
    )).json()["sha"]
    assert len(sha) == 40

    body = await _claim_job(client, test_token)
    token = body["variables"]["system.github.token"]["Value"]
    resp = await _download_info(client, body, token, "admin/private-action", "main")
    assert resp.status_code == 404
    assert resp.json()["typeKey"] == "UnresolvableActionDownloadInfoException"

    archive = await _archive(client, token, "admin/private-action", sha)
    assert archive.status_code == 404


@pytest.mark.asyncio
async def test_a_private_repository_with_the_jobs_owner_is_served(
    client, uses_workflow, test_token, monkeypatch  # noqa: F811
):
    await _private_repository(client, test_token, "own-private-action")
    _no_upstream(monkeypatch)

    body = await _claim_job(client, test_token)
    token = body["variables"]["system.github.token"]["Value"]
    resp = await _download_info(client, body, token, "testuser/own-private-action", "main")
    assert resp.status_code == 200, resp.text
    sha = resp.json()["actions"]["testuser/own-private-action@main"]["resolvedSha"]

    archive = await _archive(client, token, "testuser/own-private-action", sha)
    assert archive.status_code == 200, archive.text

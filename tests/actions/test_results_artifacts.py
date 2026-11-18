"""The results service behind upload-artifact@v4+ and download-artifact@v4+.

Drives the protocol the way @actions/artifact does: Twirp calls with the
job's runtime token, the content through block-blob calls to the signed URL.
"""

import base64
import hashlib
import io
import json
import zipfile
from urllib.parse import urlsplit

import pytest

from tests.actions.test_uses_steps import _claim_job, uses_workflow  # noqa: F401
from tests.conftest import API, auth_headers

SERVICE = "/twirp/github.actions.results.api.v1.ArtifactService"


def _path(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}" if parts.query else parts.path


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _scope(token: str) -> tuple[str, str]:
    part = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    _, run, job = claims["scp"].split(":")
    return run, job


async def _job(client, test_token):
    body = await _claim_job(client, test_token)
    token = body["variables"]["system.github.token"]["Value"]
    run, job = _scope(token)
    ids = {"workflow_run_backend_id": run, "workflow_job_run_backend_id": job}
    return body, {"Authorization": f"Bearer {token}"}, ids


async def _twirp(client, method, headers, body):
    return await client.post(f"{SERVICE}/{method}", json=body, headers=headers)


async def _upload(client, headers, ids, name, content, blocks=2):
    resp = await _twirp(client, "CreateArtifact", headers, {**ids, "name": name, "version": 7})
    assert resp.status_code == 200, resp.text
    upload = _path(resp.json()["signed_upload_url"])
    size = -(-len(content) // blocks)
    block_ids = []
    for index in range(blocks):
        block_id = base64.b64encode(f"block-{index:04d}".encode()).decode()
        chunk = content[index * size:(index + 1) * size]
        put = await client.put(f"{upload}&comp=block&blockid={block_id}", content=chunk)
        assert put.status_code == 201, put.text
        block_ids.append(block_id)
    block_list = "<?xml version=\"1.0\" encoding=\"utf-8\"?><BlockList>" + "".join(
        f"<Latest>{block_id}</Latest>" for block_id in block_ids) + "</BlockList>"
    put = await client.put(f"{upload}&comp=blocklist", content=block_list.encode())
    assert put.status_code == 201, put.text
    return await _twirp(client, "FinalizeArtifact", headers, {
        **ids, "name": name, "size": str(len(content)),
        "hash": f"sha256:{hashlib.sha256(content).hexdigest()}",
    })


@pytest.mark.asyncio
async def test_the_job_message_carries_the_results_service(
    client, uses_workflow, test_token  # noqa: F811
):
    body, _headers, ids = await _job(client, test_token)
    # The runner reads it from the SystemVssConnection endpoint's data and
    # exports it as ACTIONS_RESULTS_URL.
    [connection] = [e for e in body["resources"]["endpoints"] if e["name"] == "SystemVssConnection"]
    assert connection["data"]["ResultsServiceUrl"].endswith("/")
    assert ids["workflow_job_run_backend_id"].isdigit()


@pytest.mark.asyncio
async def test_upload_list_and_download_round_trip(
    client, uses_workflow, test_token  # noqa: F811
):
    body, headers, ids = await _job(client, test_token)
    content = _zip({"result.json": b'{"ok": true}', "logs/agent.log": b"line\n" * 1000})

    finalize = await _upload(client, headers, ids, "fullsend-triage", content)
    assert finalize.status_code == 200, finalize.text
    assert finalize.json()["ok"] is True
    artifact_id = finalize.json()["artifact_id"]

    listed = await _twirp(client, "ListArtifacts", headers, {**ids, "name_filter": "fullsend-triage"})
    assert listed.status_code == 200
    [entry] = listed.json()["artifacts"]
    assert entry["database_id"] == artifact_id
    assert entry["size"] == str(len(b'{"ok": true}') + len(b"line\n" * 1000))

    signed = await _twirp(client, "GetSignedArtifactURL", headers, {**ids, "name": "fullsend-triage"})
    assert signed.status_code == 200
    download = await client.get(_path(signed.json()["signed_url"]))
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert archive.read("result.json") == b'{"ok": true}'
        assert archive.read("logs/agent.log") == b"line\n" * 1000

    # The REST API sees the same artifact.
    rest = await client.get(
        f"{API}/repos/testuser/uses-step-repo/actions/artifacts/{artifact_id}",
        headers=auth_headers(test_token),
    )
    assert rest.status_code == 200
    assert rest.json()["files"] == {"result.json": 12, "logs/agent.log": 5000}


@pytest.mark.asyncio
async def test_an_unarchived_upload_keeps_its_file(
    client, uses_workflow, test_token  # noqa: F811
):
    body, headers, ids = await _job(client, test_token)
    finalize = await _upload(client, headers, ids, "report.txt", b"plain text", blocks=1)
    assert finalize.status_code == 200, finalize.text
    rest = await client.get(
        f"{API}/repos/testuser/uses-step-repo/actions/artifacts/{finalize.json()['artifact_id']}",
        headers=auth_headers(test_token),
    )
    assert rest.json()["files"] == {"report.txt": 10}


@pytest.mark.asyncio
async def test_a_second_artifact_with_the_same_name_is_refused(
    client, uses_workflow, test_token  # noqa: F811
):
    body, headers, ids = await _job(client, test_token)
    assert (await _upload(client, headers, ids, "dup", _zip({"a": b"1"}))).status_code == 200
    again = await _twirp(client, "CreateArtifact", headers, {**ids, "name": "dup", "version": 7})
    assert again.status_code == 409
    assert again.json()["code"] == "already_exists"


@pytest.mark.asyncio
async def test_a_hash_mismatch_is_refused(client, uses_workflow, test_token):  # noqa: F811
    body, headers, ids = await _job(client, test_token)
    resp = await _twirp(client, "CreateArtifact", headers, {**ids, "name": "bad", "version": 7})
    upload = _path(resp.json()["signed_upload_url"])
    assert (await client.put(upload, content=b"content")).status_code == 201
    finalize = await _twirp(client, "FinalizeArtifact", headers, {
        **ids, "name": "bad", "size": "7", "hash": "sha256:" + "0" * 64,
    })
    assert finalize.status_code == 400


@pytest.mark.asyncio
async def test_the_service_needs_the_runtime_token(client, uses_workflow, test_token):  # noqa: F811
    body, headers, ids = await _job(client, test_token)
    no_token = await _twirp(client, "ListArtifacts", {}, ids)
    assert no_token.status_code == 401
    forged = headers["Authorization"].rsplit(".", 1)[0] + ".forged"
    bad = await _twirp(client, "ListArtifacts", {"Authorization": forged}, ids)
    assert bad.status_code == 401
    other_job = await _twirp(client, "ListArtifacts", headers, {
        **ids, "workflow_job_run_backend_id": "999999",
    })
    assert other_job.status_code == 403


@pytest.mark.asyncio
async def test_signed_urls_refuse_a_wrong_signature(client, uses_workflow, test_token):  # noqa: F811
    body, headers, ids = await _job(client, test_token)
    resp = await _twirp(client, "CreateArtifact", headers, {**ids, "name": "sig", "version": 7})
    upload = _path(resp.json()["signed_upload_url"])
    tampered = upload.split("?sig=")[0] + "?sig=" + "0" * 64
    assert (await client.put(tampered, content=b"x")).status_code == 403

    finalize = await _upload(client, headers, ids, "sig2", _zip({"a": b"1"}))
    artifact_id = finalize.json()["artifact_id"]
    resp = await client.get(f"/_apis/results/artifacts/{artifact_id}/download?sig=" + "0" * 64)
    assert resp.status_code == 403

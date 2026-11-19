"""The results service behind upload-artifact@v4+ and download-artifact@v4+.

Drives the protocol the way @actions/artifact does: Twirp calls with the
job's runtime token, the content through block-blob calls to the signed URL.
"""

import base64
import hashlib
import io
import json
import os
import re
import time
import types
import zipfile
from urllib.parse import urlsplit

import pytest

from app.api import actions_results
from app.config import settings
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


async def _upload(client, headers, ids, name, content, blocks=2, mime_type=None):
    create = {**ids, "name": name, "version": 7}
    if mime_type:
        create["mime_type"] = mime_type
    resp = await _twirp(client, "CreateArtifact", headers, create)
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
    # The size of the blob uploaded, as GitHub reports it; not the files' total.
    assert entry["size"] == str(len(content))

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
    wrong_signature = headers["Authorization"].rsplit(".", 1)[0] + ".unsigned"
    bad = await _twirp(client, "ListArtifacts", {"Authorization": wrong_signature}, ids)
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
    tampered = re.sub(r"sig=[0-9a-f]+", "sig=" + "0" * 64, upload)
    assert (await client.put(tampered, content=b"x")).status_code == 403

    finalize = await _upload(client, headers, ids, "sig2", _zip({"a": b"1"}))
    artifact_id = finalize.json()["artifact_id"]
    resp = await client.get(f"/_apis/results/artifacts/{artifact_id}/download?sig=" + "0" * 64)
    assert resp.status_code == 403
    signed = await _twirp(client, "GetSignedArtifactURL", headers, {**ids, "name": "sig2"})
    download = re.sub(r"sig=[0-9a-f]+", "sig=" + "0" * 64, _path(signed.json()["signed_url"]))
    assert (await client.get(download)).status_code == 403


async def _download(client, headers, ids, name):
    signed = await _twirp(client, "GetSignedArtifactURL", headers, {**ids, "name": name})
    assert signed.status_code == 200, signed.text
    return await client.get(_path(signed.json()["signed_url"]))


def _upload_dir(upload_url: str) -> str:
    key = urlsplit(upload_url).path.rsplit("/", 1)[1]
    return os.path.join(settings.DATA_DIR, "artifact-uploads", key)


@pytest.mark.asyncio
async def test_an_archived_download_is_the_uploaded_zip_byte_for_byte(
    client, uses_workflow, test_token  # noqa: F811
):
    body, headers, ids = await _job(client, test_token)
    # Stored, not deflated: a zip the server rebuilt would differ.
    content = _zip({"b.txt": b"second", "a.txt": b"first"})
    finalize = await _upload(client, headers, ids, "bundle", content, mime_type="application/zip")
    assert finalize.status_code == 200, finalize.text

    download = await _download(client, headers, ids, "bundle")
    assert download.status_code == 200
    assert download.content == content
    assert download.headers["content-type"] == "application/zip"
    assert 'filename="bundle.zip"' in download.headers["content-disposition"]


@pytest.mark.asyncio
async def test_an_unarchived_file_downloads_as_sent(
    client, uses_workflow, test_token  # noqa: F811
):
    body, headers, ids = await _job(client, test_token)
    finalize = await _upload(client, headers, ids, "report.txt", b"plain text", blocks=1, mime_type="text/plain")
    assert finalize.status_code == 200, finalize.text

    download = await _download(client, headers, ids, "report.txt")
    assert download.content == b"plain text"
    assert download.headers["content-type"].startswith("text/plain")
    assert 'filename="report.txt"' in download.headers["content-disposition"]


@pytest.mark.asyncio
async def test_an_unarchived_zip_file_stays_one_file(
    client, uses_workflow, test_token  # noqa: F811
):
    """`archive: false` on a .zip: the artifact is the file, not its members."""
    body, headers, ids = await _job(client, test_token)
    content = _zip({"inner.txt": b"inside"})
    finalize = await _upload(client, headers, ids, "release.zip", content, mime_type="application/zip")
    assert finalize.status_code == 200, finalize.text

    download = await _download(client, headers, ids, "release.zip")
    assert download.content == content
    assert 'filename="release.zip"' in download.headers["content-disposition"]
    rest = await client.get(
        f"{API}/repos/testuser/uses-step-repo/actions/artifacts/{finalize.json()['artifact_id']}",
        headers=auth_headers(test_token),
    )
    assert rest.json()["files"] == {"release.zip": len(content)}


@pytest.mark.asyncio
async def test_overwrite_deletes_then_creates_the_same_name(
    client, uses_workflow, test_token  # noqa: F811
):
    """upload-artifact's `overwrite: true`: DeleteArtifact, then CreateArtifact again."""
    body, headers, ids = await _job(client, test_token)
    first = await _upload(client, headers, ids, "out", _zip({"v": b"1"}))
    first_id = first.json()["artifact_id"]

    deleted = await _twirp(client, "DeleteArtifact", headers, {**ids, "name": "out"})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"ok": True, "artifact_id": first_id}
    listed = await _twirp(client, "ListArtifacts", headers, {**ids, "name_filter": "out"})
    assert listed.json()["artifacts"] == []
    rest = await client.get(
        f"{API}/repos/testuser/uses-step-repo/actions/artifacts/{first_id}",
        headers=auth_headers(test_token),
    )
    assert rest.status_code == 404

    second = await _upload(client, headers, ids, "out", _zip({"v": b"2"}))
    assert second.status_code == 200, second.text
    assert second.json()["artifact_id"] != first_id
    missing = await _twirp(client, "DeleteArtifact", headers, {**ids, "name": "gone"})
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_a_retried_create_replaces_the_abandoned_upload(
    client, uses_workflow, test_token  # noqa: F811
):
    body, headers, ids = await _job(client, test_token)
    abandoned = await _twirp(client, "CreateArtifact", headers, {**ids, "name": "retry", "version": 7})
    abandoned_url = _path(abandoned.json()["signed_upload_url"])

    content = _zip({"r": b"retried"})
    finalize = await _upload(client, headers, ids, "retry", content)
    assert finalize.status_code == 200, finalize.text
    assert (await _download(client, headers, ids, "retry")).content == content
    # The replaced upload is gone; its URL no longer takes data.
    assert (await client.put(abandoned_url, content=b"late")).status_code == 404


@pytest.mark.asyncio
async def test_a_retried_finalize_returns_the_committed_artifact(
    client, uses_workflow, test_token  # noqa: F811
):
    body, headers, ids = await _job(client, test_token)
    content = _zip({"f": b"final"})
    first = await _upload(client, headers, ids, "fin", content)
    assert first.status_code == 200, first.text
    request = {**ids, "name": "fin", "size": str(len(content)),
               "hash": f"sha256:{hashlib.sha256(content).hexdigest()}"}

    again = await _twirp(client, "FinalizeArtifact", headers, request)
    assert again.status_code == 200, again.text
    assert again.json()["artifact_id"] == first.json()["artifact_id"]
    other = await _twirp(client, "FinalizeArtifact", headers, {**request, "hash": "sha256:" + "0" * 64})
    assert other.status_code == 409


@pytest.mark.asyncio
async def test_expired_urls_are_refused(
    client, uses_workflow, test_token, monkeypatch  # noqa: F811
):
    body, headers, ids = await _job(client, test_token)
    await _upload(client, headers, ids, "old", _zip({"o": b"1"}))
    created = await _twirp(client, "CreateArtifact", headers, {**ids, "name": "late", "version": 7})
    upload = _path(created.json()["signed_upload_url"])
    signed = await _twirp(client, "GetSignedArtifactURL", headers, {**ids, "name": "old"})
    download = _path(signed.json()["signed_url"])

    later = time.time() + 7 * 3600
    monkeypatch.setattr(actions_results, "time", types.SimpleNamespace(time=lambda: later))
    assert (await client.put(upload, content=b"data")).status_code == 403
    assert not os.path.exists(os.path.join(_upload_dir(upload), "blob"))
    assert (await client.get(download)).status_code == 403


@pytest.mark.asyncio
async def test_an_upload_over_the_size_limit_is_refused(
    client, uses_workflow, test_token, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(settings, "ARTIFACT_MAX_BYTES", 10)
    body, headers, ids = await _job(client, test_token)
    created = await _twirp(client, "CreateArtifact", headers, {**ids, "name": "big", "version": 7})
    upload = _path(created.json()["signed_upload_url"])
    directory = _upload_dir(upload)

    # One Put Blob, and one block, over the limit.
    assert (await client.put(upload, content=b"x" * 11)).status_code == 413
    block = base64.b64encode(b"block-0000").decode()
    assert (await client.put(f"{upload}&comp=block&blockid={block}", content=b"x" * 11)).status_code == 413
    # Blocks within the limit whose list adds up past it.
    ids_ = []
    for index in range(2):
        block = base64.b64encode(f"block-{index:04d}".encode()).decode()
        put = await client.put(f"{upload}&comp=block&blockid={block}", content=b"x" * 6)
        assert put.status_code == 201
        ids_.append(block)
    block_list = "<BlockList>" + "".join(f"<Latest>{b}</Latest>" for b in ids_) + "</BlockList>"
    assert (await client.put(f"{upload}&comp=blocklist", content=block_list.encode())).status_code == 413
    assert not [n for n in os.listdir(directory) if n.startswith("blob")]


@pytest.mark.asyncio
async def test_a_block_list_over_the_entry_limit_is_refused(
    client, uses_workflow, test_token, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(settings, "ARTIFACT_MAX_BLOCKS", 2)
    body, headers, ids = await _job(client, test_token)
    created = await _twirp(client, "CreateArtifact", headers, {**ids, "name": "many", "version": 7})
    upload = _path(created.json()["signed_upload_url"])
    block = base64.b64encode(b"block-0000").decode()
    assert (await client.put(f"{upload}&comp=block&blockid={block}", content=b"x")).status_code == 201
    block_list = "<BlockList>" + f"<Latest>{block}</Latest>" * 3 + "</BlockList>"
    assert (await client.put(f"{upload}&comp=blocklist", content=block_list.encode())).status_code == 413
    assert not [n for n in os.listdir(_upload_dir(upload)) if n.startswith("blob")]


@pytest.mark.asyncio
async def test_a_zip_expanding_past_the_limit_is_refused(
    client, uses_workflow, test_token, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(settings, "ARTIFACT_MAX_BYTES", 1000)
    body, headers, ids = await _job(client, test_token)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zeros", b"\0" * 5000)
    content = buffer.getvalue()
    assert len(content) < 1000

    finalize = await _upload(client, headers, ids, "bomb", content)
    assert finalize.status_code == 429, finalize.text
    assert finalize.json()["code"] == "resource_exhausted"
    listed = await _twirp(client, "ListArtifacts", headers, {**ids, "name_filter": "bomb"})
    assert listed.json()["artifacts"] == []
    stored = os.path.join(settings.DATA_DIR, "artifacts")
    assert not os.path.isdir(stored) or os.listdir(stored) == []


@pytest.mark.asyncio
async def test_a_compressed_artifact_lists_the_uploaded_size(
    client, uses_workflow, test_token  # noqa: F811
):
    """ListArtifacts and REST report the blob's size, not what it expands to."""
    body, headers, ids = await _job(client, test_token)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.txt", b"a" * 20000)
    content = buffer.getvalue()
    assert len(content) < 20000

    finalize = await _upload(client, headers, ids, "packed", content)
    assert finalize.status_code == 200, finalize.text
    listed = await _twirp(client, "ListArtifacts", headers, {**ids, "name_filter": "packed"})
    [entry] = listed.json()["artifacts"]
    assert entry["size"] == str(len(content))
    rest = await client.get(
        f"{API}/repos/testuser/uses-step-repo/actions/artifacts/{finalize.json()['artifact_id']}",
        headers=auth_headers(test_token),
    )
    assert rest.json()["size_in_bytes"] == len(content)
    assert rest.json()["files"] == {"big.txt": 20000}


@pytest.mark.asyncio
async def test_two_finalizes_racing_for_one_name_store_one_artifact(
    client, uses_workflow, test_token, monkeypatch  # noqa: F811
):
    """Two uploads that both passed the name lookup: the first stored wins.

    Two jobs of one run finalizing the same name at once both see no
    artifact of that name before either is stored. The lookup is made to
    answer "none" so the second finalize reaches storage the same way.
    """
    body, headers, ids = await _job(client, test_token)
    winner = _zip({"a": b"1"})
    first = await _upload(client, headers, ids, "shared", winner)
    assert first.status_code == 200, first.text

    async def nothing_named(db, run_id, name):
        return None

    monkeypatch.setattr(actions_results, "_artifact_named", nothing_named)
    second = await _upload(client, headers, ids, "shared", _zip({"b": b"2"}))
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "already_exists"

    monkeypatch.undo()
    listed = await _twirp(client, "ListArtifacts", headers, {**ids, "name_filter": "shared"})
    assert [a["database_id"] for a in listed.json()["artifacts"]] == [first.json()["artifact_id"]]
    assert (await _download(client, headers, ids, "shared")).content == winner
    blobs = os.listdir(os.path.join(settings.DATA_DIR, "artifact-blobs"))
    assert sorted(blobs) == sorted([first.json()["artifact_id"], first.json()["artifact_id"] + ".json"])

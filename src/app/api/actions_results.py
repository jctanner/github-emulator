"""The Actions results service that upload-artifact@v4+ and download-artifact@v4+ use.

Those actions (through @actions/artifact v2+) do not use the REST artifact
API. They call a Twirp service at ACTIONS_RESULTS_URL, authenticated with the
job's ACTIONS_RUNTIME_TOKEN, and move the bytes through Azure Blob Storage
URLs that service signs:

- ``CreateArtifact`` returns a signed upload URL;
- the client uploads the zip there with block-blob calls (``comp=block`` for
  each chunk, then ``comp=blocklist`` to commit them in order);
- ``FinalizeArtifact`` makes it an artifact of the run;
- ``ListArtifacts`` and ``GetSignedArtifactURL`` find one and return a signed
  download URL, which serves the uploaded blob byte for byte;
- ``DeleteArtifact`` removes one (upload-artifact's ``overwrite: true``).

Here the "blob storage" is a directory per pending upload, and a finalized
upload is stored like any other artifact (ADR-0002), so the REST routes see
it too. Signed URLs carry an expiry and an HMAC of their purpose, id and
expiry under SECRET_KEY; the Azure client sends no other credential.

The actions refuse to run at all unless GITHUB_SERVER_URL's host is
github.com, *.ghe.com or *.localhost (anything else is treated as GHES,
which they do not support), so an emulator serving these jobs needs a
``*.localhost`` host name.
"""

import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import time
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from sqlalchemy import select

from app.api.actions_distributed_task import _request_base
from app.api.deps import DbSession
from app.config import settings
from app.models.artifact import WorkflowArtifact
from app.schemas.user import _fmt_dt
from app.services import artifact_storage
from app.services.job_token_service import validate_job_token

router = APIRouter(tags=["actions-results"])

SERVICE = "/twirp/github.actions.results.api.v1.ArtifactService"

# How long a signed upload or download URL stays valid: a job's longest run.
URL_LIFETIME_SECONDS = 6 * 3600


class TwirpError(Exception):
    """A Twirp error: an HTTP status and ``{"code", "msg"}``."""

    STATUS = {
        "unauthenticated": 401,
        "permission_denied": 403,
        "not_found": 404,
        "already_exists": 409,
        "invalid_argument": 400,
        "failed_precondition": 412,
        "resource_exhausted": 429,
    }

    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code
        self.msg = msg

    def response(self) -> JSONResponse:
        return JSONResponse({"code": self.code, "msg": self.msg}, status_code=self.STATUS[self.code])


def _sign(purpose: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), purpose.encode(), hashlib.sha256).hexdigest()


def _signed_query(purpose: str) -> str:
    expires = int(time.time()) + URL_LIFETIME_SECONDS
    return f"exp={expires}&sig={_sign(f'{purpose}:{expires}')}"


def _signature_ok(purpose: str, exp: str, sig: str) -> bool:
    """The signature covers the purpose and the expiry, which has not passed."""
    if not exp.isdigit() or int(exp) < time.time():
        return False
    return hmac.compare_digest(_sign(f"{purpose}:{exp}"), sig or "")


def _uploads_dir() -> str:
    return os.path.join(settings.DATA_DIR, "artifact-uploads")


def _upload_dir(key: str) -> str:
    # Keys are token_urlsafe output; anything else never names a directory.
    if not key or not all(c.isalnum() or c in "-_" for c in key):
        raise TwirpError("not_found", "unknown upload")
    return os.path.join(_uploads_dir(), key)


def _active_pointer(run_id: int, job_id: int, name: str) -> str:
    """The file naming the one active pending upload for this run, job and name."""
    digest = hashlib.sha256(f"{run_id}:{job_id}:{name}".encode()).hexdigest()
    return os.path.join(settings.DATA_DIR, "artifact-uploads-active", digest)


def _active_upload(run_id: int, job_id: int, name: str) -> str | None:
    try:
        with open(_active_pointer(run_id, job_id, name)) as handle:
            directory = _upload_dir(handle.read().strip())
    except (OSError, TwirpError):
        return None
    return directory if os.path.isdir(directory) else None


def _end_upload(run_id: int, job_id: int, name: str) -> None:
    """Drop the active pending upload for this name, and its pointer."""
    directory = _active_upload(run_id, job_id, name)
    if directory is not None:
        shutil.rmtree(directory, ignore_errors=True)
    with contextlib.suppress(FileNotFoundError):
        os.remove(_active_pointer(run_id, job_id, name))


async def _job_and_run(request: Request, db):
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    validated = await validate_job_token(db, token) if token else None
    if validated is None:
        raise TwirpError("unauthenticated", "a valid ACTIONS_RUNTIME_TOKEN is required")
    return validated


def _check_backend_ids(body: dict, job, run) -> None:
    """The ids in the request are the ones the token carries; refuse others."""
    if str(body.get("workflow_run_backend_id", "")) != str(run.id) or str(
        body.get("workflow_job_run_backend_id", "")
    ) != str(job.id):
        raise TwirpError("permission_denied", "backend ids do not match the runtime token")


async def _artifact_named(db, run_id: int, name: str) -> WorkflowArtifact | None:
    return (await db.execute(
        select(WorkflowArtifact).where(
            WorkflowArtifact.run_id == run_id,
            WorkflowArtifact.name == name,
            WorkflowArtifact.expired.is_(False),
        )
    )).scalars().first()


def _string_value(body: dict, key: str) -> str:
    """A google.protobuf.StringValue in proto JSON is the bare string."""
    value = body.get(key)
    if isinstance(value, dict):  # tolerate {"value": ...}
        value = value.get("value")
    return "" if value is None else str(value)


@router.post(SERVICE + "/{method}")
async def artifact_service(method: str, request: Request, db: DbSession):
    try:
        job, run = await _job_and_run(request, db)
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            raise TwirpError("invalid_argument", "request body must be JSON") from None
        if not isinstance(body, dict):
            raise TwirpError("invalid_argument", "request body must be a JSON object")
        _check_backend_ids(body, job, run)
        handler = _METHODS.get(method)
        if handler is None:
            return JSONResponse({"code": "bad_route", "msg": f"no method {method}"}, status_code=404)
        return JSONResponse(await handler(body, job, run, request, db))
    except TwirpError as exc:
        return exc.response()


async def _create_artifact(body, job, run, request, db) -> dict:
    name = str(body.get("name", "")).strip()
    if not name:
        raise TwirpError("invalid_argument", "name is required")
    if await _artifact_named(db, run.id, name) is not None:
        raise TwirpError("already_exists", f"an artifact named {name} already exists for this run")
    # @actions/artifact sends the blob's content type. A zip it built is
    # application/zip under the artifact's name; with `archive: false` the
    # artifact is named after its one file, so `report.zip` sent as is is a
    # file, not an archive. Clients without the field always send a zip.
    content_type = _string_value(body, "mime_type")
    archived = not content_type or (
        content_type.lower() == "application/zip" and not name.lower().endswith(".zip")
    )
    # One pending upload per run, job and name: a new CreateArtifact replaces
    # an earlier one, whose URL then answers 404.
    _end_upload(run.id, job.id, name)
    key = secrets.token_urlsafe(24)
    directory = _upload_dir(key)
    os.makedirs(os.path.join(directory, "blocks"))
    with open(os.path.join(directory, "meta.json"), "w") as handle:
        json.dump({
            "run_id": run.id, "job_id": job.id, "repo_id": run.repo_id, "name": name,
            "archived": archived, "content_type": content_type or "application/zip",
        }, handle)
    pointer = _active_pointer(run.id, job.id, name)
    os.makedirs(os.path.dirname(pointer), exist_ok=True)
    with open(pointer + ".tmp", "w") as handle:
        handle.write(key)
    os.replace(pointer + ".tmp", pointer)
    url = f"{_request_base(request)}/_apis/results/uploads/{key}?{_signed_query(f'upload:{key}')}"
    return {"ok": True, "signed_upload_url": url}


def _sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


async def _finalize_artifact(body, job, run, request, db) -> dict:
    name = str(body.get("name", "")).strip()
    expected = _string_value(body, "hash")
    expected_sha = expected[7:] if expected.startswith("sha256:") else ""
    existing = await _artifact_named(db, run.id, name)
    if existing is not None:
        # A retried FinalizeArtifact whose first answer was lost gets the
        # artifact it committed.
        meta = artifact_storage.blob_meta(existing.id) or {}
        if expected_sha and meta.get("sha256") == expected_sha and meta.get("job_id") == job.id:
            return {"ok": True, "artifact_id": str(existing.id)}
        raise TwirpError("already_exists", f"an artifact named {name} already exists for this run")
    directory = _active_upload(run.id, job.id, name)
    if directory is None:
        raise TwirpError("not_found", f"no upload in progress for {name}")
    blob = os.path.join(directory, "blob")
    if not os.path.isfile(blob):
        raise TwirpError("failed_precondition", f"nothing was uploaded for {name}")
    with open(os.path.join(directory, "meta.json")) as handle:
        meta = json.load(handle)
    sha256 = _sha256_of(blob)
    if expected_sha and sha256 != expected_sha:
        raise TwirpError("invalid_argument", "uploaded content does not match the hash")
    archived = bool(meta.get("archived", True))
    try:
        artifact = await artifact_storage.store_blob_artifact(
            db, run_id=run.id, repo_id=run.repo_id, job_id=job.id, name=name, source=blob,
            archived=archived, content_type=meta.get("content_type") or "application/zip",
            file_name=f"{name}.zip" if archived else name, sha256=sha256,
        )
    except artifact_storage.ArtifactPathError as exc:
        raise TwirpError("invalid_argument", str(exc)) from exc
    except artifact_storage.ArtifactTooLarge as exc:
        raise TwirpError("resource_exhausted", str(exc)) from exc
    _end_upload(run.id, job.id, name)
    return {"ok": True, "artifact_id": str(artifact.id)}


async def _list_artifacts(body, job, run, request, db) -> dict:
    query = select(WorkflowArtifact).where(
        WorkflowArtifact.run_id == run.id, WorkflowArtifact.expired.is_(False),
    ).order_by(WorkflowArtifact.id)
    name = _string_value(body, "name_filter")
    if name:
        query = query.where(WorkflowArtifact.name == name)
    artifact_id = _string_value(body, "id_filter")
    if artifact_id:
        query = query.where(WorkflowArtifact.id == int(artifact_id))
    artifacts = (await db.execute(query)).scalars().all()
    return {"artifacts": [
        {
            "workflow_run_backend_id": str(run.id),
            "workflow_job_run_backend_id": str(job.id),
            "database_id": str(artifact.id),
            "name": artifact.name,
            "size": str(artifact.size_in_bytes),
            "created_at": _fmt_dt(artifact.created_at),
        }
        for artifact in artifacts
    ]}


async def _get_signed_artifact_url(body, job, run, request, db) -> dict:
    name = str(body.get("name", "")).strip()
    artifact = await _artifact_named(db, run.id, name)
    if artifact is None:
        raise TwirpError("not_found", f"no artifact named {name} in this run")
    query = _signed_query(f"download:{artifact.id}")
    return {"signed_url": f"{_request_base(request)}/_apis/results/artifacts/{artifact.id}/download?{query}"}


async def _delete_artifact(body, job, run, request, db) -> dict:
    name = str(body.get("name", "")).strip()
    artifact = await _artifact_named(db, run.id, name)
    if artifact is None:
        raise TwirpError("not_found", f"no artifact named {name} in this run")
    artifact_id = artifact.id
    await artifact_storage.delete_artifact(db, artifact)
    return {"ok": True, "artifact_id": str(artifact_id)}


_METHODS = {
    "CreateArtifact": _create_artifact,
    "FinalizeArtifact": _finalize_artifact,
    "ListArtifacts": _list_artifacts,
    "GetSignedArtifactURL": _get_signed_artifact_url,
    "DeleteArtifact": _delete_artifact,
}


# --- the blob storage side ----------------------------------------------------

def _blob_headers() -> dict:
    # What the Azure client parses from a block-blob response; values are
    # placeholders, but their presence keeps its deserializer quiet.
    return {
        "x-ms-request-id": secrets.token_hex(16),
        "x-ms-version": "2021-08-06",
        "x-ms-request-server-encrypted": "true",
        "ETag": f'"0x{secrets.token_hex(8).upper()}"',
        "Last-Modified": "Thu, 01 Jan 2026 00:00:00 GMT",
    }


# <BlockList><Latest>id</Latest>...</BlockList>; Committed and Uncommitted
# name a block the same way for this purpose.
_BLOCK_ID = re.compile(r"<(?:Latest|Committed|Uncommitted)>([^<]*)</(?:Latest|Committed|Uncommitted)>")

# Room for one block-list entry: a base64 block id of at most 64 bytes, and
# its tags.
_BLOCK_ENTRY_BYTES = 128


class _TooLarge(Exception):
    pass


def _block_path(directory: str, block_id: str) -> str:
    return os.path.join(directory, "blocks", hashlib.sha256(block_id.encode()).hexdigest())


async def _receive(request: Request, path: str, limit: int) -> None:
    """Stream the request body to ``path``; past ``limit`` bytes, keep nothing."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _TooLarge
    partial = path + ".part"
    try:
        size = 0
        with open(partial, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise _TooLarge
                out.write(chunk)
        os.replace(partial, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(partial)


async def _read_small(request: Request, limit: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise _TooLarge
    return bytes(body)


def _commit_blocks(directory: str, ids: list[str]) -> Response | None:
    """Join the listed blocks into the blob, all or nothing; an error response or None."""
    paths = [_block_path(directory, block_id) for block_id in ids]
    if not all(os.path.isfile(path) for path in paths):
        return Response(status_code=400)
    if sum(os.path.getsize(path) for path in paths) > settings.ARTIFACT_MAX_BYTES:
        return Response(status_code=413)
    blob = os.path.join(directory, "blob")
    partial = blob + ".part"
    try:
        with open(partial, "wb") as out:
            for path in paths:
                with open(path, "rb") as block:
                    shutil.copyfileobj(block, out)
        os.replace(partial, blob)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(partial)
    return None


@router.put("/_apis/results/uploads/{key}")
async def blob_upload(
    key: str, request: Request,
    sig: str = Query(""), exp: str = Query(""), comp: str = Query(""), blockid: str = Query(""),
):
    """Put Block, Put Block List, or a single Put Blob, as Azure accepts them."""
    # The signature and expiry are checked before the body is read.
    if not _signature_ok(f"upload:{key}", exp, sig):
        return Response(status_code=403)
    try:
        directory = _upload_dir(key)
    except TwirpError:
        return Response(status_code=404)
    if not os.path.isdir(directory):
        return Response(status_code=404)
    limit = settings.ARTIFACT_MAX_BYTES
    try:
        if comp == "block":
            if not blockid:
                return Response(status_code=400)
            await _receive(request, _block_path(directory, blockid), limit)
        elif comp == "blocklist":
            # Only the ordered block ids matter; read them without an XML
            # parser, which would process entities from the body.
            max_blocks = settings.ARTIFACT_MAX_BLOCKS
            body = await _read_small(request, max_blocks * _BLOCK_ENTRY_BYTES + 1024)
            ids = _BLOCK_ID.findall(body.decode("utf-8", "replace"))
            if not ids:
                return Response(status_code=400)
            if len(ids) > max_blocks:
                return Response(status_code=413)
            error = _commit_blocks(directory, ids)
            if error is not None:
                return error
        elif comp:
            return Response(status_code=400)
        else:
            await _receive(request, os.path.join(directory, "blob"), limit)
    except _TooLarge:
        return Response(status_code=413)
    return Response(status_code=201, headers=_blob_headers())


@router.get("/_apis/results/artifacts/{artifact_id}/download")
async def blob_download(artifact_id: int, db: DbSession, sig: str = Query(""), exp: str = Query("")):
    """The artifact's blob as uploaded, with its content type and file name."""
    if not _signature_ok(f"download:{artifact_id}", exp, sig):
        return Response(status_code=403)
    artifact = (await db.execute(
        select(WorkflowArtifact).where(WorkflowArtifact.id == artifact_id)
    )).scalar_one_or_none()
    if artifact is None or artifact.expired:
        return Response(status_code=404)
    meta = artifact_storage.blob_meta(artifact_id)
    path = artifact_storage.blob_path(artifact_id)
    if meta is None or not os.path.isfile(path):
        # Uploaded through REST: no blob was sent, so zip its files.
        return StreamingResponse(
            artifact_storage.zip_artifact(artifact), media_type="application/zip",
            headers={"Content-Disposition": _disposition(f"{artifact.name}.zip")},
        )
    return FileResponse(
        path, media_type=meta.get("content_type") or "application/zip",
        headers={"Content-Disposition": _disposition(meta.get("file_name") or artifact.name)},
    )


def _disposition(file_name: str) -> str:
    ascii_name = file_name.encode("ascii", "replace").decode().replace('"', "_").replace("?", "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file_name)}"

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
  download URL for the zip.

Here the "blob storage" is a directory per pending upload, and a finalized
upload is stored like any other artifact (ADR-0002), so the REST routes see
it too. Signed URLs carry an HMAC of their path under SECRET_KEY; the Azure
client sends no other credential.

The actions refuse to run at all unless GITHUB_SERVER_URL's host is
github.com, *.ghe.com or *.localhost (anything else is treated as GHES,
which they do not support), so an emulator serving these jobs needs a
``*.localhost`` host name.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import zipfile
from datetime import timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select

from app.api.actions_distributed_task import _request_base
from app.api.deps import DbSession
from app.config import settings
from app.models.artifact import WorkflowArtifact
from app.services import artifact_storage
from app.services.job_token_service import validate_job_token

router = APIRouter()

SERVICE = "/twirp/github.actions.results.api.v1.ArtifactService"


class TwirpError(Exception):
    """A Twirp error: an HTTP status and ``{"code", "msg"}``."""

    STATUS = {
        "unauthenticated": 401,
        "permission_denied": 403,
        "not_found": 404,
        "already_exists": 409,
        "invalid_argument": 400,
        "failed_precondition": 412,
    }

    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code
        self.msg = msg

    def response(self) -> JSONResponse:
        return JSONResponse({"code": self.code, "msg": self.msg}, status_code=self.STATUS[self.code])


def _sign(purpose: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), purpose.encode(), hashlib.sha256).hexdigest()


def _signature_ok(purpose: str, sig: str) -> bool:
    return hmac.compare_digest(_sign(purpose), sig or "")


def _uploads_dir() -> str:
    return os.path.join(settings.DATA_DIR, "artifact-uploads")


def _upload_dir(key: str) -> str:
    # Keys are token_urlsafe output; anything else never names a directory.
    if not key or not all(c.isalnum() or c in "-_" for c in key):
        raise TwirpError("not_found", "unknown upload")
    return os.path.join(_uploads_dir(), key)


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


def _timestamp(value) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    key = secrets.token_urlsafe(24)
    directory = _upload_dir(key)
    os.makedirs(os.path.join(directory, "blocks"))
    with open(os.path.join(directory, "meta.json"), "w") as handle:
        json.dump({"run_id": run.id, "job_id": job.id, "repo_id": run.repo_id, "name": name}, handle)
    url = f"{_request_base(request)}/_apis/results/uploads/{key}?sig={_sign(f'upload:{key}')}"
    return {"ok": True, "signed_upload_url": url}


async def _finalize_artifact(body, job, run, request, db) -> dict:
    name = str(body.get("name", "")).strip()
    pending = _pending_upload(run.id, job.id, name)
    if pending is None:
        raise TwirpError("not_found", f"no upload in progress for {name}")
    directory, blob = pending
    if not os.path.isfile(blob):
        raise TwirpError("failed_precondition", f"nothing was uploaded for {name}")
    if await _artifact_named(db, run.id, name) is not None:
        raise TwirpError("already_exists", f"an artifact named {name} already exists for this run")
    with open(blob, "rb") as handle:
        raw = handle.read()
    expected = _string_value(body, "hash")
    if expected.startswith("sha256:") and hashlib.sha256(raw).hexdigest() != expected[7:]:
        raise TwirpError("invalid_argument", "uploaded content does not match the hash")
    # upload-artifact sends a zip unless `archive: false` uploads one file
    # as is; keep that file under its own name.
    try:
        payload = artifact_storage.zip_members(raw)
    except zipfile.BadZipFile:
        payload = {name: raw}
    try:
        artifact = await artifact_storage.store_artifact(
            db, run_id=run.id, repo_id=run.repo_id, name=name, payload=payload,
        )
    except artifact_storage.ArtifactPathError as exc:
        raise TwirpError("invalid_argument", str(exc)) from exc
    shutil.rmtree(directory, ignore_errors=True)
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
            "created_at": _timestamp(artifact.created_at),
        }
        for artifact in artifacts
    ]}


async def _get_signed_artifact_url(body, job, run, request, db) -> dict:
    name = str(body.get("name", "")).strip()
    artifact = await _artifact_named(db, run.id, name)
    if artifact is None:
        raise TwirpError("not_found", f"no artifact named {name} in this run")
    sig = _sign(f"download:{artifact.id}")
    return {"signed_url": f"{_request_base(request)}/_apis/results/artifacts/{artifact.id}/download?sig={sig}"}


_METHODS = {
    "CreateArtifact": _create_artifact,
    "FinalizeArtifact": _finalize_artifact,
    "ListArtifacts": _list_artifacts,
    "GetSignedArtifactURL": _get_signed_artifact_url,
}


def _pending_upload(run_id: int, job_id: int, name: str):
    """The upload directory this job opened for ``name``, and its blob path."""
    root = _uploads_dir()
    if not os.path.isdir(root):
        return None
    for key in os.listdir(root):
        directory = os.path.join(root, key)
        try:
            with open(os.path.join(directory, "meta.json")) as handle:
                meta = json.load(handle)
        except (OSError, ValueError):
            continue
        if (meta.get("run_id"), meta.get("job_id"), meta.get("name")) == (run_id, job_id, name):
            return directory, os.path.join(directory, "blob")
    return None


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


def _block_path(directory: str, block_id: str) -> str:
    return os.path.join(directory, "blocks", hashlib.sha256(block_id.encode()).hexdigest())


@router.put("/_apis/results/uploads/{key}")
async def blob_upload(
    key: str, request: Request,
    sig: str = Query(""), comp: str = Query(""), blockid: str = Query(""),
):
    """Put Block, Put Block List, or a single Put Blob, as Azure accepts them."""
    if not _signature_ok(f"upload:{key}", sig):
        return Response(status_code=403)
    try:
        directory = _upload_dir(key)
    except TwirpError:
        return Response(status_code=404)
    if not os.path.isdir(directory):
        return Response(status_code=404)
    body = await request.body()
    if comp == "block":
        if not blockid:
            return Response(status_code=400)
        with open(_block_path(directory, blockid), "wb") as handle:
            handle.write(body)
    elif comp == "blocklist":
        # Only the ordered block ids matter; read them without an XML parser,
        # so a crafted body cannot spend entity expansion on us.
        ids = _BLOCK_ID.findall(body.decode("utf-8", "replace"))
        if not ids:
            return Response(status_code=400)
        with open(os.path.join(directory, "blob"), "wb") as out:
            for block_id in ids:
                path = _block_path(directory, block_id)
                if not os.path.isfile(path):
                    return Response(status_code=400)
                with open(path, "rb") as block:
                    shutil.copyfileobj(block, out)
    elif comp:
        return Response(status_code=400)
    else:
        with open(os.path.join(directory, "blob"), "wb") as handle:
            handle.write(body)
    return Response(status_code=201, headers=_blob_headers())


@router.get("/_apis/results/artifacts/{artifact_id}/download")
async def blob_download(artifact_id: int, db: DbSession, sig: str = Query("")):
    if not _signature_ok(f"download:{artifact_id}", sig):
        return Response(status_code=403)
    artifact = (await db.execute(
        select(WorkflowArtifact).where(WorkflowArtifact.id == artifact_id)
    )).scalar_one_or_none()
    if artifact is None or artifact.expired:
        return Response(status_code=404)
    return StreamingResponse(artifact_storage.zip_artifact(artifact), media_type="application/zip")

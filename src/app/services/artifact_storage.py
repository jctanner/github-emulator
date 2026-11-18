"""Artifact bytes on disk, indexed by the ``workflow_artifacts`` row.

ADR-0002: the database row keeps name, size and a path -> size index; the
bytes live under ``DATA_DIR/artifacts/{artifact_id}/``. Both upload paths,
the REST route and the Actions results service the upload-artifact action
talks to, store through here so they cannot drift apart. A results-service
upload also keeps the blob it sent under ``DATA_DIR/artifact-blobs/``, so its
download returns those exact bytes.
"""

import contextlib
import io
import json
import os
import shutil
import zipfile

from app.config import settings
from app.models.artifact import WorkflowArtifact


class ArtifactPathError(ValueError):
    """A member path that would resolve outside its artifact directory."""


def artifact_dir(artifact_id: int) -> str:
    """Where an artifact's bytes live.

    Alongside logs/jobs, which is the same pattern: the database row indexes
    the upload and the filesystem holds it. DATA_DIR is a mounted volume, so
    this survives a restart.
    """
    return os.path.join(settings.DATA_DIR, "artifacts", str(artifact_id))


def member_path(artifact_id: int, relative: str) -> str:
    """Resolve a path inside an artifact, refusing anything that escapes it.

    An artifact's member paths come from whoever uploaded it, so "../" and
    absolute paths have to be rejected rather than normalised away: a stored
    path that resolves outside the artifact directory would let an upload
    write over, or read back, unrelated emulator data.
    """
    base = os.path.realpath(artifact_dir(artifact_id))
    target = os.path.realpath(os.path.join(base, relative))
    if target != base and not target.startswith(base + os.sep):
        raise ArtifactPathError(f"Invalid artifact path: {relative}")
    return target


def zip_members(raw: bytes) -> dict[str, bytes]:
    """The files in a zip archive, by member path. Raises zipfile.BadZipFile."""
    payload: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            payload[member.filename] = archive.read(member)
    return payload


class ArtifactTooLarge(ValueError):
    """An artifact over ``settings.ARTIFACT_MAX_BYTES``."""


def _blobs_dir() -> str:
    return os.path.join(settings.DATA_DIR, "artifact-blobs")


def blob_path(artifact_id: int) -> str:
    """The blob a results-service upload sent, kept byte for byte."""
    return os.path.join(_blobs_dir(), str(artifact_id))


def blob_meta(artifact_id: int) -> dict | None:
    """The stored blob's content type, file name, sha256 and job, if any."""
    try:
        with open(blob_path(artifact_id) + ".json") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def remove_files(artifact_id: int) -> None:
    """Remove everything stored for an artifact: its files and its blob."""
    shutil.rmtree(artifact_dir(artifact_id), ignore_errors=True)
    for path in (blob_path(artifact_id), blob_path(artifact_id) + ".json"):
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)


async def delete_artifact(db, artifact: WorkflowArtifact) -> None:
    remove_files(artifact.id)
    await db.delete(artifact)
    await db.commit()


async def _store(db, *, run_id: int, repo_id: int, name: str, write) -> WorkflowArtifact:
    """Create the row, write the files with ``write(artifact_id)``, record its index.

    A failed write removes both the files and the row rather than leaving an
    index pointing at a partial upload.
    """
    artifact = WorkflowArtifact(run_id=run_id, repo_id=repo_id, name=name, files={}, size_in_bytes=0)
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)

    try:
        index = write(artifact.id)
    except BaseException:
        remove_files(artifact.id)
        await db.delete(artifact)
        await db.commit()
        raise

    artifact.files = index
    artifact.size_in_bytes = sum(index.values())
    await db.commit()
    await db.refresh(artifact)
    return artifact


async def store_artifact(
    db, *, run_id: int, repo_id: int, name: str, payload: dict[str, bytes],
) -> WorkflowArtifact:
    """Store an artifact given as member path -> content (the REST upload)."""

    def write(artifact_id: int) -> dict[str, int]:
        index: dict[str, int] = {}
        for relative, content in payload.items():
            target = member_path(artifact_id, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(content)
            index[relative] = len(content)
        return index

    return await _store(db, run_id=run_id, repo_id=repo_id, name=name, write=write)


def _extract_zip(artifact_id: int, source: str, limit: int) -> dict[str, int]:
    """Expand a zip into the artifact's directory, at most ``limit`` bytes in all."""
    with zipfile.ZipFile(source) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if sum(m.file_size for m in members) > limit:
            raise ArtifactTooLarge(f"the archive expands to more than {limit} bytes")
        index: dict[str, int] = {}
        total = 0
        for member in members:
            target = member_path(artifact_id, member.filename)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(member) as src, open(target, "wb") as out:
                while chunk := src.read(1 << 20):
                    total += len(chunk)
                    if total > limit:
                        raise ArtifactTooLarge(f"the archive expands to more than {limit} bytes")
                    out.write(chunk)
            index[member.filename] = member.file_size
        return index


async def store_blob_artifact(
    db, *, run_id: int, repo_id: int, job_id: int, name: str, source: str,
    archived: bool, content_type: str, file_name: str, sha256: str,
) -> WorkflowArtifact:
    """Store a results-service upload: the blob as sent, and an index for REST.

    The blob at ``source`` is moved into place unchanged, so a download
    returns the bytes the client uploaded. The REST routes read the files
    under the artifact directory: an archived upload's zip members, or the
    one unarchived file under the artifact's name.
    """
    limit = settings.ARTIFACT_MAX_BYTES

    def write(artifact_id: int) -> dict[str, int]:
        index = None
        if archived:
            try:
                index = _extract_zip(artifact_id, source, limit)
            except zipfile.BadZipFile:
                # Not a zip after all: keep it as the one file it is.
                shutil.rmtree(artifact_dir(artifact_id), ignore_errors=True)
        if index is None:
            target = member_path(artifact_id, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(source, target)
            index = {name: os.path.getsize(target)}
        os.makedirs(_blobs_dir(), exist_ok=True)
        os.replace(source, blob_path(artifact_id))
        with open(blob_path(artifact_id) + ".json", "w") as handle:
            json.dump({
                "content_type": content_type, "file_name": file_name,
                "sha256": sha256, "job_id": job_id,
            }, handle)
        return index

    return await _store(db, run_id=run_id, repo_id=repo_id, name=name, write=write)


def zip_artifact(artifact: WorkflowArtifact) -> io.BytesIO:
    """The artifact's files as a zip archive, rewound for reading."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted((artifact.files or {}).keys()):
            source = member_path(artifact.id, relative)
            if os.path.isfile(source):
                archive.write(source, arcname=relative)
    buffer.seek(0)
    return buffer

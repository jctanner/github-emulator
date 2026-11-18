"""Artifact bytes on disk, indexed by the ``workflow_artifacts`` row.

ADR-0002: the database row keeps name, size and a path -> size index; the
bytes live under ``DATA_DIR/artifacts/{artifact_id}/``. Both upload paths,
the REST route and the Actions results service the upload-artifact action
talks to, store through here so they cannot drift apart.
"""

import io
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


async def store_artifact(
    db, *, run_id: int, repo_id: int, name: str, payload: dict[str, bytes],
) -> WorkflowArtifact:
    """Create the row, write the files, and record the index.

    A failed write removes both the directory and the row rather than leaving
    an index pointing at a partial upload.
    """
    artifact = WorkflowArtifact(run_id=run_id, repo_id=repo_id, name=name, files={}, size_in_bytes=0)
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)

    index: dict[str, int] = {}
    total = 0
    try:
        for relative, content in payload.items():
            target = member_path(artifact.id, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(content)
            index[relative] = len(content)
            total += len(content)
    except Exception:
        shutil.rmtree(artifact_dir(artifact.id), ignore_errors=True)
        await db.delete(artifact)
        await db.commit()
        raise

    artifact.files = index
    artifact.size_in_bytes = total
    await db.commit()
    await db.refresh(artifact)
    return artifact


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

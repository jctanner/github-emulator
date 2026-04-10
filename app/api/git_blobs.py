"""Git Data API -- Blobs."""

import asyncio
import base64
import os

from fastapi import APIRouter, HTTPException

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings

router = APIRouter(tags=["git-blobs"])

BASE = settings.BASE_URL


async def _git(repo_path: str, *args: str, input_data: bytes | None = None) -> str:
    env = {**os.environ, "GIT_DIR": repo_path}
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdin=asyncio.subprocess.PIPE if input_data else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate(input=input_data)
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode())
    return stdout.decode()


async def _git_bytes(repo_path: str, *args: str) -> bytes:
    env = {**os.environ, "GIT_DIR": repo_path}
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError("git command failed")
    return stdout


@router.get("/repos/{owner}/{repo}/git/blobs/{sha}")
async def get_blob(
    owner: str, repo: str, sha: str, db: DbSession, current_user: CurrentUser
):
    """Get a Git blob."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Not Found")

    try:
        content = await _git_bytes(repository.disk_path, "cat-file", "blob", sha)
    except RuntimeError:
        raise HTTPException(status_code=404, detail="Not Found")

    api = f"{BASE}/api/v3"
    return {
        "sha": sha,
        "node_id": "",
        "size": len(content),
        "url": f"{api}/repos/{owner}/{repo}/git/blobs/{sha}",
        "content": base64.b64encode(content).decode(),
        "encoding": "base64",
    }


@router.post("/repos/{owner}/{repo}/git/blobs", status_code=201)
async def create_blob(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession
):
    """Create a Git blob."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Repository not found on disk")

    content = body.get("content", "")
    encoding = body.get("encoding", "utf-8")

    if encoding == "base64":
        try:
            data = base64.b64decode(content)
        except Exception:
            raise HTTPException(status_code=422, detail="Invalid base64 content")
    else:
        data = content.encode("utf-8")

    try:
        sha = (await _git(repository.disk_path, "hash-object", "-w", "--stdin", input_data=data)).strip()
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    api = f"{BASE}/api/v3"
    return {
        "sha": sha,
        "url": f"{api}/repos/{owner}/{repo}/git/blobs/{sha}",
    }

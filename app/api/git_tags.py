"""Git Data API -- Tags."""

import asyncio
import os

from fastapi import APIRouter, HTTPException

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings

router = APIRouter(tags=["git-tags"])

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


@router.get("/repos/{owner}/{repo}/git/tags/{sha}")
async def get_tag(
    owner: str, repo: str, sha: str, db: DbSession, current_user: CurrentUser
):
    """Get a Git tag object."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Not Found")

    try:
        obj_type = (await _git(repository.disk_path, "cat-file", "-t", sha)).strip()
    except RuntimeError:
        raise HTTPException(status_code=404, detail="Not Found")

    if obj_type != "tag":
        raise HTTPException(status_code=404, detail="Not a tag object")

    try:
        tag_info = (await _git(repository.disk_path, "cat-file", "-p", sha)).strip()
    except RuntimeError:
        raise HTTPException(status_code=404, detail="Not Found")

    # Parse tag info
    tag_name = ""
    target_sha = ""
    tagger = ""
    message_lines = []
    in_message = False
    for line in tag_info.splitlines():
        if in_message:
            message_lines.append(line)
        elif line == "":
            in_message = True
        elif line.startswith("object "):
            target_sha = line.split(" ", 1)[1]
        elif line.startswith("tag "):
            tag_name = line.split(" ", 1)[1]
        elif line.startswith("tagger "):
            tagger = line.split(" ", 1)[1]

    api = f"{BASE}/api/v3"
    return {
        "sha": sha,
        "tag": tag_name,
        "url": f"{api}/repos/{owner}/{repo}/git/tags/{sha}",
        "message": "\n".join(message_lines),
        "tagger": {"name": tagger, "email": "", "date": ""},
        "object": {
            "type": "commit",
            "sha": target_sha,
            "url": f"{api}/repos/{owner}/{repo}/git/commits/{target_sha}",
        },
        "verification": {"verified": False, "reason": "unsigned", "signature": None, "payload": None},
    }


@router.post("/repos/{owner}/{repo}/git/tags", status_code=201)
async def create_tag(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession
):
    """Create a Git tag object (stub -- creates lightweight tag via update-ref)."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Repository not found on disk")

    tag_name = body.get("tag", "")
    sha = body.get("object", "")
    message = body.get("message", "")

    if not tag_name or not sha:
        raise HTTPException(status_code=422, detail="tag and object are required")

    try:
        await _git(repository.disk_path, "update-ref", f"refs/tags/{tag_name}", sha)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    api = f"{BASE}/api/v3"
    return {
        "sha": sha,
        "tag": tag_name,
        "url": f"{api}/repos/{owner}/{repo}/git/tags/{sha}",
        "message": message,
        "object": {
            "type": "commit",
            "sha": sha,
            "url": f"{api}/repos/{owner}/{repo}/git/commits/{sha}",
        },
    }

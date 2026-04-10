"""Git Data API -- Commits."""

import asyncio
import os

from fastapi import APIRouter, HTTPException

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings

router = APIRouter(tags=["git-commits"])

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


@router.get("/repos/{owner}/{repo}/git/commits/{sha}")
async def get_git_commit(
    owner: str, repo: str, sha: str, db: DbSession, current_user: CurrentUser
):
    """Get a Git commit object."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Not Found")

    fmt = "%H%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%ce%x1f%cI%x1f%s%x1f%P%x1f%T"
    try:
        out = (await _git(repository.disk_path, "log", f"--format={fmt}", "-1", sha)).strip()
    except RuntimeError:
        raise HTTPException(status_code=404, detail="Not Found")

    if not out:
        raise HTTPException(status_code=404, detail="Not Found")

    parts = out.split("\x1f")
    api = f"{BASE}/api/v3"
    parent_shas = parts[8].split() if len(parts) > 8 else []

    return {
        "sha": parts[0],
        "url": f"{api}/repos/{owner}/{repo}/git/commits/{parts[0]}",
        "html_url": f"{BASE}/{owner}/{repo}/commit/{parts[0]}",
        "author": {
            "name": parts[1] if len(parts) > 1 else "",
            "email": parts[2] if len(parts) > 2 else "",
            "date": parts[3] if len(parts) > 3 else "",
        },
        "committer": {
            "name": parts[4] if len(parts) > 4 else "",
            "email": parts[5] if len(parts) > 5 else "",
            "date": parts[6] if len(parts) > 6 else "",
        },
        "tree": {
            "sha": parts[9] if len(parts) > 9 else "",
            "url": f"{api}/repos/{owner}/{repo}/git/trees/{parts[9] if len(parts) > 9 else ''}",
        },
        "message": parts[7] if len(parts) > 7 else "",
        "parents": [
            {"sha": p, "url": f"{api}/repos/{owner}/{repo}/git/commits/{p}", "html_url": f"{BASE}/{owner}/{repo}/commit/{p}"}
            for p in parent_shas
        ],
        "verification": {"verified": False, "reason": "unsigned", "signature": None, "payload": None},
    }


@router.post("/repos/{owner}/{repo}/git/commits", status_code=201)
async def create_git_commit(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession
):
    """Create a Git commit."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Repository not found on disk")

    message = body.get("message", "")
    tree = body.get("tree", "")
    parents = body.get("parents", [])

    if not message or not tree:
        raise HTTPException(status_code=422, detail="message and tree are required")

    args = ["commit-tree", tree, "-m", message]
    for parent in parents:
        args.extend(["-p", parent])

    try:
        sha = (await _git(repository.disk_path, *args)).strip()
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    api = f"{BASE}/api/v3"
    return {
        "sha": sha,
        "url": f"{api}/repos/{owner}/{repo}/git/commits/{sha}",
        "message": message,
        "tree": {"sha": tree, "url": f"{api}/repos/{owner}/{repo}/git/trees/{tree}"},
        "parents": [{"sha": p, "url": f"{api}/repos/{owner}/{repo}/git/commits/{p}"} for p in parents],
    }

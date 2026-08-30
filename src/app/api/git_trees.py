"""Git Data API -- Trees."""

import asyncio
import os

from fastapi import APIRouter, HTTPException

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings

router = APIRouter(tags=["git-trees"])

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


@router.get("/repos/{owner}/{repo}/git/trees/{sha}")
async def get_tree(
    owner: str, repo: str, sha: str, db: DbSession, current_user: CurrentUser,
    recursive: str | None = None,
):
    """Get a Git tree."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Not Found")

    args = ["ls-tree", sha]
    if recursive:
        args.insert(1, "-r")

    try:
        out = await _git(repository.disk_path, *args)
    except RuntimeError:
        raise HTTPException(status_code=404, detail="Not Found")

    api = f"{BASE}/api/v3"
    tree_items = []
    for line in out.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t", 1)
        meta = parts[0].split()
        path = parts[1] if len(parts) > 1 else ""
        mode = meta[0] if len(meta) > 0 else ""
        obj_type = meta[1] if len(meta) > 1 else ""
        obj_sha = meta[2] if len(meta) > 2 else ""
        tree_items.append({
            "path": path,
            "mode": mode,
            "type": obj_type,
            "sha": obj_sha,
            "size": None if obj_type == "tree" else 0,
            "url": f"{api}/repos/{owner}/{repo}/git/{obj_type}s/{obj_sha}",
        })

    return {
        "sha": sha,
        "url": f"{api}/repos/{owner}/{repo}/git/trees/{sha}",
        "tree": tree_items,
        "truncated": False,
    }


@router.post("/repos/{owner}/{repo}/git/trees", status_code=201)
async def create_tree(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession
):
    """Create a Git tree."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Repository not found on disk")

    tree_entries = body.get("tree", [])
    base_tree = body.get("base_tree")

    lines = []
    if base_tree:
        try:
            existing = await _git(repository.disk_path, "ls-tree", base_tree)
            lines = [l for l in existing.strip().splitlines() if l]
        except RuntimeError:
            pass

    for entry in tree_entries:
        path = entry.get("path", "")
        mode = entry.get("mode", "100644")
        entry_type = entry.get("type", "blob")
        sha = entry.get("sha", "")
        # Remove any existing entry for this path
        lines = [l for l in lines if not l.endswith(f"\t{path}")]
        if sha:
            lines.append(f"{mode} {entry_type} {sha}\t{path}")

    tree_input = "\n".join(lines) + "\n" if lines else "\n"

    try:
        tree_sha = (await _git(repository.disk_path, "mktree", input_data=tree_input.encode())).strip()
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    api = f"{BASE}/api/v3"
    return {
        "sha": tree_sha,
        "url": f"{api}/repos/{owner}/{repo}/git/trees/{tree_sha}",
        "tree": [],
        "truncated": False,
    }

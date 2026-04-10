"""Git Data API -- References (refs)."""

import asyncio
import os

from fastapi import APIRouter, HTTPException

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings

router = APIRouter(tags=["git-refs"])

BASE = settings.BASE_URL


async def _git(repo_path: str, *args: str) -> str:
    env = {**os.environ, "GIT_DIR": repo_path}
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode())
    return stdout.decode()


def _ref_json(ref: str, sha: str, owner: str, repo_name: str, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    return {
        "ref": ref,
        "node_id": "",
        "url": f"{api}/repos/{owner}/{repo_name}/git/{ref}",
        "object": {
            "sha": sha,
            "type": "commit",
            "url": f"{api}/repos/{owner}/{repo_name}/git/commits/{sha}",
        },
    }


@router.get("/repos/{owner}/{repo}/git/refs")
async def list_refs(
    owner: str, repo: str, db: DbSession, current_user: CurrentUser
):
    """List all references."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        return []

    try:
        out = await _git(repository.disk_path, "for-each-ref", "--format=%(refname) %(objectname)")
    except RuntimeError:
        return []

    refs = []
    for line in out.strip().splitlines():
        if not line:
            continue
        parts = line.split()
        ref_name = parts[0]
        sha = parts[1] if len(parts) > 1 else ""
        refs.append(_ref_json(ref_name, sha, owner, repo, BASE))

    return refs


@router.get("/repos/{owner}/{repo}/git/ref/{ref:path}")
async def get_ref(
    owner: str, repo: str, ref: str, db: DbSession, current_user: CurrentUser
):
    """Get a single reference."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Not Found")

    full_ref = ref if ref.startswith("refs/") else f"refs/{ref}"
    try:
        sha = (await _git(repository.disk_path, "rev-parse", full_ref)).strip()
    except RuntimeError:
        raise HTTPException(status_code=404, detail="Not Found")

    return _ref_json(full_ref, sha, owner, repo, BASE)


@router.post("/repos/{owner}/{repo}/git/refs", status_code=201)
async def create_ref(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession
):
    """Create a reference."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Repository not found on disk")

    ref = body.get("ref", "")
    sha = body.get("sha", "")
    if not ref or not sha:
        raise HTTPException(status_code=422, detail="ref and sha are required")

    try:
        await _git(repository.disk_path, "update-ref", ref, sha)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return _ref_json(ref, sha, owner, repo, BASE)


@router.patch("/repos/{owner}/{repo}/git/refs/{ref:path}")
async def update_ref(
    owner: str, repo: str, ref: str, body: dict, user: AuthUser, db: DbSession
):
    """Update a reference."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Repository not found on disk")

    full_ref = ref if ref.startswith("refs/") else f"refs/{ref}"
    sha = body.get("sha", "")
    force = body.get("force", False)

    args = ["update-ref", full_ref, sha]
    if not force:
        try:
            old_sha = (await _git(repository.disk_path, "rev-parse", full_ref)).strip()
            args.append(old_sha)
        except RuntimeError:
            pass

    try:
        await _git(repository.disk_path, *args)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return _ref_json(full_ref, sha, owner, repo, BASE)


@router.delete("/repos/{owner}/{repo}/git/refs/{ref:path}", status_code=204)
async def delete_ref(
    owner: str, repo: str, ref: str, user: AuthUser, db: DbSession
):
    """Delete a reference."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Repository not found on disk")

    full_ref = ref if ref.startswith("refs/") else f"refs/{ref}"
    try:
        await _git(repository.disk_path, "update-ref", "-d", full_ref)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

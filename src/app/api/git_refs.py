"""Git Data API -- References (refs)."""

import asyncio
import os

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.branch import Branch
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.middleware.error_handler import ValidationError
from app.schemas.browse import TagResponse
from app.schemas.user import _make_node_id

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


def _branch_name(ref: str) -> str | None:
    normalized = ref if ref.startswith("refs/") else f"refs/{ref}"
    if normalized.startswith("refs/heads/"):
        return normalized.removeprefix("refs/heads/")
    return None


@router.get("/repos/{owner}/{repo}/tags", response_model=list[TagResponse])
async def list_tags(
    owner: str, repo: str, db: DbSession, current_user: CurrentUser
):
    """List annotated and lightweight tags in GitHub's repository shape."""
    repository = await get_repo_or_404(owner, repo, db)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        return []
    try:
        out = await _git(
            repository.disk_path,
            "for-each-ref",
            "--format=%(refname:strip=2)%00%(*objectname)%00%(objectname)",
            "refs/tags",
        )
    except RuntimeError:
        return []

    api = f"{BASE}/api/v3/repos/{owner}/{repo}"
    values = []
    for line in out.splitlines():
        name, peeled_sha, object_sha = (line.split("\x00") + ["", ""])[:3]
        sha = peeled_sha or object_sha
        if not name or not sha:
            continue
        values.append(
            {
                "name": name,
                "commit": {"sha": sha, "url": f"{api}/commits/{sha}"},
                "zipball_url": f"{api}/zipball/{name}",
                "tarball_url": f"{api}/tarball/{name}",
                "node_id": _make_node_id("Tag", hash(f"{repository.id}:{name}") % 10**8),
            }
        )
    return values


async def _get_branch_record(db, repository, ref: str) -> Branch | None:
    name = _branch_name(ref)
    if name is None:
        return None
    result = await db.execute(
        select(Branch).where(Branch.repo_id == repository.id, Branch.name == name)
    )
    return result.scalar_one_or_none()


async def _record_branch_change(db, repository, ref: str, sha: str | None, user) -> None:
    name = _branch_name(ref)
    if name is None:
        return
    branch = await _get_branch_record(db, repository, ref)
    if sha is None:
        if branch is not None:
            await db.delete(branch)
    elif branch is None:
        db.add(Branch(repo_id=repository.id, name=name, sha=sha))
    else:
        branch.sha = sha

    pull_requests = (
        await db.execute(
            select(PullRequest)
            .join(Issue, PullRequest.issue_id == Issue.id)
            .where(
                PullRequest.repo_id == repository.id,
                PullRequest.head_ref == name,
                Issue.state == "open",
            )
        )
    ).scalars().all()
    if sha is not None:
        for pull_request in pull_requests:
            pull_request.head_sha = sha
            pull_request.last_push_by_id = user.id
    await db.commit()

    from app.services.merge_readiness_service import reevaluate_auto_merges

    await reevaluate_auto_merges(db, repository.id)


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

    await _record_branch_change(db, repository, ref, sha, user)
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

    branch = await _get_branch_record(db, repository, full_ref)
    if branch is not None and branch.protection is not None:
        if branch.protection.lock_branch:
            raise ValidationError(message="Protected branch is locked")
        if force and not branch.protection.allow_force_pushes:
            raise ValidationError(message="Cannot force-update this protected branch")

    args = ["update-ref", full_ref, sha]
    old_sha = None
    if not force:
        try:
            old_sha = (await _git(repository.disk_path, "rev-parse", full_ref)).strip()
            args.append(old_sha)
        except RuntimeError:
            pass
        if old_sha:
            try:
                await _git(repository.disk_path, "merge-base", "--is-ancestor", old_sha, sha)
            except RuntimeError:
                raise ValidationError(message="Update is not a fast forward")

    try:
        await _git(repository.disk_path, *args)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    await _record_branch_change(db, repository, full_ref, sha, user)
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
    branch = await _get_branch_record(db, repository, full_ref)
    if (
        branch is not None
        and branch.protection is not None
        and not branch.protection.allow_deletions
    ):
        raise ValidationError(message="Cannot delete this protected branch")
    try:
        await _git(repository.disk_path, "update-ref", "-d", full_ref)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await _record_branch_change(db, repository, full_ref, None, user)

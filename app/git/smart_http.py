"""Git Smart HTTP protocol handler.

Implements the three endpoints needed for git clone/push/pull over HTTP:
- GET /{owner}/{repo}.git/info/refs?service=git-upload-pack|git-receive-pack
- POST /{owner}/{repo}.git/git-upload-pack
- POST /{owner}/{repo}.git/git-receive-pack

Also supports URLs without .git suffix.

Reference: https://git-scm.com/docs/http-protocol
"""

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.branch import Branch
from app.models.repository import Repository
from app.models.user import User
from app.api.deps import get_current_user
from app.git.bare_repo import get_branches as get_disk_branches

router = APIRouter()


def pkt_line(data: str) -> bytes:
    """Encode a string as a pkt-line (4-byte hex length prefix + data)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    length = len(data) + 4
    return f"{length:04x}".encode("ascii") + data


def pkt_flush() -> bytes:
    """Return a flush packet (0000)."""
    return b"0000"


async def _resolve_repo(
    db: AsyncSession, owner: str, repo: str
) -> Repository:
    """Resolve owner/repo to a Repository, stripping .git suffix if present."""
    if repo.endswith(".git"):
        repo = repo[:-4]
    result = await db.execute(
        select(Repository).where(Repository.full_name == f"{owner}/{repo}")
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repository


async def _check_read_access(
    repository: Repository, user: User | None
) -> None:
    """Check if user has read access to the repository."""
    if repository.private and (user is None or user.id != repository.owner_id):
        raise HTTPException(status_code=404, detail="Repository not found")


async def _check_write_access(
    repository: Repository, user: User | None
) -> None:
    """Check if user has write access to the repository."""
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="GitHub Emulator"'},
        )
    if user.id != repository.owner_id and not user.site_admin:
        # TODO: check collaborator access
        raise HTTPException(status_code=403, detail="Permission denied")


async def _run_git_command(
    args: list[str],
    repo_path: str,
    input_data: bytes | None = None,
) -> tuple[bytes, bytes]:
    """Run a git command and return (stdout, stderr)."""
    env = os.environ.copy()
    env["GIT_DIR"] = repo_path

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if input_data is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate(input=input_data)
    return stdout, stderr


async def _sync_branches_to_db(
    db: AsyncSession, repository: Repository
) -> None:
    """Sync branch refs from the on-disk bare repo into the branches table."""
    disk_branches = await get_disk_branches(repository.disk_path)
    disk_map = {b["name"]: b["sha"] for b in disk_branches}

    # Fetch existing DB branches for this repo
    result = await db.execute(
        select(Branch).where(Branch.repo_id == repository.id)
    )
    existing = {b.name: b for b in result.scalars().all()}

    # Update or insert branches that exist on disk
    for name, sha in disk_map.items():
        if name in existing:
            if existing[name].sha != sha:
                existing[name].sha = sha
        else:
            db.add(Branch(repo_id=repository.id, name=name, sha=sha))

    # Delete branches that no longer exist on disk
    for name, branch in existing.items():
        if name not in disk_map:
            await db.delete(branch)

    await db.commit()


async def _stream_git_command(
    args: list[str],
    repo_path: str,
    input_data: bytes,
):
    """Run a git command and stream its output."""
    env = os.environ.copy()
    env["GIT_DIR"] = repo_path

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    # Write input and close stdin
    proc.stdin.write(input_data)
    await proc.stdin.drain()
    proc.stdin.close()
    await proc.stdin.wait_closed()

    # Stream stdout
    while True:
        chunk = await proc.stdout.read(65536)
        if not chunk:
            break
        yield chunk

    await proc.wait()


@router.get("/{owner}/{repo_name}/info/refs")
@router.get("/{owner}/{repo_name}.git/info/refs")
async def info_refs(
    owner: str,
    repo_name: str,
    service: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """Git Smart HTTP reference discovery.

    Returns the list of refs in the repository for clone/fetch/push operations.
    """
    if service not in ("git-upload-pack", "git-receive-pack"):
        raise HTTPException(status_code=403, detail="Invalid service")

    repository = await _resolve_repo(db, owner, repo_name)

    # Check access
    if service == "git-receive-pack":
        await _check_write_access(repository, user)
    else:
        await _check_read_access(repository, user)

    repo_path = repository.disk_path
    if not os.path.isdir(repo_path):
        raise HTTPException(status_code=404, detail="Repository not found on disk")

    # Run git service with --advertise-refs
    stdout, stderr = await _run_git_command(
        [service, "--stateless-rpc", "--advertise-refs", repo_path],
        repo_path,
    )

    # Build response: service announcement + refs
    body = pkt_line(f"# service={service}\n") + pkt_flush() + stdout

    return Response(
        content=body,
        media_type=f"application/x-{service}-advertisement",
        headers={
            "Cache-Control": "no-cache",
            "Expires": "Fri, 01 Jan 1980 00:00:00 GMT",
            "Pragma": "no-cache",
        },
    )


@router.post("/{owner}/{repo_name}/git-upload-pack")
@router.post("/{owner}/{repo_name}.git/git-upload-pack")
async def git_upload_pack(
    owner: str,
    repo_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """Git Smart HTTP upload-pack (fetch/clone).

    Pipes request body to git-upload-pack and streams the response.
    """
    repository = await _resolve_repo(db, owner, repo_name)
    await _check_read_access(repository, user)

    repo_path = repository.disk_path
    if not os.path.isdir(repo_path):
        raise HTTPException(status_code=404, detail="Repository not found on disk")

    input_data = await request.body()

    return StreamingResponse(
        _stream_git_command(
            ["git-upload-pack", "--stateless-rpc", repo_path],
            repo_path,
            input_data,
        ),
        media_type="application/x-git-upload-pack-result",
        headers={
            "Cache-Control": "no-cache",
            "Expires": "Fri, 01 Jan 1980 00:00:00 GMT",
            "Pragma": "no-cache",
        },
    )


@router.post("/{owner}/{repo_name}/git-receive-pack")
@router.post("/{owner}/{repo_name}.git/git-receive-pack")
async def git_receive_pack(
    owner: str,
    repo_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """Git Smart HTTP receive-pack (push).

    Pipes request body to git-receive-pack and streams the response.
    Requires authentication with write access.
    """
    repository = await _resolve_repo(db, owner, repo_name)
    await _check_write_access(repository, user)

    repo_path = repository.disk_path
    if not os.path.isdir(repo_path):
        raise HTTPException(status_code=404, detail="Repository not found on disk")

    input_data = await request.body()

    # Run receive-pack
    stdout, stderr = await _run_git_command(
        ["git-receive-pack", "--stateless-rpc", repo_path],
        repo_path,
        input_data,
    )

    # Update pushed_at timestamp
    from datetime import datetime, timezone

    repository.pushed_at = datetime.now(timezone.utc)
    await db.commit()

    # Sync branch refs from disk into the database
    try:
        await _sync_branches_to_db(db, repository)
    except Exception:
        pass  # Don't fail the push if branch sync fails

    # Trigger search indexing in the background
    try:
        from app.services.index_service import index_repository
        await index_repository(db, repository)
    except Exception:
        pass  # Don't fail the push if indexing fails

    return Response(
        content=stdout,
        media_type="application/x-git-receive-pack-result",
        headers={
            "Cache-Control": "no-cache",
            "Expires": "Fri, 01 Jan 1980 00:00:00 GMT",
            "Pragma": "no-cache",
        },
    )

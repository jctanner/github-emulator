"""Git Smart HTTP protocol handler.

Implements the three endpoints needed for git clone/push/pull over HTTP:
- GET /{owner}/{repo}.git/info/refs?service=git-upload-pack|git-receive-pack
- POST /{owner}/{repo}.git/git-upload-pack
- POST /{owner}/{repo}.git/git-receive-pack

Also supports URLs without .git suffix.

Reference: https://git-scm.com/docs/http-protocol
"""

import asyncio
import contextlib
import os
import tempfile
import zlib
from datetime import datetime, timezone

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


async def _stream_git_command_from_file(
    args: list[str],
    repo_path: str,
    input_path: str,
):
    """Run a git command with stdin from a file while streaming stdout."""
    env = os.environ.copy()
    env["GIT_DIR"] = repo_path

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    async def write_stdin() -> None:
        try:
            with open(input_path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with contextlib.suppress(Exception):
                proc.stdin.close()
                await proc.stdin.wait_closed()

    async def drain_stderr() -> bytes:
        return await proc.stderr.read()

    stdin_task = asyncio.create_task(write_stdin())
    stderr_task = asyncio.create_task(drain_stderr())
    try:
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            yield chunk

        await proc.wait()
        await stdin_task
        await stderr_task
    finally:
        if proc.returncode is None:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
        for task in (stdin_task, stderr_task):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


async def _spool_request_body(request: Request, prefix: str = "git-request-") -> str:
    """Write a possibly large request body to a temporary file."""
    content_encoding = request.headers.get("content-encoding", "").lower()
    encodings = [value.strip() for value in content_encoding.split(",") if value.strip()]
    decompressor = None
    if encodings in ([], ["identity"]):
        pass
    elif encodings in (["gzip"], ["x-gzip"]):
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    else:
        raise HTTPException(status_code=415, detail="Unsupported Git request encoding")

    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".pack")
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                if chunk:
                    if decompressor is not None:
                        chunk = decompressor.decompress(chunk)
                    if chunk:
                        f.write(chunk)
            if decompressor is not None:
                tail = decompressor.flush()
                if tail:
                    f.write(tail)
        return path
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        raise


async def _run_git_command_from_file(
    args: list[str],
    repo_path: str,
    input_path: str,
) -> tuple[int, bytes, bytes]:
    """Run a git command with stdin read incrementally from a file."""
    env = os.environ.copy()
    env["GIT_DIR"] = repo_path

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    async def write_stdin() -> None:
        try:
            with open(input_path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with contextlib.suppress(Exception):
                proc.stdin.close()
                await proc.stdin.wait_closed()

    stdin_task = asyncio.create_task(write_stdin())
    stdout, stderr = await asyncio.gather(proc.stdout.read(), proc.stderr.read())
    exit_code = await proc.wait()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await stdin_task
    return exit_code, stdout, stderr


async def _post_receive_pack_tasks(repo_id: int, user_id: int | None) -> None:
    """Run expensive post-push side effects outside the Git HTTP response path."""
    from sqlalchemy import select

    from app.database import async_session

    async with async_session() as db:
        result = await db.execute(select(Repository).where(Repository.id == repo_id))
        repository = result.scalar_one_or_none()
        if repository is None:
            return

        user = None
        if user_id is not None:
            user_result = await db.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()

        repository.pushed_at = datetime.now(timezone.utc)
        await db.commit()

        previous_branches = {
            branch.name: branch.sha
            for branch in (await db.execute(
                select(Branch).where(Branch.repo_id == repository.id)
            )).scalars().all()
        }
        changed_branch = repository.default_branch or "main"
        changed_before = previous_branches.get(changed_branch)
        try:
            disk_branches = await get_disk_branches(repository.disk_path)
            for branch in disk_branches:
                if previous_branches.get(branch["name"]) != branch["sha"]:
                    changed_branch = branch["name"]
                    changed_before = previous_branches.get(branch["name"])
                    break
        except Exception:
            pass

        try:
            await _sync_branches_to_db(db, repository)
        except Exception:
            pass

        try:
            from app.services.index_service import index_repository

            await index_repository(db, repository)
        except Exception:
            pass

        try:
            from app.services.workflow_service import process_push_event

            await process_push_event(
                db,
                repository,
                user,
                before_sha=changed_before,
                ref_name=changed_branch,
            )
        except Exception:
            pass


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

    input_path = await _spool_request_body(request, prefix="git-upload-pack-")

    async def stream_and_cleanup():
        try:
            async for chunk in _stream_git_command_from_file(
                ["git-upload-pack", "--stateless-rpc", repo_path],
                repo_path,
                input_path,
            ):
                yield chunk
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(input_path)

    return StreamingResponse(
        stream_and_cleanup(),
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

    repo_id = repository.id
    user_id = user.id if user else None
    input_path = await _spool_request_body(request, prefix="git-receive-pack-")
    try:
        exit_code, stdout, stderr = await _run_git_command_from_file(
            ["git-receive-pack", "--stateless-rpc", repo_path],
            repo_path,
            input_path,
        )
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(input_path)

    if exit_code == 0:
        asyncio.create_task(_post_receive_pack_tasks(repo_id, user_id))

    return Response(
        content=stdout,
        media_type="application/x-git-receive-pack-result",
        headers={
            "Cache-Control": "no-cache",
            "Expires": "Fri, 01 Jan 1980 00:00:00 GMT",
            "Pragma": "no-cache",
        },
    )

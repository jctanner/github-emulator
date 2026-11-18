"""Contents endpoints -- file/dir CRUD and README retrieval."""

import asyncio
import base64
import os
import tempfile

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_record_or_404, require_contents_write
from app.config import settings
from app.git.bare_repo import parse_tree, read_object
from app.schemas.browse import ContentResponse
from app.schemas.user import _make_node_id
from app.services.api_push import after_api_commit
from app.services.branch_service import record_moved_branch

router = APIRouter(tags=["contents"])

BASE = settings.BASE_URL


async def _git(
    repo_path: str,
    *args: str,
    input_data: bytes | None = None,
    env_overrides: dict[str, str] | None = None,
) -> str:
    env = {
        **os.environ,
        "GIT_DIR": repo_path,
        "GIT_AUTHOR_NAME": "GitHub Emulator",
        "GIT_AUTHOR_EMAIL": "emulator@github-emulator.local",
        "GIT_COMMITTER_NAME": "GitHub Emulator",
        "GIT_COMMITTER_EMAIL": "emulator@github-emulator.local",
    }
    if env_overrides:
        env.update(env_overrides)
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


def _file_response(
    owner: str, repo_name: str, path: str, content_bytes: bytes,
    sha: str, ref: str, base_url: str, with_content: bool = True,
) -> dict:
    api = f"{base_url}/api/v3"
    encoded = base64.b64encode(content_bytes).decode() if with_content else ""
    return {
        "type": "file",
        "encoding": "base64" if with_content else "none",
        "size": len(content_bytes),
        "name": os.path.basename(path),
        "path": path,
        "content": encoded,
        "sha": sha,
        "url": f"{api}/repos/{owner}/{repo_name}/contents/{path}?ref={ref}",
        "git_url": f"{api}/repos/{owner}/{repo_name}/git/blobs/{sha}",
        "html_url": f"{base_url}/{owner}/{repo_name}/blob/{ref}/{path}",
        "download_url": f"{base_url}/{owner}/{repo_name}/raw/{ref}/{path}",
        "_links": {
            "self": f"{api}/repos/{owner}/{repo_name}/contents/{path}?ref={ref}",
            "git": f"{api}/repos/{owner}/{repo_name}/git/blobs/{sha}",
            "html": f"{base_url}/{owner}/{repo_name}/blob/{ref}/{path}",
        },
    }


# Media types that ask GET contents and GET readme for the file's bytes
# instead of the JSON object. GitHub answers with the bytes and echoes the
# type as the Content-Type; a directory still gets the JSON listing.
_RAW_MEDIA_TYPES = (
    "application/vnd.github.raw",
    "application/vnd.github.v3.raw",
    "application/vnd.github.raw+json",
)

# From 1 MiB (1,048,576 bytes, checked on github.com) GitHub's JSON object
# carries no content: "encoding" is "none" and "content" is "". The raw
# media type still returns the bytes.
_JSON_CONTENT_LIMIT = 1024 * 1024


def _raw_media_type(request: Request) -> str | None:
    """The raw media type the Accept header asks for, if any.

    A media range with ``q=0`` is one the client does not accept, so it is
    not selected.
    """
    for part in request.headers.get("accept", "").split(","):
        media, *params = part.split(";")
        media = media.strip().lower()
        if media not in _RAW_MEDIA_TYPES:
            continue
        q = 1.0
        for param in params:
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    q = float(value.strip())
                except ValueError:
                    q = 1.0
        if q > 0:
            return media
    return None


def _get_file_response(
    request: Request, owner: str, repo_name: str, path: str, content_bytes: bytes,
    sha: str, ref: str,
) -> dict | Response:
    """A file for GET contents or GET readme, in the media type asked for."""
    media = _raw_media_type(request)
    if media is not None:
        return Response(content=content_bytes, media_type=media)
    return _file_response(
        owner, repo_name, path, content_bytes, sha, ref, BASE,
        with_content=len(content_bytes) < _JSON_CONTENT_LIMIT,
    )


@router.get(
    "/repos/{owner}/{repo}/contents/{path:path}",
    response_model=ContentResponse | list[ContentResponse],
)
async def get_contents(
    owner: str, repo: str, path: str, request: Request, db: DbSession, current_user: CurrentUser,
    ref: str | None = None,
):
    """Get file or directory contents."""
    repository = await get_repo_record_or_404(owner, repo, db)

    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Not Found")

    branch = ref or repository.default_branch

    # One git process per call: resolve the path and read the object
    # together. A job reads the agents harness with about a thousand of these
    # calls, and on a loaded machine each process start is the main cost.
    found = await read_object(repository.disk_path, f"{branch}:{path}")
    if found is None:
        raise HTTPException(status_code=404, detail="Not Found")
    object_sha, obj_type, content_bytes = found

    if obj_type == "blob":
        return _get_file_response(request, owner, repo, path, content_bytes, object_sha, branch)
    elif obj_type == "tree":
        api = f"{BASE}/api/v3"
        entries = []
        for mode, entry_name, entry_sha in parse_tree(content_bytes, len(object_sha) // 2):
            entry_path = f"{path}/{entry_name}".strip("/")
            # A tree or a submodule (gitlink) lists as "dir", anything else
            # as "file", as before.
            t = "dir" if mode in ("40000", "160000") else "file"
            entries.append({
                "type": t,
                "size": 0,
                "name": entry_name,
                "path": entry_path,
                "sha": entry_sha,
                "url": f"{api}/repos/{owner}/{repo}/contents/{entry_path}?ref={branch}",
                "git_url": f"{api}/repos/{owner}/{repo}/git/{('blobs' if t == 'file' else 'trees')}/{entry_sha}",
                "html_url": f"{BASE}/{owner}/{repo}/{'blob' if t == 'file' else 'tree'}/{branch}/{entry_path}",
                "download_url": f"{BASE}/{owner}/{repo}/raw/{branch}/{entry_path}" if t == "file" else None,
                "_links": {
                    "self": f"{api}/repos/{owner}/{repo}/contents/{entry_path}?ref={branch}",
                    "git": f"{api}/repos/{owner}/{repo}/git/{('blobs' if t == 'file' else 'trees')}/{entry_sha}",
                    "html": f"{BASE}/{owner}/{repo}/{'blob' if t == 'file' else 'tree'}/{branch}/{entry_path}",
                },
            })
        return entries
    else:
        raise HTTPException(status_code=404, detail="Not Found")


@router.put("/repos/{owner}/{repo}/contents/{path:path}")
async def create_or_update_file(
    owner: str, repo: str, path: str, body: dict, user: AuthUser, db: DbSession,
):
    """Create or update a file."""
    repository = await get_repo_record_or_404(owner, repo, db)
    await require_contents_write(db, repository, user)

    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Repository not found on disk")

    message = body.get("message", f"Update {path}")
    content_b64 = body.get("content", "")
    branch = body.get("branch", repository.default_branch)

    try:
        content_bytes = base64.b64decode(content_b64)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid base64 content") from None

    repo_path = repository.disk_path

    # Write blob
    blob_sha = (await _git(repo_path, "hash-object", "-w", "--stdin", input_data=content_bytes)).strip()

    # Get current tree
    is_new_file = True
    current_sha = None
    try:
        parent_sha = (await _git(repo_path, "rev-parse", "--verify", f"refs/heads/{branch}")).strip()
        tree_sha = (await _git(repo_path, "rev-parse", f"{parent_sha}^{{tree}}")).strip()
        # Check if the file already exists
        try:
            current_sha = (await _git(repo_path, "rev-parse", f"{parent_sha}:{path}")).strip()
            is_new_file = False
        except RuntimeError:
            pass
    except RuntimeError:
        parent_sha = None
        tree_sha = None

    # Updating an existing file needs its current blob sha, so a stale caller
    # cannot overwrite a newer version. GitHub ignores ``sha`` on a create.
    sha = body.get("sha")
    if not is_new_file:
        if not sha:
            raise HTTPException(status_code=422, detail='Invalid request.\n\n"sha" wasn\'t supplied.')
        if sha != current_sha:
            raise HTTPException(status_code=409, detail=f"{path} does not match {sha}")

    # Build a new tree by applying the path through a temporary index.  Passing
    # a nested path directly to ``git mktree`` fails because mktree accepts only
    # entries for the tree currently being written; update-index/write-tree
    # creates any missing intermediate trees for us.
    if tree_sha:
        index_fd, index_path = tempfile.mkstemp(prefix="github-emulator-index-")
        os.close(index_fd)
        try:
            os.unlink(index_path)
            index_env = {"GIT_INDEX_FILE": index_path}
            await _git(repo_path, "read-tree", tree_sha, env_overrides=index_env)
            await _git(
                repo_path,
                "update-index",
                "--add",
                "--cacheinfo",
                "100644",
                blob_sha,
                path,
                env_overrides=index_env,
            )
            new_tree_sha = (
                await _git(repo_path, "write-tree", env_overrides=index_env)
            ).strip()
        finally:
            try:
                os.unlink(index_path)
            except FileNotFoundError:
                pass
    else:
        index_fd, index_path = tempfile.mkstemp(prefix="github-emulator-index-")
        os.close(index_fd)
        try:
            os.unlink(index_path)
            index_env = {"GIT_INDEX_FILE": index_path}
            await _git(
                repo_path,
                "update-index",
                "--add",
                "--cacheinfo",
                "100644",
                blob_sha,
                path,
                env_overrides=index_env,
            )
            new_tree_sha = (
                await _git(repo_path, "write-tree", env_overrides=index_env)
            ).strip()
        finally:
            try:
                os.unlink(index_path)
            except FileNotFoundError:
                pass

    # Create commit
    env_args = [
        "commit-tree", new_tree_sha, "-m", message,
    ]
    if parent_sha:
        env_args.extend(["-p", parent_sha])

    commit_sha = (await _git(repo_path, *env_args)).strip()

    # Move the branch only if it is still where it was read (an empty old
    # value means it must not exist yet), so of two concurrent writers with
    # the same sha one gets the 409 instead of both succeeding.
    try:
        await _git(repo_path, "update-ref", f"refs/heads/{branch}", commit_sha, parent_sha or "")
    except RuntimeError:
        raise HTTPException(status_code=409, detail=f"{path} does not match {sha}" if sha else "Conflict") from None
    repository = await record_moved_branch(
        db, repository, branch, parent_sha, commit_sha, refresh=(user,),
    )
    await after_api_commit(db, repository, user, branch, parent_sha, commit_sha)

    api = f"{BASE}/api/v3"
    result = {
        "content": _file_response(owner, repo, path, content_bytes, blob_sha, branch, BASE),
        "commit": {
            "sha": commit_sha,
            "node_id": _make_node_id("Commit", hash(commit_sha) % 10**8),
            "url": f"{api}/repos/{owner}/{repo}/git/commits/{commit_sha}",
            "html_url": f"{BASE}/{owner}/{repo}/commit/{commit_sha}",
            "message": message,
        },
    }
    return JSONResponse(content=result, status_code=201 if is_new_file else 200)


@router.delete("/repos/{owner}/{repo}/contents/{path:path}")
async def delete_file(
    owner: str, repo: str, path: str, body: dict, user: AuthUser, db: DbSession,
):
    """Delete a file with a commit on the branch, as GitHub does.

    ``sha`` is required and must be the file's current blob, so a stale
    caller cannot delete a newer version. The commit is a push like any
    other (``after_api_commit``).
    """
    repository = await get_repo_record_or_404(owner, repo, db)
    await require_contents_write(db, repository, user)
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Repository not found on disk")
    message = body.get("message")
    sha = body.get("sha")
    if not message or not sha:
        raise HTTPException(status_code=422, detail="Invalid request. message and sha are required")
    branch = body.get("branch", repository.default_branch)
    repo_path = repository.disk_path
    try:
        parent_sha = (await _git(repo_path, "rev-parse", "--verify", f"refs/heads/{branch}")).strip()
        current = (await _git(repo_path, "rev-parse", f"{parent_sha}:{path}")).strip()
    except RuntimeError:
        raise HTTPException(status_code=404, detail="Not Found") from None
    if current != sha:
        raise HTTPException(status_code=409, detail=f"{path} does not match {sha}")

    index_fd, index_path = tempfile.mkstemp(prefix="github-emulator-index-")
    os.close(index_fd)
    try:
        os.unlink(index_path)
        index_env = {"GIT_INDEX_FILE": index_path}
        await _git(repo_path, "read-tree", parent_sha, env_overrides=index_env)
        # A mode-0 entry removes the path (NUL-terminated, so any filename
        # is one record); --force-remove needs a work tree.
        await _git(
            repo_path, "update-index", "-z", "--index-info",
            input_data=f"0 {'0' * 40}\t{path}\0".encode(), env_overrides=index_env,
        )
        new_tree_sha = (await _git(repo_path, "write-tree", env_overrides=index_env)).strip()
    finally:
        try:
            os.unlink(index_path)
        except FileNotFoundError:
            pass
    commit_sha = (await _git(repo_path, "commit-tree", new_tree_sha, "-m", message, "-p", parent_sha)).strip()
    await _git(repo_path, "update-ref", f"refs/heads/{branch}", commit_sha, parent_sha)
    repository = await record_moved_branch(
        db, repository, branch, parent_sha, commit_sha, refresh=(user,),
    )
    await after_api_commit(db, repository, user, branch, parent_sha, commit_sha)

    api = f"{BASE}/api/v3"
    return {
        "content": None,
        "commit": {
            "sha": commit_sha,
            "node_id": _make_node_id("Commit", hash(commit_sha) % 10**8),
            "url": f"{api}/repos/{owner}/{repo}/git/commits/{commit_sha}",
            "html_url": f"{BASE}/{owner}/{repo}/commit/{commit_sha}",
            "message": message,
        },
    }


README_NAMES = ["README.md", "README.rst", "README.txt", "README", "readme.md"]


async def _readme_response(
    request: Request, owner: str, repo: str, db, ref: str | None, directory: str = "",
) -> dict | Response:
    """Return the README in ``directory`` (the repository root when empty), as GitHub does."""
    repository = await get_repo_record_or_404(owner, repo, db)

    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Not Found")

    branch = ref or repository.default_branch
    directory = directory.strip("/")
    if directory and any(part in ("", ".", "..") for part in directory.split("/")):
        raise HTTPException(status_code=404, detail="Not Found")

    # Try common README filenames
    for readme_name in README_NAMES:
        path = f"{directory}/{readme_name}" if directory else readme_name
        found = await read_object(repository.disk_path, f"{branch}:{path}")
        if found is not None and found[1] == "blob":
            return _get_file_response(request, owner, repo, path, found[2], found[0], branch)

    raise HTTPException(status_code=404, detail="Not Found")


@router.get("/repos/{owner}/{repo}/readme", response_model=ContentResponse)
async def get_readme(
    owner: str, repo: str, request: Request, db: DbSession, current_user: CurrentUser,
    ref: str | None = None,
):
    """Get the README for a repository."""
    return await _readme_response(request, owner, repo, db, ref)


@router.get("/repos/{owner}/{repo}/readme/{dir:path}", response_model=ContentResponse)
async def get_directory_readme(
    owner: str, repo: str, dir: str, request: Request, db: DbSession, current_user: CurrentUser,
    ref: str | None = None,
):
    """Get the README in a directory (GitHub's ``GET /repos/{owner}/{repo}/readme/{dir}``)."""
    return await _readme_response(request, owner, repo, db, ref, dir)

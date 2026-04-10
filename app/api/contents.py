"""Contents endpoints -- file/dir CRUD and README retrieval."""

import asyncio
import base64
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.schemas.user import _make_node_id

router = APIRouter(tags=["contents"])

BASE = settings.BASE_URL


async def _git(repo_path: str, *args: str, input_data: bytes | None = None) -> str:
    env = {
        **os.environ,
        "GIT_DIR": repo_path,
        "GIT_AUTHOR_NAME": "GitHub Emulator",
        "GIT_AUTHOR_EMAIL": "emulator@github-emulator.local",
        "GIT_COMMITTER_NAME": "GitHub Emulator",
        "GIT_COMMITTER_EMAIL": "emulator@github-emulator.local",
    }
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
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode())
    return stdout


def _file_response(
    owner: str, repo_name: str, path: str, content_bytes: bytes,
    sha: str, ref: str, base_url: str,
) -> dict:
    api = f"{base_url}/api/v3"
    encoded = base64.b64encode(content_bytes).decode()
    return {
        "type": "file",
        "encoding": "base64",
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


@router.get("/repos/{owner}/{repo}/contents/{path:path}")
async def get_contents(
    owner: str, repo: str, path: str, db: DbSession, current_user: CurrentUser,
    ref: str | None = None,
):
    """Get file or directory contents."""
    repository = await get_repo_or_404(owner, repo, db)

    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Not Found")

    branch = ref or repository.default_branch

    # Try to get as file first
    try:
        blob_sha = (await _git(repository.disk_path, "rev-parse", f"{branch}:{path}")).strip()
        # Check if it's a blob (file) or tree (directory)
        obj_type = (await _git(repository.disk_path, "cat-file", "-t", blob_sha)).strip()
    except RuntimeError:
        raise HTTPException(status_code=404, detail="Not Found")

    if obj_type == "blob":
        content_bytes = await _git_bytes(repository.disk_path, "cat-file", "blob", blob_sha)
        return _file_response(owner, repo, path, content_bytes, blob_sha, branch, BASE)
    elif obj_type == "tree":
        # List directory
        try:
            ls_out = await _git(repository.disk_path, "ls-tree", blob_sha)
        except RuntimeError:
            raise HTTPException(status_code=404, detail="Not Found")

        api = f"{BASE}/api/v3"
        entries = []
        for line in ls_out.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t", 1)
            meta = parts[0].split()
            entry_name = parts[1] if len(parts) > 1 else ""
            entry_sha = meta[2] if len(meta) > 2 else ""
            entry_type = meta[1] if len(meta) > 1 else "blob"

            entry_path = f"{path}/{entry_name}".strip("/")
            t = "file" if entry_type == "blob" else "dir"
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
    repository = await get_repo_or_404(owner, repo, db)

    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Repository not found on disk")

    message = body.get("message", f"Update {path}")
    content_b64 = body.get("content", "")
    branch = body.get("branch", repository.default_branch)

    try:
        content_bytes = base64.b64decode(content_b64)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid base64 content")

    repo_path = repository.disk_path

    # Write blob
    blob_sha = (await _git(repo_path, "hash-object", "-w", "--stdin", input_data=content_bytes)).strip()

    # Get current tree
    is_new_file = True
    try:
        parent_sha = (await _git(repo_path, "rev-parse", branch)).strip()
        tree_sha = (await _git(repo_path, "rev-parse", f"{branch}^{{tree}}")).strip()
        # Check if the file already exists
        try:
            await _git(repo_path, "rev-parse", f"{branch}:{path}")
            is_new_file = False
        except RuntimeError:
            pass
    except RuntimeError:
        parent_sha = None
        tree_sha = None

    # Build new tree by reading the current one and adding/updating the entry
    if tree_sha:
        tree_info = await _git(repo_path, "ls-tree", tree_sha)
        lines = [l for l in tree_info.strip().splitlines() if l]
        # Remove existing entry for this path
        lines = [l for l in lines if not l.endswith(f"\t{path}")]
        lines.append(f"100644 blob {blob_sha}\t{path}")
        tree_input = "\n".join(lines) + "\n"
    else:
        tree_input = f"100644 blob {blob_sha}\t{path}\n"

    new_tree_sha = (await _git(repo_path, "mktree", input_data=tree_input.encode())).strip()

    # Create commit
    env_args = [
        "commit-tree", new_tree_sha, "-m", message,
    ]
    if parent_sha:
        env_args.extend(["-p", parent_sha])

    commit_sha = (await _git(repo_path, *env_args)).strip()

    # Update ref
    await _git(repo_path, "update-ref", f"refs/heads/{branch}", commit_sha)

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
    """Delete a file (stub -- returns 200 with commit info)."""
    repository = await get_repo_or_404(owner, repo, db)
    message = body.get("message", f"Delete {path}")
    sha = body.get("sha", "")

    return {
        "content": None,
        "commit": {
            "sha": "0" * 40,
            "message": message,
        },
    }


@router.get("/repos/{owner}/{repo}/readme")
async def get_readme(
    owner: str, repo: str, db: DbSession, current_user: CurrentUser,
    ref: str | None = None,
):
    """Get the README for a repository."""
    repository = await get_repo_or_404(owner, repo, db)

    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Not Found")

    branch = ref or repository.default_branch

    # Try common README filenames
    for readme_name in ["README.md", "README.rst", "README.txt", "README", "readme.md"]:
        try:
            blob_sha = (await _git(repository.disk_path, "rev-parse", f"{branch}:{readme_name}")).strip()
            content_bytes = await _git_bytes(repository.disk_path, "cat-file", "blob", blob_sha)
            return _file_response(owner, repo, readme_name, content_bytes, blob_sha, branch, BASE)
        except RuntimeError:
            continue

    raise HTTPException(status_code=404, detail="Not Found")

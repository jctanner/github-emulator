"""Commit endpoints -- list, get, and compare commits via bare repo."""

import asyncio
import os

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.git.bare_repo import (
    get_ahead_behind,
    get_commit_diff,
    get_compare_diff,
    get_merge_base,
    resolve_ref,
)
from app.schemas.browse import CommitResponse
from app.schemas.user import _fmt_dt, _make_node_id

router = APIRouter(tags=["commits"])

BASE = settings.BASE_URL


async def _git(repo_path: str, *args: str) -> str:
    """Run a git command in the bare repo and return stdout."""
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


def _parse_commit_line(
    line: str, owner: str, repo_name: str, base_url: str, verified: bool = False
) -> dict:
    """Parse a ``git log --format`` line into a commit dict."""
    api = f"{base_url}/api/v3"
    parts = line.split("\x1f")
    sha = parts[0] if len(parts) > 0 else ""
    author_name = parts[1] if len(parts) > 1 else ""
    author_email = parts[2] if len(parts) > 2 else ""
    author_date = parts[3] if len(parts) > 3 else ""
    committer_name = parts[4] if len(parts) > 4 else ""
    committer_email = parts[5] if len(parts) > 5 else ""
    committer_date = parts[6] if len(parts) > 6 else ""
    message = parts[7] if len(parts) > 7 else ""
    parents_raw = parts[8] if len(parts) > 8 else ""
    tree_sha = parts[9] if len(parts) > 9 else ""

    parents = [
        {"sha": p, "url": f"{api}/repos/{owner}/{repo_name}/commits/{p}",
         "html_url": f"{base_url}/{owner}/{repo_name}/commit/{p}"}
        for p in parents_raw.split() if p
    ]

    return {
        "sha": sha,
        "node_id": _make_node_id("Commit", hash(sha) % 10**8),
        "commit": {
            "author": {"name": author_name, "email": author_email, "date": author_date},
            "committer": {"name": committer_name, "email": committer_email, "date": committer_date},
            "message": message,
            "tree": {"sha": tree_sha, "url": f"{api}/repos/{owner}/{repo_name}/git/trees/{tree_sha}"},
            "url": f"{api}/repos/{owner}/{repo_name}/git/commits/{sha}",
            "comment_count": 0,
            "verification": {
                "verified": verified,
                "reason": "valid" if verified else "unsigned",
                "signature": None,
                "payload": None,
            },
        },
        "url": f"{api}/repos/{owner}/{repo_name}/commits/{sha}",
        "html_url": f"{base_url}/{owner}/{repo_name}/commit/{sha}",
        "comments_url": f"{api}/repos/{owner}/{repo_name}/commits/{sha}/comments",
        "author": None,
        "committer": None,
        "parents": parents,
    }


def _with_file_urls(owner: str, repo: str, sha: str, files: list[dict]) -> list[dict]:
    """Add GitHub's per-file URLs, at commit ``sha``, to changed-file records."""
    for file in files:
        filename = file.get("filename", "")
        file.update(
            {
                "sha": file.get("sha", ""),
                "blob_url": f"{BASE}/{owner}/{repo}/blob/{sha}/{filename}",
                "raw_url": f"{BASE}/{owner}/{repo}/raw/{sha}/{filename}",
                "contents_url": (
                    f"{BASE}/api/v3/repos/{owner}/{repo}/contents/{filename}"
                    f"?ref={sha}"
                ),
            }
        )
    return files


@router.get("/repos/{owner}/{repo}/commits", response_model=list[CommitResponse])
async def list_commits(
    owner: str,
    repo: str,
    db: DbSession,
    current_user: CurrentUser,
    request: Request,
    sha: str | None = Query(None),
    path: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List commits for a repository (reads from bare repo via git log)."""
    repository = await get_repo_or_404(owner, repo, db)

    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        return []

    fmt = "%H%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%ce%x1f%cI%x1f%s%x1f%P%x1f%T"
    args = ["log", f"--format={fmt}", f"--skip={( page - 1) * per_page}", f"-{per_page}"]

    if sha:
        args.append(sha)

    if path:
        args.extend(["--", path])

    try:
        out = await _git(repository.disk_path, *args)
    except RuntimeError:
        return []

    commits = []
    for line in out.strip().splitlines():
        if line:
            commits.append(
                _parse_commit_line(
                    line, owner, repo, BASE,
                    bool(getattr(request.state, "is_installation_token", False)),
                )
            )

    return commits


@router.get("/repos/{owner}/{repo}/commits/{sha}", response_model=CommitResponse)
async def get_commit(
    owner: str,
    repo: str,
    sha: str,
    db: DbSession,
    current_user: CurrentUser,
    request: Request,
):
    """Get a single commit."""
    repository = await get_repo_or_404(owner, repo, db)

    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Not Found")

    fmt = "%H%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%ce%x1f%cI%x1f%s%x1f%P%x1f%T"
    try:
        out = await _git(repository.disk_path, "log", f"--format={fmt}", "-1", sha)
    except RuntimeError:
        raise HTTPException(status_code=404, detail="Not Found")

    line = out.strip()
    if not line:
        raise HTTPException(status_code=404, detail="Not Found")

    commit = _parse_commit_line(
        line,
        owner,
        repo,
        BASE,
        bool(getattr(request.state, "is_installation_token", False)),
    )
    files = _with_file_urls(
        owner, repo, commit["sha"],
        await get_commit_diff(repository.disk_path, commit["sha"]),
    )
    additions = sum(file.get("additions", 0) for file in files)
    deletions = sum(file.get("deletions", 0) for file in files)
    commit["stats"] = {
        "total": additions + deletions,
        "additions": additions,
        "deletions": deletions,
    }
    commit["files"] = files
    return commit




_COMMIT_FORMAT = "%H%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%ce%x1f%cI%x1f%s%x1f%P%x1f%T"


async def _resolve_compare_side(
    ref: str, repository, db, user, request: Request
) -> str | None:
    """Resolve one side of ``base...head`` to a commit SHA in ``repository``.

    The side is a branch, tag or SHA, or ``owner:ref``. An owner other than
    the repository's names a repository in its fork network, whose branch is
    fetched into this one so the comparison runs here.
    """
    if not ref or ref.startswith("-"):
        return None
    repo_owner = repository.full_name.split("/", 1)[0]
    owner_login, sep, branch = ref.partition(":")
    if sep and owner_login != repo_owner:
        from app.api.deps import installation_token_covers
        from app.services import cross_repo_pulls
        from app.services.repository_access import can_read

        fork = await cross_repo_pulls.resolve_fork_for_owner(db, repository, owner_login)
        if (
            fork is None
            or not await can_read(db, fork, user)
            or not installation_token_covers(request, fork.full_name)
        ):
            return None
        return await cross_repo_pulls.fetch_branch_objects(repository, fork, branch)
    if sep:
        ref = branch
    return await resolve_ref(repository.disk_path, f"{ref}^{{commit}}")


async def _commit_objects(
    repository, owner: str, repo: str, shas: list[str], verified: bool
) -> list[dict]:
    if not shas:
        return []
    out = await _git(
        repository.disk_path, "log", "--no-walk=unsorted", f"--format={_COMMIT_FORMAT}", *shas
    )
    return [
        _parse_commit_line(line, owner, repo, BASE, verified)
        for line in out.strip().splitlines()
        if line
    ]


@router.get("/repos/{owner}/{repo}/compare/{basehead}")
async def compare_commits(
    owner: str,
    repo: str,
    basehead: str,
    db: DbSession,
    current_user: CurrentUser,
    request: Request,
    page: int | None = Query(None, ge=1),
    per_page: int | None = Query(None, ge=1, le=100),
):
    """Compare two commits: GitHub's ``base...head`` comparison.

    ``commits`` are those on head since the merge base, oldest first (up to
    250 unless paginated); ``files`` compare the merge base with head and
    appear on the first page only.
    """
    repository = await get_repo_or_404(owner, repo, db)

    if "..." not in basehead:
        raise HTTPException(status_code=404, detail="Not Found")
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Not Found")

    base_ref, _, head_ref = basehead.partition("...")
    base_sha = await _resolve_compare_side(base_ref, repository, db, current_user, request)
    head_sha = await _resolve_compare_side(head_ref, repository, db, current_user, request)
    if not base_sha or not head_sha:
        raise HTTPException(status_code=404, detail="Not Found")

    merge_base = await get_merge_base(repository.disk_path, base_sha, head_sha)
    if not merge_base:
        raise HTTPException(
            status_code=404, detail="No common ancestor between base and head."
        )

    ahead_by, behind_by = await get_ahead_behind(repository.disk_path, base_sha, head_sha)
    if ahead_by and behind_by:
        status = "diverged"
    elif ahead_by:
        status = "ahead"
    elif behind_by:
        status = "behind"
    else:
        status = "identical"

    verified = bool(getattr(request.state, "is_installation_token", False))
    base_commit = (await _commit_objects(repository, owner, repo, [base_sha], verified))[0]
    merge_base_commit = (
        base_commit if merge_base == base_sha
        else (await _commit_objects(repository, owner, repo, [merge_base], verified))[0]
    )

    commit_shas = (
        await _git(repository.disk_path, "rev-list", "--reverse", f"{base_sha}..{head_sha}")
    ).split()
    if page is None and per_page is None:
        commit_shas = commit_shas[:250]
        first_page = True
    else:
        size = per_page or 30
        start = ((page or 1) - 1) * size
        commit_shas = commit_shas[start:start + size]
        first_page = (page or 1) == 1
    commits = await _commit_objects(repository, owner, repo, commit_shas, verified)

    files = []
    if first_page:
        files = _with_file_urls(
            owner, repo, head_sha,
            await get_compare_diff(repository.disk_path, base_sha, head_sha),
        )

    api = f"{BASE}/api/v3"
    return {
        "url": f"{api}/repos/{owner}/{repo}/compare/{basehead}",
        "html_url": f"{BASE}/{owner}/{repo}/compare/{basehead}",
        "permalink_url": f"{BASE}/{owner}/{repo}/compare/{base_sha}...{head_sha}",
        "diff_url": f"{BASE}/{owner}/{repo}/compare/{basehead}.diff",
        "patch_url": f"{BASE}/{owner}/{repo}/compare/{basehead}.patch",
        "base_commit": base_commit,
        "merge_base_commit": merge_base_commit,
        "status": status,
        "ahead_by": ahead_by,
        "behind_by": behind_by,
        "total_commits": ahead_by,
        "commits": commits,
        "files": files,
    }

"""UI-oriented repository reads kept separate from GitHub-compatible REST."""

import asyncio
import os

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, get_repo_record_or_404
from app.git.bare_repo import get_commit_count, get_log, get_tag_count, list_tree
from app.models.branch import Branch
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.schemas.browse import (
    RepositoryHomeSummaryResponse,
    RepositoryNavigationResponse,
    RepositoryTreeResponse,
)


router = APIRouter(prefix="/api/_ui/repos", tags=["ui-repositories"])


async def _get_visible_repository(owner: str, repo: str, db: DbSession, current_user: CurrentUser):
    repository = await get_repo_record_or_404(owner, repo, db)
    if repository.private and (
        current_user is None
        or (
            current_user.id != repository.owner_id
            and not current_user.site_admin
        )
    ):
        raise HTTPException(status_code=404, detail="Not Found")
    return repository


@router.get(
    "/{owner}/{repo}/navigation",
    response_model=RepositoryNavigationResponse,
)
async def repository_navigation(
    owner: str,
    repo: str,
    db: DbSession,
    current_user: CurrentUser,
):
    """Return lightweight open-work counts for persistent repository tabs."""
    repository = await _get_visible_repository(owner, repo, db, current_user)
    pull_issue_ids = select(PullRequest.issue_id)
    open_issues_count = (
        await db.execute(
            select(func.count(Issue.id)).where(
                Issue.repo_id == repository.id,
                Issue.state == "open",
                ~Issue.id.in_(pull_issue_ids),
            )
        )
    ).scalar() or 0
    open_pulls_count = (
        await db.execute(
            select(func.count(Issue.id)).where(
                Issue.repo_id == repository.id,
                Issue.state == "open",
                Issue.id.in_(pull_issue_ids),
            )
        )
    ).scalar() or 0
    return {
        "open_issues_count": open_issues_count,
        "open_pulls_count": open_pulls_count,
    }


@router.get(
    "/{owner}/{repo}/summary",
    response_model=RepositoryHomeSummaryResponse,
)
async def repository_home_summary(
    owner: str,
    repo: str,
    db: DbSession,
    current_user: CurrentUser,
    ref: str | None = Query(None),
):
    """Return accurate repository-home counts without loading collections."""
    repository = await _get_visible_repository(owner, repo, db, current_user)

    branch_count = (
        await db.execute(
            select(func.count(Branch.id)).where(Branch.repo_id == repository.id)
        )
    ).scalar() or 0
    commit_count = 0
    tag_count = 0
    if repository.disk_path and os.path.isdir(repository.disk_path):
        commit_count, tag_count = await asyncio.gather(
            get_commit_count(
                repository.disk_path, ref or repository.default_branch
            ),
            get_tag_count(repository.disk_path),
        )

    return {
        "default_branch": repository.default_branch,
        "commit_count": commit_count,
        "branch_count": branch_count,
        "tag_count": tag_count,
    }


# Bound the git subprocesses spawned to look up each entry's last commit.
_LAST_COMMIT_CONCURRENCY = 8
_ENTRY_TYPES = {"blob": "file", "tree": "dir", "commit": "submodule"}


def _tree_commit(commit: dict | None) -> dict | None:
    if not commit:
        return None
    return {
        "sha": commit["sha"],
        "short_sha": commit["sha"][:7],
        "message": commit["message"],
        "author_name": commit["author_name"],
        "date": commit["committer_date"],
    }


@router.get(
    "/{owner}/{repo}/tree",
    response_model=RepositoryTreeResponse,
)
async def repository_tree(
    owner: str,
    repo: str,
    db: DbSession,
    current_user: CurrentUser,
    ref: str | None = Query(None),
    path: str = Query(""),
):
    """List a directory with the latest commit for it and for each entry.

    The GitHub-compatible contents API carries no commit data, so the file
    browser reads this instead to show the last commit message and age per row.
    """
    repository = await _get_visible_repository(owner, repo, db, current_user)
    ref = ref or repository.default_branch
    path = path.strip("/")
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        raise HTTPException(status_code=404, detail="Not Found")

    raw_entries = await list_tree(repository.disk_path, ref, path)
    if raw_entries is None:
        raise HTTPException(status_code=404, detail="Not Found")
    raw_entries.sort(key=lambda e: (e["type"] != "tree", e["name"].lower()))

    gate = asyncio.Semaphore(_LAST_COMMIT_CONCURRENCY)

    async def last_commit(entry_path: str | None) -> dict | None:
        async with gate:
            commits = await get_log(
                repository.disk_path, ref=ref, max_count=1, path=entry_path
            )
        return commits[0] if commits else None

    entry_paths = [
        "/".join(part for part in (path, entry["name"]) if part)
        for entry in raw_entries
    ]
    latest, *entry_commits = await asyncio.gather(
        last_commit(path or None), *(last_commit(p) for p in entry_paths)
    )

    return {
        "ref": ref,
        "path": path,
        "latest_commit": _tree_commit(latest),
        "entries": [
            {
                "name": entry["name"],
                "path": entry_path,
                "type": _ENTRY_TYPES.get(entry["type"], "file"),
                "last_commit": _tree_commit(commit),
            }
            for entry, entry_path, commit in zip(
                raw_entries, entry_paths, entry_commits
            )
        ],
    }

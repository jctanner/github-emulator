"""UI-oriented repository reads kept separate from GitHub-compatible REST."""

import asyncio
import os

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, get_repo_record_or_404
from app.git.bare_repo import get_commit_count, get_tag_count
from app.models.branch import Branch
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.schemas.browse import (
    RepositoryHomeSummaryResponse,
    RepositoryNavigationResponse,
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
            get_commit_count(repository.disk_path, repository.default_branch),
            get_tag_count(repository.disk_path),
        )

    return {
        "default_branch": repository.default_branch,
        "commit_count": commit_count,
        "branch_count": branch_count,
        "tag_count": tag_count,
    }

"""A commit made through the API is a push.

GitHub treats a commit written through the contents API like any other push
to the branch: it raises ``push`` activity, moves the head of every open pull
request from that branch (raising ``synchronize``, in the base repository
too when the branch heads a fork's pull request) and re-evaluates auto-merge.
The git transport and GraphQL's ``updateRefs`` already did this; the contents
API did not, so a commit pushed that way left its pull request stale and
started no workflow.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.user import User
from app.services.cross_repo_pulls import same_repository_head

logger = logging.getLogger("github_emulator.api_push")


async def after_api_commit(
    db: AsyncSession,
    repository: Repository,
    user: User,
    branch: str,
    before: str | None,
    after: str,
) -> None:
    """Process a branch moved from ``before`` to ``after`` by an API commit."""
    from app.services.merge_readiness_service import reevaluate_auto_merges
    from app.services.workflow_service import process_push_event

    repository_id = repository.id
    # The route resolved the repository without its relationships (raiseload);
    # push processing reads its owner and organization, so load it afresh.
    repository = (await db.execute(
        select(Repository).where(Repository.id == repository_id).execution_options(populate_existing=True)
    )).scalar_one()
    try:
        await process_push_event(
            db, repository, user,
            before_sha=before, ref_name=branch, after_sha=after,
            created=before is None, deleted=False,
        )
    except Exception:
        # The commit is made; a dispatch failure must not leave the pull
        # request heads below unrecorded (as after a git push).
        logger.exception("push activity for %s:%s failed", repository.full_name, branch)
        await db.rollback()
    heads = (await db.execute(
        select(PullRequest).join(Issue, PullRequest.issue_id == Issue.id).where(
            PullRequest.repo_id == repository_id,
            PullRequest.head_ref == branch,
            same_repository_head(repository_id),
            Issue.state == "open",
        )
    )).scalars().all()
    for pull_request in heads:
        pull_request.head_sha = after
        pull_request.last_push_by_id = user.id
    await db.commit()
    try:
        await reevaluate_auto_merges(db, repository_id)
    except Exception:
        logger.exception("auto-merge re-evaluation for %s failed", repository_id)

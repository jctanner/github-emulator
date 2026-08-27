"""Event-driven pull request auto-merge processing."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auto_merge import PullRequestAutoMerge
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.user import User


READY_LABEL = "ready-for-merge"


async def process_auto_merge(
    db: AsyncSession,
    pull_request: PullRequest,
    actor: User | None = None,
) -> bool:
    """Merge an enabled PR once the emulator's readiness gate is satisfied.

    GitHub's real readiness calculation includes branch protection, required
    checks, approvals, merge queues, and conflict detection.  The emulator's
    deterministic equivalent is the ``ready-for-merge`` label, which is what
    the Fullsend review workflow already applies after an approval.
    """
    result = await db.execute(
        select(PullRequestAutoMerge).where(
            PullRequestAutoMerge.pull_request_id == pull_request.id
        )
    )
    request = result.scalar_one_or_none()
    if request is None or pull_request.merged:
        return False

    issue_result = await db.execute(select(Issue).where(Issue.id == pull_request.issue_id))
    issue = issue_result.scalar_one_or_none()
    if issue is None or issue.state != "open" or READY_LABEL not in {
        label.name for label in (issue.labels or [])
    }:
        return False

    repo_result = await db.execute(select(Repository).where(Repository.id == pull_request.repo_id))
    repository = repo_result.scalar_one_or_none()
    if repository is None:
        return False

    # Reuse the emulator's existing real-git merge implementation.  Importing
    # lazily avoids a module cycle between the REST route and this service.
    from app.api.pulls import _attach_resolved_refs, _perform_git_merge

    await _attach_resolved_refs(pull_request, repository)
    merge_method = (request.merge_method or "MERGE").lower()
    headline = request.commit_headline or f"Merge pull request #{issue.number}"
    body = request.commit_body or f"{headline}\n\nMerge {pull_request.head_ref} into {pull_request.base_ref}"
    merge_sha = None
    try:
        merge_sha = await _perform_git_merge(
            disk_path=repository.disk_path,
            head_ref=pull_request.resolved_head_ref,
            base_ref=pull_request.resolved_base_ref,
            merge_method=merge_method,
            commit_message=body,
        )
    except Exception:
        # The REST merge route has the same DB-only fallback for fixture repos
        # whose refs are synthetic or whose checkout is not on disk.
        merge_sha = None

    now = datetime.now(timezone.utc)
    pull_request.merged = True
    pull_request.merged_at = now
    pull_request.merged_by_id = actor.id if actor else request.enabled_by_id
    pull_request.merge_commit_sha = merge_sha or f"merge_{pull_request.head_sha[:8]}_{pull_request.base_sha[:8]}"
    issue.state = "closed"
    issue.closed_at = now
    repository.open_issues_count = max(0, repository.open_issues_count - 1)
    await db.delete(request)
    await db.commit()
    await db.refresh(issue)
    await db.refresh(pull_request)

    from app.services.workflow_service import build_activity_payload, dispatch_event

    event_actor = actor
    if event_actor is None:
        actor_result = await db.execute(select(User).where(User.id == repository.owner_id))
        event_actor = actor_result.scalar_one_or_none()
    if event_actor is not None:
        await dispatch_event(
            db,
            repository,
            event_actor,
            "pull_request_target",
            "closed",
            build_activity_payload(
                repository,
                event_actor,
                "closed",
                issue=issue,
                pull_request=pull_request,
                ref=f"refs/heads/{pull_request.base_ref}",
                sha=pull_request.merge_commit_sha or pull_request.base_sha,
            ),
            ref=pull_request.base_ref,
            sha=pull_request.merge_commit_sha or pull_request.base_sha,
        )
    return True

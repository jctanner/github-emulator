"""Event-driven pull request auto-merge processing."""

import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auto_merge import PullRequestAutoMerge
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.user import User


async def process_auto_merge(
    db: AsyncSession,
    pull_request: PullRequest,
    actor: User | None = None,
) -> bool:
    """Merge an enabled PR once shared branch-policy readiness is satisfied."""
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
    if issue is None or issue.state != "open":
        return False

    repo_result = await db.execute(select(Repository).where(Repository.id == pull_request.repo_id))
    repository = repo_result.scalar_one_or_none()
    if repository is None:
        return False

    merge_actor = actor
    if merge_actor is None:
        actor_result = await db.execute(select(User).where(User.id == request.enabled_by_id))
        merge_actor = actor_result.scalar_one_or_none()

    from app.services.merge_readiness_service import evaluate_merge_readiness

    readiness = await evaluate_merge_readiness(
        db,
        pull_request,
        actor=merge_actor,
        merge_method=(request.merge_method or "MERGE").lower(),
    )
    if not readiness.ready:
        return False

    from app.services.closing_issue_service import resolve_closing_issues

    linked_issues = await resolve_closing_issues(
        db,
        pull_request,
        repository,
        include_commit_messages=True,
    )

    # Reuse the emulator's existing real-git merge implementation.  Importing
    # lazily avoids a module cycle between the REST route and this service.
    from app.api.pulls import (
        _attach_resolved_refs,
        _perform_git_merge,
        _record_merged_base_sha,
    )

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
        merge_sha = None

    # A real repository with resolvable refs must produce a real merge. This
    # closes the race where conflicts appear after readiness was evaluated.
    if repository.disk_path and os.path.isdir(repository.disk_path) and merge_sha is None:
        from app.services.git_service import get_ref_sha

        actual_head = await get_ref_sha(repository.disk_path, pull_request.resolved_head_ref)
        actual_base = await get_ref_sha(repository.disk_path, pull_request.resolved_base_ref)
        if actual_head and actual_base:
            return False

    now = datetime.now(timezone.utc)
    pull_request.merged = True
    pull_request.merged_at = now
    pull_request.merged_by_id = merge_actor.id if merge_actor else request.enabled_by_id
    pull_request.merge_commit_sha = merge_sha or f"merge_{pull_request.head_sha[:8]}_{pull_request.base_sha[:8]}"
    await _record_merged_base_sha(
        db,
        repository,
        pull_request.resolved_base_ref,
        merge_sha,
    )
    issue.state = "closed"
    issue.closed_at = now
    issue.state_reason = "completed"
    issue.closed_by_id = merge_actor.id if merge_actor else request.enabled_by_id
    repository.open_issues_count = max(0, repository.open_issues_count - 1)
    closed_linked_issues = []
    if merge_actor is not None:
        from app.services.closing_issue_service import close_linked_issues

        closed_linked_issues = close_linked_issues(linked_issues, merge_actor, now)
    await db.delete(request)
    await db.commit()
    await db.refresh(issue)
    await db.refresh(pull_request)

    from app.services.workflow_service import build_activity_payload, dispatch_event

    event_actor = merge_actor
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
        from app.services.closing_issue_service import dispatch_linked_issue_closed_events

        await dispatch_linked_issue_closed_events(db, closed_linked_issues, event_actor)
    return True

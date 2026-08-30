"""Resolve and apply GitHub pull-request closing-keyword references."""

from __future__ import annotations

import os
import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.git.bare_repo import get_log
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.user import User


_CLOSING_REFERENCE = re.compile(
    r"\b(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)"
    r"\s*:?\s+"
    r"(?:(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+))?"
    r"#(?P<number>\d+)\b",
    re.IGNORECASE,
)


def parse_closing_references(text: str | None) -> list[tuple[str | None, str | None, int]]:
    """Return unique closing references in source order."""
    references: list[tuple[str | None, str | None, int]] = []
    seen: set[tuple[str | None, str | None, int]] = set()
    for match in _CLOSING_REFERENCE.finditer(text or ""):
        reference = (
            match.group("owner"),
            match.group("repo"),
            int(match.group("number")),
        )
        normalized = (
            reference[0].lower() if reference[0] else None,
            reference[1].lower() if reference[1] else None,
            reference[2],
        )
        if normalized not in seen:
            seen.add(normalized)
            references.append(reference)
    return references


async def _reference_texts(
    pull_request: PullRequest,
    repository: Repository,
    *,
    include_commit_messages: bool,
) -> list[str]:
    texts = [pull_request.issue.body or ""]
    if not include_commit_messages:
        return texts
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        return texts

    base_ref = getattr(pull_request, "resolved_base_ref", pull_request.base_ref)
    head_ref = getattr(pull_request, "resolved_head_ref", pull_request.head_ref)
    commits = await get_log(
        repository.disk_path,
        ref=f"{base_ref}..{head_ref}",
        max_count=1000,
    )
    texts.extend(
        "\n".join(part for part in (commit.get("message"), commit.get("body")) if part)
        for commit in commits
    )
    return texts


async def resolve_closing_issues(
    db: AsyncSession,
    pull_request: PullRequest,
    repository: Repository,
    *,
    include_commit_messages: bool = False,
) -> list[Issue]:
    """Resolve keyword-linked issues when the PR targets the default branch."""
    if pull_request.base_ref != repository.default_branch:
        return []

    references: list[tuple[str | None, str | None, int]] = []
    seen: set[tuple[str | None, str | None, int]] = set()
    for text in await _reference_texts(
        pull_request,
        repository,
        include_commit_messages=include_commit_messages,
    ):
        for owner, repo_name, number in parse_closing_references(text):
            key = (
                owner.lower() if owner else repository.full_name.split("/", 1)[0].lower(),
                repo_name.lower() if repo_name else repository.name.lower(),
                number,
            )
            if key not in seen:
                seen.add(key)
                references.append((owner, repo_name, number))

    issues: list[Issue] = []
    for owner, repo_name, number in references:
        target_repository = repository
        if owner and repo_name:
            full_name = f"{owner}/{repo_name}".lower()
            repo_result = await db.execute(
                select(Repository).where(func.lower(Repository.full_name) == full_name)
            )
            target_repository = repo_result.scalar_one_or_none()
            if target_repository is None:
                continue
        issue_result = await db.execute(
            select(Issue).where(
                Issue.repo_id == target_repository.id,
                Issue.number == number,
            )
        )
        issue = issue_result.scalar_one_or_none()
        if issue is not None and issue.id != pull_request.issue_id:
            issues.append(issue)
    return issues


def close_linked_issues(
    issues: list[Issue],
    actor: User,
    closed_at: datetime,
) -> list[Issue]:
    """Close open linked issues and maintain their repository counters."""
    closed: list[Issue] = []
    for issue in issues:
        if issue.state != "open":
            continue
        issue.state = "closed"
        issue.state_reason = "completed"
        issue.closed_at = closed_at
        issue.closed_by_id = actor.id
        issue.repository.open_issues_count = max(
            0, issue.repository.open_issues_count - 1
        )
        closed.append(issue)
    return closed


async def dispatch_linked_issue_closed_events(
    db: AsyncSession,
    issues: list[Issue],
    actor: User,
) -> None:
    """Emit the issue activity GitHub produces for keyword-driven closure."""
    from app.services.workflow_service import build_activity_payload, dispatch_event

    if not issues:
        return
    issue_ids = [issue.id for issue in issues]
    result = await db.execute(
        select(Issue)
        .options(selectinload(Issue.repository))
        .where(Issue.id.in_(issue_ids))
        .order_by(Issue.id)
        .execution_options(populate_existing=True)
    )
    refreshed_issues = list(result.scalars().all())
    for issue in refreshed_issues:
        await dispatch_event(
            db,
            issue.repository,
            actor,
            "issues",
            "closed",
            build_activity_payload(
                issue.repository,
                actor,
                "closed",
                issue=issue,
            ),
            ref=issue.repository.default_branch,
        )

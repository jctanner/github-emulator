"""Shared pull-request merge-readiness evaluation."""

import asyncio
import os
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch, BranchProtection
from app.models.check import CheckRun
from app.models.commit_status import CommitStatus
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Collaborator, Repository
from app.models.review import Review
from app.models.user import User


SUCCESSFUL_CHECK_CONCLUSIONS = {"success", "neutral", "skipped"}


@dataclass(slots=True)
class MergeReadiness:
    state: str
    ready: bool
    mergeable: bool | None
    protected: bool = False
    reasons: list[str] = field(default_factory=list)
    review_decision: str | None = None


async def _run_git(disk_path: str, *args: str) -> tuple[int, str, str]:
    if not disk_path or not os.path.isdir(disk_path):
        return 128, "", "repository unavailable"
    process = await asyncio.create_subprocess_exec(
        "git",
        "--git-dir",
        disk_path,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def _has_merge_conflicts(repository: Repository, pr: PullRequest) -> bool | None:
    if not repository.disk_path or not pr.head_sha or not pr.base_sha:
        return None
    returncode, stdout, _ = await _run_git(
        repository.disk_path,
        "merge-tree",
        "--write-tree",
        pr.base_sha,
        pr.head_sha,
    )
    if returncode == 0:
        return False
    first_line = stdout.splitlines()[0].strip() if stdout.splitlines() else ""
    if (
        returncode == 1
        and len(first_line) in {40, 64}
        and all(character in "0123456789abcdef" for character in first_line.lower())
    ):
        return True
    return None


async def _head_contains_base(
    repository: Repository, current_base_sha: str, head_sha: str
) -> bool | None:
    if not current_base_sha or not head_sha:
        return None
    if repository.disk_path and os.path.isdir(repository.disk_path):
        returncode, _, _ = await _run_git(
            repository.disk_path,
            "merge-base",
            "--is-ancestor",
            current_base_sha,
            head_sha,
        )
        if returncode == 0:
            return True
        if returncode == 1:
            return False
    return None


async def _is_repository_admin(
    db: AsyncSession, repository: Repository, actor: User | None
) -> bool:
    if actor is None:
        return False
    if actor.site_admin or actor.id == repository.owner_id:
        return True
    result = await db.execute(
        select(Collaborator).where(
            Collaborator.repo_id == repository.id,
            Collaborator.user_id == actor.id,
            Collaborator.permission == "admin",
        )
    )
    return result.scalar_one_or_none() is not None


def _required_contexts(protection: BranchProtection) -> set[str]:
    settings = protection.required_status_checks or {}
    contexts = set(settings.get("contexts", []))
    contexts.update(
        item.get("context")
        for item in settings.get("checks", [])
        if isinstance(item, dict) and item.get("context")
    )
    return contexts


async def _status_check_reasons(
    db: AsyncSession, pr: PullRequest, protection: BranchProtection
) -> list[str]:
    required = _required_contexts(protection)
    if not required:
        return []

    statuses = (
        await db.execute(
            select(CommitStatus)
            .where(CommitStatus.repo_id == pr.repo_id, CommitStatus.sha == pr.head_sha)
            .order_by(CommitStatus.created_at.desc(), CommitStatus.id.desc())
        )
    ).scalars().all()
    checks = (
        await db.execute(
            select(CheckRun)
            .where(CheckRun.repo_id == pr.repo_id, CheckRun.head_sha == pr.head_sha)
            .order_by(CheckRun.created_at.desc(), CheckRun.id.desc())
        )
    ).scalars().all()

    latest_status = {}
    for status in statuses:
        latest_status.setdefault(status.context, status)
    latest_check = {}
    for check in checks:
        latest_check.setdefault(check.name, check)

    reasons = []
    for context in sorted(required):
        status = latest_status.get(context)
        check = latest_check.get(context)
        status_passed = status is not None and status.state == "success"
        check_passed = (
            check is not None
            and check.status == "completed"
            and check.conclusion in SUCCESSFUL_CHECK_CONCLUSIONS
        )
        if status_passed or check_passed:
            continue
        if status is None and check is None:
            reasons.append(f"required status check '{context}' is missing")
        else:
            reasons.append(f"required status check '{context}' has not succeeded")
    return reasons


async def _review_state(
    db: AsyncSession, pr: PullRequest, protection: BranchProtection
) -> tuple[list[str], str | None]:
    settings = protection.required_pull_request_reviews
    if settings is None:
        return [], None
    reviews = (
        await db.execute(
            select(Review)
            .where(Review.pull_request_id == pr.id, Review.state != "PENDING")
            .order_by(Review.created_at.asc(), Review.id.asc())
        )
    ).scalars().all()
    latest_by_user = {}
    for review in reviews:
        latest_by_user[review.user_id] = review

    current = list(latest_by_user.values())
    if any(review.state == "CHANGES_REQUESTED" for review in current):
        return ["changes have been requested"], "CHANGES_REQUESTED"

    approvals = [review for review in current if review.state == "APPROVED"]
    if settings.get("dismiss_stale_reviews"):
        approvals = [review for review in approvals if review.commit_id == pr.head_sha]

    required_count = int(settings.get("required_approving_review_count", 1))
    reasons = []
    if len(approvals) < required_count:
        reasons.append(
            f"{required_count} approving review(s) required; {len(approvals)} current"
        )

    if settings.get("require_last_push_approval") and pr.last_push_by_id is not None:
        if not any(review.user_id != pr.last_push_by_id for review in approvals):
            reasons.append("the most recent push must be approved by another user")

    return reasons, "REVIEW_REQUIRED" if reasons else "APPROVED"


async def evaluate_merge_readiness(
    db: AsyncSession,
    pr: PullRequest,
    actor: User | None = None,
    merge_method: str | None = None,
) -> MergeReadiness:
    """Calculate GitHub-shaped merge readiness for one pull request."""
    issue_result = await db.execute(select(Issue).where(Issue.id == pr.issue_id))
    issue = issue_result.scalar_one_or_none()
    repo_result = await db.execute(select(Repository).where(Repository.id == pr.repo_id))
    repository = repo_result.scalar_one_or_none()
    if issue is None or repository is None:
        return MergeReadiness("UNKNOWN", False, None, reasons=["pull request data is incomplete"])

    if pr.merged:
        return MergeReadiness("CLEAN", False, True)
    if issue.state != "open":
        return MergeReadiness("UNKNOWN", False, None, reasons=["pull request is closed"])
    if pr.draft:
        return MergeReadiness("DRAFT", False, pr.mergeable, reasons=["pull request is a draft"])

    conflicts = await _has_merge_conflicts(repository, pr)
    if pr.mergeable is False or conflicts is True:
        return MergeReadiness("DIRTY", False, False, reasons=["merge conflicts must be resolved"])
    if pr.mergeable is None and conflicts is None:
        return MergeReadiness("UNKNOWN", False, None, reasons=["mergeability is unknown"])

    branch_result = await db.execute(
        select(Branch).where(
            Branch.repo_id == repository.id,
            Branch.name == pr.base_ref,
        )
    )
    branch = branch_result.scalar_one_or_none()
    protection = branch.protection if branch is not None and branch.protected else None
    if protection is None:
        return MergeReadiness("CLEAN", True, True)

    if not protection.enforce_admins and await _is_repository_admin(db, repository, actor):
        if not (protection.required_linear_history and (merge_method or "").lower() == "merge"):
            return MergeReadiness("CLEAN", True, True, protected=True)

    reasons, review_decision = await _review_state(db, pr, protection)
    reasons.extend(await _status_check_reasons(db, pr, protection))

    status_settings = protection.required_status_checks or {}
    if status_settings.get("strict"):
        current_base_sha = branch.sha if branch is not None else ""
        contains_base = await _head_contains_base(repository, current_base_sha, pr.head_sha)
        if contains_base is False or (
            contains_base is None
            and current_base_sha
            and pr.base_sha
            and current_base_sha != pr.base_sha
        ):
            return MergeReadiness(
                "BEHIND",
                False,
                True,
                protected=True,
                reasons=["head branch is behind the protected base branch", *reasons],
                review_decision=review_decision,
            )
        if contains_base is None and (not current_base_sha or not pr.head_sha):
            return MergeReadiness(
                "UNKNOWN",
                False,
                None,
                protected=True,
                reasons=["cannot determine whether the head contains the protected base"],
                review_decision=review_decision,
            )

    if protection.required_linear_history and (merge_method or "").lower() == "merge":
        reasons.append("merge commits are not allowed by required linear history")

    if reasons:
        return MergeReadiness(
            "BLOCKED",
            False,
            True,
            protected=True,
            reasons=reasons,
            review_decision=review_decision,
        )
    return MergeReadiness(
        "CLEAN",
        True,
        True,
        protected=True,
        review_decision=review_decision,
    )


async def reevaluate_auto_merges(
    db: AsyncSession, repo_id: int, head_sha: str | None = None
) -> int:
    """Reevaluate queued auto-merges affected by a repository state change."""
    from app.models.auto_merge import PullRequestAutoMerge
    from app.services.auto_merge_service import process_auto_merge

    query = (
        select(PullRequest)
        .join(PullRequestAutoMerge, PullRequestAutoMerge.pull_request_id == PullRequest.id)
        .where(PullRequest.repo_id == repo_id, PullRequest.merged.is_(False))
    )
    if head_sha is not None:
        query = query.where(PullRequest.head_sha == head_sha)
    pull_requests = (await db.execute(query)).scalars().unique().all()
    merged = 0
    for pull_request in pull_requests:
        if await process_auto_merge(db, pull_request):
            merged += 1
    return merged

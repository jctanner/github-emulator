"""Pull request management service."""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Issue, PullRequest, Repository, User
from app.services.git_service import get_ref_sha


async def create_pr(
    db: AsyncSession,
    repo: Repository,
    user: User,
    title: str,
    head_ref: str,
    base_ref: str,
    body: Optional[str] = None,
    draft: bool = False,
) -> tuple[Issue, PullRequest]:
    """Create a new pull request.

    Pull requests share issue numbering. An Issue entry is created first,
    then a PullRequest record is linked to it.

    Args:
        db: Async database session.
        repo: The repository for the PR.
        user: The user creating the PR.
        title: PR title.
        head_ref: The head branch name.
        base_ref: The base branch name.
        body: PR body (markdown).
        draft: Whether the PR is a draft.

    Returns:
        Tuple of (Issue, PullRequest).
    """
    # Assign next issue number (PRs share issue numbering)
    number = repo.next_issue_number
    repo.next_issue_number = number + 1
    repo.open_issues_count = repo.open_issues_count + 1

    # Create the Issue entry
    issue = Issue(
        repo_id=repo.id,
        number=number,
        user_id=user.id,
        title=title,
        body=body,
        state="open",
    )
    db.add(issue)
    await db.flush()

    # Resolve head and base SHAs from the bare repo
    head_sha = ""
    base_sha = ""
    if repo.disk_path:
        resolved_head = await get_ref_sha(repo.disk_path, head_ref)
        resolved_base = await get_ref_sha(repo.disk_path, base_ref)
        head_sha = resolved_head or ""
        base_sha = resolved_base or ""

    # Create the PullRequest entry
    pr = PullRequest(
        issue_id=issue.id,
        repo_id=repo.id,
        head_ref=head_ref,
        head_sha=head_sha,
        head_repo_id=repo.id,
        base_ref=base_ref,
        base_sha=base_sha,
        draft=draft,
        mergeable=True,
    )
    db.add(pr)
    await db.commit()
    await db.refresh(issue)
    await db.refresh(pr)
    return issue, pr


async def get_pr(
    db: AsyncSession, repo_id: int, number: int
) -> Optional[tuple[Issue, PullRequest]]:
    """Get a pull request by repository ID and issue number.

    Args:
        db: Async database session.
        repo_id: The repository ID.
        number: The issue/PR number.

    Returns:
        Tuple of (Issue, PullRequest), or None if not found.
    """
    result = await db.execute(
        select(Issue).where(
            Issue.repo_id == repo_id,
            Issue.number == number,
        )
    )
    issue = result.scalar_one_or_none()
    if issue is None:
        return None

    result = await db.execute(
        select(PullRequest).where(PullRequest.issue_id == issue.id)
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        return None

    return issue, pr


async def update_pr(
    db: AsyncSession,
    issue: Issue,
    pr: PullRequest,
    **kwargs,
) -> tuple[Issue, PullRequest]:
    """Update a pull request and its associated issue.

    Args:
        db: Async database session.
        issue: The Issue associated with the PR.
        pr: The PullRequest to update.
        **kwargs: Fields to update. Issue fields (title, body, state)
            are applied to the issue; PR fields (base_ref, draft, etc.)
            are applied to the PR.

    Returns:
        Tuple of (updated Issue, updated PullRequest).
    """
    # Fields that belong to the Issue
    issue_fields = {"title", "body", "state", "state_reason", "milestone_id"}

    for key, value in kwargs.items():
        if key in issue_fields and hasattr(issue, key):
            # Handle state transitions
            if key == "state":
                if value == "closed" and issue.state == "open":
                    issue.closed_at = datetime.utcnow()
                elif value == "open" and issue.state == "closed":
                    issue.closed_at = None
                    issue.state_reason = None
            setattr(issue, key, value)
        elif hasattr(pr, key):
            setattr(pr, key, value)

    await db.commit()
    await db.refresh(issue)
    await db.refresh(pr)
    return issue, pr


async def merge_pr(
    db: AsyncSession,
    repo: Repository,
    issue: Issue,
    pr: PullRequest,
    user: User,
    merge_method: str = "merge",
    commit_title: Optional[str] = None,
    commit_message: Optional[str] = None,
) -> PullRequest:
    """Merge a pull request.

    Updates the PR as merged, closes the associated issue, and
    decrements the repo's open issues count.

    Args:
        db: Async database session.
        repo: The repository.
        issue: The Issue associated with the PR.
        pr: The PullRequest to merge.
        user: The user performing the merge.
        merge_method: Merge method ("merge", "squash", "rebase").
        commit_title: Optional custom merge commit title.
        commit_message: Optional custom merge commit message.

    Returns:
        The updated PullRequest.
    """
    now = datetime.utcnow()

    # Mark PR as merged
    pr.merged = True
    pr.merged_at = now
    pr.merged_by_id = user.id
    pr.merge_commit_sha = pr.head_sha  # Simplified; real merge would create a commit

    # Close the issue
    issue.state = "closed"
    issue.state_reason = "completed"
    issue.closed_at = now
    issue.closed_by_id = user.id

    # Update repo counters
    if repo.open_issues_count > 0:
        repo.open_issues_count = repo.open_issues_count - 1

    await db.commit()
    await db.refresh(pr)
    await db.refresh(issue)
    return pr


async def list_prs(
    db: AsyncSession,
    repo_id: int,
    state: str = "open",
    page: int = 1,
    per_page: int = 30,
) -> list[tuple[Issue, PullRequest]]:
    """List pull requests in a repository.

    Args:
        db: Async database session.
        repo_id: The repository ID.
        state: Filter by state ("open", "closed", "all").
        page: Page number (1-indexed).
        per_page: Number of results per page.

    Returns:
        List of (Issue, PullRequest) tuples.
    """
    offset = (page - 1) * per_page

    query = (
        select(Issue, PullRequest)
        .join(PullRequest, PullRequest.issue_id == Issue.id)
        .where(Issue.repo_id == repo_id)
    )

    if state != "all":
        query = query.where(Issue.state == state)

    query = query.order_by(Issue.created_at.desc()).offset(offset).limit(per_page)

    result = await db.execute(query)
    return list(result.all())

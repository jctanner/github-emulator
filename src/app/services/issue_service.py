"""Issue management service."""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Issue, IssueAssignee, IssueLabel, Label, Repository, User


async def create_issue(
    db: AsyncSession,
    repo: Repository,
    user: User,
    title: str,
    body: Optional[str] = None,
    labels: Optional[list[str]] = None,
    assignees: Optional[list[str]] = None,
    milestone_id: Optional[int] = None,
) -> Issue:
    """Create a new issue in a repository.

    Automatically assigns the next issue number from repo.next_issue_number
    and increments it.

    Args:
        db: Async database session.
        repo: The repository to create the issue in.
        user: The user creating the issue.
        title: Issue title.
        body: Issue body (markdown).
        labels: List of label names to attach.
        assignees: List of user logins to assign.
        milestone_id: Optional milestone ID.

    Returns:
        The newly created Issue.
    """
    # Assign the next issue number and increment
    number = repo.next_issue_number
    repo.next_issue_number = number + 1
    repo.open_issues_count = repo.open_issues_count + 1

    issue = Issue(
        repo_id=repo.id,
        number=number,
        user_id=user.id,
        title=title,
        body=body,
        state="open",
        milestone_id=milestone_id,
    )
    db.add(issue)
    await db.flush()

    # Attach labels by name
    if labels:
        for label_name in labels:
            result = await db.execute(
                select(Label).where(
                    Label.repo_id == repo.id,
                    Label.name == label_name,
                )
            )
            label = result.scalar_one_or_none()
            if label:
                issue_label = IssueLabel(issue_id=issue.id, label_id=label.id)
                db.add(issue_label)

    # Attach assignees by login
    if assignees:
        for assignee_login in assignees:
            result = await db.execute(
                select(User).where(User.login == assignee_login)
            )
            assignee_user = result.scalar_one_or_none()
            if assignee_user:
                issue_assignee = IssueAssignee(
                    issue_id=issue.id, user_id=assignee_user.id
                )
                db.add(issue_assignee)

    await db.commit()
    await db.refresh(issue)
    return issue


async def get_issue(
    db: AsyncSession, repo_id: int, number: int
) -> Optional[Issue]:
    """Get an issue by repository ID and issue number.

    Args:
        db: Async database session.
        repo_id: The repository ID.
        number: The issue number.

    Returns:
        The Issue, or None if not found.
    """
    result = await db.execute(
        select(Issue).where(
            Issue.repo_id == repo_id,
            Issue.number == number,
        )
    )
    return result.scalar_one_or_none()


async def update_issue(
    db: AsyncSession, issue: Issue, **kwargs
) -> Issue:
    """Update an issue's attributes.

    Handles state transitions (open/closed) by setting closed_at
    and state_reason appropriately.

    Args:
        db: Async database session.
        issue: The issue to update.
        **kwargs: Fields to update.

    Returns:
        The updated Issue.
    """
    # Handle state changes
    if "state" in kwargs:
        new_state = kwargs["state"]
        if new_state == "closed" and issue.state == "open":
            issue.closed_at = datetime.utcnow()
            if "state_reason" not in kwargs:
                kwargs["state_reason"] = "completed"
        elif new_state == "open" and issue.state == "closed":
            issue.closed_at = None
            issue.state_reason = None

    for key, value in kwargs.items():
        if hasattr(issue, key):
            setattr(issue, key, value)

    await db.commit()
    await db.refresh(issue)
    return issue


async def list_issues(
    db: AsyncSession,
    repo_id: int,
    state: str = "open",
    page: int = 1,
    per_page: int = 30,
    sort: str = "created",
    direction: str = "desc",
) -> list[Issue]:
    """List issues in a repository.

    Args:
        db: Async database session.
        repo_id: The repository ID.
        state: Filter by state ("open", "closed", "all").
        page: Page number (1-indexed).
        per_page: Number of results per page.
        sort: Sort field ("created", "updated", "comments").
        direction: Sort direction ("asc" or "desc").

    Returns:
        List of Issues.
    """
    sort_map = {
        "created": Issue.created_at,
        "updated": Issue.updated_at,
    }
    sort_column = sort_map.get(sort, Issue.created_at)

    if direction == "desc":
        sort_column = sort_column.desc()
    else:
        sort_column = sort_column.asc()

    offset = (page - 1) * per_page

    query = select(Issue).where(Issue.repo_id == repo_id)

    if state != "all":
        query = query.where(Issue.state == state)

    # Exclude pull requests (issues that have an associated PR)
    # PRs share issue numbering but can be filtered via the pull_request relationship
    query = query.order_by(sort_column).offset(offset).limit(per_page)

    result = await db.execute(query)
    return list(result.scalars().all())

"""Label management service."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Issue, IssueLabel, Label


async def create_label(
    db: AsyncSession,
    repo_id: int,
    name: str,
    color: str,
    description: Optional[str] = None,
    is_default: bool = False,
) -> Label:
    """Create a new label in a repository.

    Args:
        db: Async database session.
        repo_id: The repository ID.
        name: Label name.
        color: Label color (6-char hex without #).
        description: Optional label description.
        is_default: Whether this is a default label.

    Returns:
        The newly created Label.
    """
    # Strip leading '#' from color if present
    color = color.lstrip("#")

    label = Label(
        repo_id=repo_id,
        name=name,
        color=color,
        description=description,
        is_default=is_default,
    )
    db.add(label)
    await db.commit()
    await db.refresh(label)
    return label


async def get_label_by_name(
    db: AsyncSession, repo_id: int, name: str
) -> Optional[Label]:
    """Get a label by name within a repository.

    Args:
        db: Async database session.
        repo_id: The repository ID.
        name: The label name.

    Returns:
        The Label, or None if not found.
    """
    result = await db.execute(
        select(Label).where(
            Label.repo_id == repo_id,
            Label.name == name,
        )
    )
    return result.scalar_one_or_none()


async def get_label_by_id(
    db: AsyncSession, label_id: int
) -> Optional[Label]:
    """Get a label by its ID.

    Args:
        db: Async database session.
        label_id: The label ID.

    Returns:
        The Label, or None if not found.
    """
    result = await db.execute(
        select(Label).where(Label.id == label_id)
    )
    return result.scalar_one_or_none()


async def update_label(
    db: AsyncSession, label: Label, **kwargs
) -> Label:
    """Update a label's attributes.

    Args:
        db: Async database session.
        label: The label to update.
        **kwargs: Fields to update (name, color, description).

    Returns:
        The updated Label.
    """
    for key, value in kwargs.items():
        if key == "color" and isinstance(value, str):
            value = value.lstrip("#")
        if hasattr(label, key):
            setattr(label, key, value)
    await db.commit()
    await db.refresh(label)
    return label


async def delete_label(db: AsyncSession, label: Label) -> None:
    """Delete a label.

    Also removes the label from any issues it is attached to.

    Args:
        db: Async database session.
        label: The label to delete.
    """
    # Remove label associations from issues
    result = await db.execute(
        select(IssueLabel).where(IssueLabel.label_id == label.id)
    )
    for issue_label in result.scalars().all():
        await db.delete(issue_label)

    await db.delete(label)
    await db.commit()


async def list_labels_for_repo(
    db: AsyncSession, repo_id: int
) -> list[Label]:
    """List all labels in a repository.

    Args:
        db: Async database session.
        repo_id: The repository ID.

    Returns:
        List of Labels.
    """
    result = await db.execute(
        select(Label)
        .where(Label.repo_id == repo_id)
        .order_by(Label.name.asc())
    )
    return list(result.scalars().all())


async def add_labels_to_issue(
    db: AsyncSession, issue: Issue, label_names: list[str]
) -> list[Label]:
    """Add labels to an issue by name.

    Labels that are already attached are skipped. Labels that do not
    exist in the repository are ignored.

    Args:
        db: Async database session.
        issue: The issue to add labels to.
        label_names: List of label names to add.

    Returns:
        List of all Labels now on the issue.
    """
    # Get existing label IDs on this issue
    existing_result = await db.execute(
        select(IssueLabel.label_id).where(IssueLabel.issue_id == issue.id)
    )
    existing_label_ids = set(existing_result.scalars().all())

    for label_name in label_names:
        label = await get_label_by_name(db, issue.repo_id, label_name)
        if label and label.id not in existing_label_ids:
            issue_label = IssueLabel(issue_id=issue.id, label_id=label.id)
            db.add(issue_label)
            existing_label_ids.add(label.id)

    await db.commit()
    await db.refresh(issue)
    return list(issue.labels)


async def remove_label_from_issue(
    db: AsyncSession, issue: Issue, label_name: str
) -> None:
    """Remove a label from an issue.

    Args:
        db: Async database session.
        issue: The issue to remove the label from.
        label_name: The name of the label to remove.
    """
    label = await get_label_by_name(db, issue.repo_id, label_name)
    if label is None:
        return

    result = await db.execute(
        select(IssueLabel).where(
            IssueLabel.issue_id == issue.id,
            IssueLabel.label_id == label.id,
        )
    )
    issue_label = result.scalar_one_or_none()
    if issue_label:
        await db.delete(issue_label)
        await db.commit()


async def set_labels_on_issue(
    db: AsyncSession, issue: Issue, label_names: list[str]
) -> list[Label]:
    """Replace all labels on an issue with the given set.

    Args:
        db: Async database session.
        issue: The issue to set labels on.
        label_names: List of label names to set.

    Returns:
        List of Labels now on the issue.
    """
    # Remove all existing labels
    result = await db.execute(
        select(IssueLabel).where(IssueLabel.issue_id == issue.id)
    )
    for issue_label in result.scalars().all():
        await db.delete(issue_label)

    # Add the new labels
    for label_name in label_names:
        label = await get_label_by_name(db, issue.repo_id, label_name)
        if label:
            issue_label = IssueLabel(issue_id=issue.id, label_id=label.id)
            db.add(issue_label)

    await db.commit()
    await db.refresh(issue)
    return list(issue.labels)

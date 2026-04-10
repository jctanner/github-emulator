"""Repository management service."""

import os
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Repository, User
from app.services.git_service import (
    create_initial_commit,
    delete_bare_repo,
    get_repo_size,
    init_bare_repo,
)

# Valid repository name pattern: alphanumeric, hyphens, underscores, dots
REPO_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")


async def create_repo(
    db: AsyncSession,
    owner: User,
    name: str,
    description: Optional[str] = None,
    private: bool = False,
    auto_init: bool = False,
    default_branch: str = "main",
    **kwargs,
) -> Repository:
    """Create a new repository.

    Validates the name, ensures uniqueness under the owner, creates the
    database record, initializes a bare git repo on disk, and optionally
    creates an initial commit.

    Args:
        db: Async database session.
        owner: The User who owns the repository.
        name: Repository name.
        description: Optional description.
        private: Whether the repository is private.
        auto_init: If True, create an initial commit with README.md.
        default_branch: Name of the default branch.
        **kwargs: Additional repository fields.

    Returns:
        The newly created Repository.

    Raises:
        ValueError: If the name is invalid or already taken.
    """
    # Validate repo name
    if not REPO_NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid repository name '{name}'. "
            "Only alphanumeric characters, hyphens, underscores, and dots are allowed."
        )

    # Check uniqueness under owner
    full_name = f"{owner.login}/{name}"
    result = await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"Repository '{full_name}' already exists.")

    # Determine disk path
    disk_path = os.path.join(settings.DATA_DIR, owner.login, f"{name}.git")

    # Build repository record
    repo = Repository(
        owner_id=owner.id,
        owner_type=owner.type,
        name=name,
        full_name=full_name,
        description=description,
        private=private,
        default_branch=default_branch,
        disk_path=disk_path,
        visibility="private" if private else "public",
    )

    # Apply any extra keyword arguments
    for key, value in kwargs.items():
        if hasattr(repo, key):
            setattr(repo, key, value)

    db.add(repo)
    await db.commit()
    await db.refresh(repo)

    # Initialize bare repository on disk
    await init_bare_repo(disk_path, default_branch)

    # Create initial commit if requested
    if auto_init:
        owner_name = owner.name or owner.login
        owner_email = owner.email or f"{owner.login}@users.noreply.localhost"
        await create_initial_commit(disk_path, default_branch, owner_name, owner_email)

    # Update size
    repo.size = await get_repo_size(disk_path)
    await db.commit()
    await db.refresh(repo)

    return repo


async def get_repo(
    db: AsyncSession, owner_login: str, repo_name: str
) -> Optional[Repository]:
    """Get a repository by owner login and name.

    Args:
        db: Async database session.
        owner_login: The owner's login name.
        repo_name: The repository name.

    Returns:
        The Repository, or None if not found.
    """
    full_name = f"{owner_login}/{repo_name}"
    result = await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    return result.scalar_one_or_none()


async def update_repo(
    db: AsyncSession, repo: Repository, **kwargs
) -> Repository:
    """Update a repository's attributes.

    Args:
        db: Async database session.
        repo: The repository to update.
        **kwargs: Fields to update.

    Returns:
        The updated Repository.
    """
    for key, value in kwargs.items():
        if hasattr(repo, key):
            setattr(repo, key, value)

    # Sync visibility with private flag
    if "private" in kwargs:
        repo.visibility = "private" if kwargs["private"] else "public"

    await db.commit()
    await db.refresh(repo)
    return repo


async def delete_repo(db: AsyncSession, repo: Repository) -> None:
    """Delete a repository from the database and remove the bare repo from disk.

    Args:
        db: Async database session.
        repo: The repository to delete.
    """
    disk_path = repo.disk_path
    await db.delete(repo)
    await db.commit()

    # Remove bare repo from disk
    if disk_path:
        await delete_bare_repo(disk_path)


async def list_user_repos(
    db: AsyncSession,
    owner_login: str,
    page: int = 1,
    per_page: int = 30,
    sort: str = "full_name",
    direction: str = "asc",
) -> list[Repository]:
    """List repositories for a given user.

    Args:
        db: Async database session.
        owner_login: The owner's login name.
        page: Page number (1-indexed).
        per_page: Number of results per page.
        sort: Sort field ("full_name", "created", "updated", "pushed").
        direction: Sort direction ("asc" or "desc").

    Returns:
        List of Repositories.
    """
    # Map sort parameter to column
    sort_map = {
        "full_name": Repository.full_name,
        "created": Repository.created_at,
        "updated": Repository.updated_at,
        "pushed": Repository.pushed_at,
    }
    sort_column = sort_map.get(sort, Repository.full_name)

    if direction == "desc":
        sort_column = sort_column.desc()
    else:
        sort_column = sort_column.asc()

    offset = (page - 1) * per_page

    # Join with User to filter by login
    result = await db.execute(
        select(Repository)
        .join(User, Repository.owner_id == User.id)
        .where(User.login == owner_login)
        .order_by(sort_column)
        .offset(offset)
        .limit(per_page)
    )
    return list(result.scalars().all())

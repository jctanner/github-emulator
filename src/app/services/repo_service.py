"""Repository management service."""

import os
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database_retry import commit_with_sqlite_retry
from app.models import Organization, Repository, User
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
    namespace_login: str | None = None,
    owner_type: str | None = None,
    **kwargs,
) -> Repository:
    """Create a new repository.

    Validates the name, ensures uniqueness under the owner, creates the
    database record, initializes a bare git repo on disk, and optionally
    creates an initial commit.

    Args:
        db: Async database session.
        owner: The user creating or personally owning the repository.
        name: Repository name.
        description: Optional description.
        private: Whether the repository is private.
        auto_init: If True, create an initial commit with README.md.
        default_branch: Name of the default branch.
        namespace_login: Personal or organization namespace for the repository.
        owner_type: GitHub owner type, either ``User`` or ``Organization``.
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
    namespace = namespace_login or owner.login
    repository_owner_type = owner_type or owner.type
    full_name = f"{namespace}/{name}"
    result = await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"Repository '{full_name}' already exists.")

    # Determine disk path
    if repository_owner_type == "Organization":
        disk_path = os.path.join(settings.DATA_DIR, "repos", namespace, f"{name}.git")
    else:
        disk_path = os.path.join(settings.DATA_DIR, namespace, f"{name}.git")

    # Build repository record
    organization_id = None
    if repository_owner_type == "Organization":
        organization_id = (
            await db.execute(select(Organization.id).where(Organization.login == namespace))
        ).scalar_one_or_none()
        if organization_id is None:
            raise ValueError(f"Organization '{namespace}' does not exist.")

    repo = Repository(
        owner_id=owner.id,
        organization_id=organization_id,
        owner_type=repository_owner_type,
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


async def rename_repo(
    db: AsyncSession,
    repository: Repository,
    new_name: str,
) -> Repository:
    """Rename a repository record and its bare repository directory."""
    if not REPO_NAME_PATTERN.match(new_name):
        raise ValueError(
            f"Invalid repository name '{new_name}'. "
            "Only alphanumeric characters, hyphens, underscores, and dots are allowed."
        )
    if new_name == repository.name:
        return repository

    namespace = repository.full_name.split("/", 1)[0]
    new_full_name = f"{namespace}/{new_name}"
    result = await db.execute(
        select(Repository).where(Repository.full_name == new_full_name)
    )
    if result.scalar_one_or_none() is not None:
        raise ValueError(f"Repository '{new_full_name}' already exists.")

    old_disk_path = repository.disk_path
    new_disk_path = None
    if old_disk_path:
        new_disk_path = os.path.join(
            os.path.dirname(old_disk_path), f"{new_name}.git"
        )
        if os.path.exists(new_disk_path):
            raise ValueError(f"Repository storage for '{new_full_name}' already exists.")
        if os.path.exists(old_disk_path):
            os.rename(old_disk_path, new_disk_path)

    repository.name = new_name
    repository.full_name = new_full_name
    if new_disk_path:
        repository.disk_path = new_disk_path
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        if (
            old_disk_path
            and new_disk_path
            and os.path.exists(new_disk_path)
            and not os.path.exists(old_disk_path)
        ):
            os.rename(new_disk_path, old_disk_path)
        raise
    await db.refresh(repository)
    return repository


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
    await commit_with_sqlite_retry(
        db,
        label="delete_repo",
        before_retry=lambda: db.delete(repo),
    )

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

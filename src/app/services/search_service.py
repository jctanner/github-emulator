"""Search service for repositories, issues, and users."""

import re
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Issue, Repository, User


def _parse_query(query: str) -> tuple[str, dict[str, str]]:
    """Parse a GitHub-style search query into free text and qualifiers.

    Supports qualifiers like "language:python", "stars:>10", "state:open", etc.

    Args:
        query: The raw query string.

    Returns:
        Tuple of (free_text, qualifiers_dict).
    """
    qualifiers = {}
    free_text_parts = []

    # Match qualifier patterns like "key:value" or "key:>value"
    qualifier_pattern = re.compile(r"(\w+):([^\s]+)")

    for token in query.split():
        match = qualifier_pattern.match(token)
        if match:
            qualifiers[match.group(1)] = match.group(2)
        else:
            free_text_parts.append(token)

    return " ".join(free_text_parts), qualifiers


async def search_repos(
    db: AsyncSession,
    query: str,
    page: int = 1,
    per_page: int = 30,
    sort: Optional[str] = None,
    order: str = "desc",
) -> tuple[int, list[Repository]]:
    """Search repositories.

    Supports qualifiers: language, stars (with >, <, >=, <=), user, topic.
    Free text is matched against name, full_name, and description.

    Args:
        db: Async database session.
        query: Search query string.
        page: Page number (1-indexed).
        per_page: Number of results per page.
        sort: Sort field ("stars", "forks", "updated").
        order: Sort order ("asc" or "desc").

    Returns:
        Tuple of (total_count, list_of_repositories).
    """
    free_text, qualifiers = _parse_query(query)
    offset = (page - 1) * per_page

    stmt = select(Repository)
    count_stmt = select(func.count()).select_from(Repository)

    # Apply free text search
    if free_text:
        like_pattern = f"%{free_text}%"
        text_filter = or_(
            Repository.name.ilike(like_pattern),
            Repository.full_name.ilike(like_pattern),
            Repository.description.ilike(like_pattern),
        )
        stmt = stmt.where(text_filter)
        count_stmt = count_stmt.where(text_filter)

    # Apply qualifiers
    if "language" in qualifiers:
        lang_filter = Repository.language.ilike(qualifiers["language"])
        stmt = stmt.where(lang_filter)
        count_stmt = count_stmt.where(lang_filter)

    if "user" in qualifiers:
        stmt = stmt.join(User, Repository.owner_id == User.id).where(
            User.login == qualifiers["user"]
        )
        count_stmt = count_stmt.join(User, Repository.owner_id == User.id).where(
            User.login == qualifiers["user"]
        )

    if "stars" in qualifiers:
        stars_val = qualifiers["stars"]
        stars_filter = _parse_numeric_qualifier(
            Repository.stargazers_count, stars_val
        )
        if stars_filter is not None:
            stmt = stmt.where(stars_filter)
            count_stmt = count_stmt.where(stars_filter)

    if "topic" in qualifiers:
        # JSON array contains check - SQLite specific
        topic_filter = Repository.topics.contains(qualifiers["topic"])
        stmt = stmt.where(topic_filter)
        count_stmt = count_stmt.where(topic_filter)

    # Sort
    sort_map = {
        "stars": Repository.stargazers_count,
        "forks": Repository.forks_count,
        "updated": Repository.updated_at,
    }
    if sort and sort in sort_map:
        sort_column = sort_map[sort]
        if order == "asc":
            stmt = stmt.order_by(sort_column.asc())
        else:
            stmt = stmt.order_by(sort_column.desc())
    else:
        # Default: best match (by name relevance, approximate)
        stmt = stmt.order_by(Repository.stargazers_count.desc())

    # Get total count
    count_result = await db.execute(count_stmt)
    total_count = count_result.scalar() or 0

    # Get paginated results
    stmt = stmt.offset(offset).limit(per_page)
    result = await db.execute(stmt)
    repos = list(result.scalars().all())

    return total_count, repos


async def search_issues(
    db: AsyncSession,
    query: str,
    page: int = 1,
    per_page: int = 30,
    sort: Optional[str] = None,
    order: str = "desc",
) -> tuple[int, list[Issue]]:
    """Search issues and pull requests.

    Supports qualifiers: state, author, repo, type (issue/pr), label.
    Free text is matched against title and body.

    Args:
        db: Async database session.
        query: Search query string.
        page: Page number (1-indexed).
        per_page: Number of results per page.
        sort: Sort field ("created", "updated", "comments").
        order: Sort order ("asc" or "desc").

    Returns:
        Tuple of (total_count, list_of_issues).
    """
    free_text, qualifiers = _parse_query(query)
    offset = (page - 1) * per_page

    stmt = select(Issue)
    count_stmt = select(func.count()).select_from(Issue)

    # Apply free text search
    if free_text:
        like_pattern = f"%{free_text}%"
        text_filter = or_(
            Issue.title.ilike(like_pattern),
            Issue.body.ilike(like_pattern),
        )
        stmt = stmt.where(text_filter)
        count_stmt = count_stmt.where(text_filter)

    # Apply qualifiers
    if "state" in qualifiers:
        state_filter = Issue.state == qualifiers["state"]
        stmt = stmt.where(state_filter)
        count_stmt = count_stmt.where(state_filter)

    if "author" in qualifiers:
        stmt = stmt.join(User, Issue.user_id == User.id).where(
            User.login == qualifiers["author"]
        )
        count_stmt = count_stmt.join(User, Issue.user_id == User.id).where(
            User.login == qualifiers["author"]
        )

    if "repo" in qualifiers:
        repo_full_name = qualifiers["repo"]
        stmt = stmt.join(Repository, Issue.repo_id == Repository.id).where(
            Repository.full_name == repo_full_name
        )
        count_stmt = count_stmt.join(
            Repository, Issue.repo_id == Repository.id
        ).where(Repository.full_name == repo_full_name)

    # Sort
    sort_map = {
        "created": Issue.created_at,
        "updated": Issue.updated_at,
    }
    if sort and sort in sort_map:
        sort_column = sort_map[sort]
        if order == "asc":
            stmt = stmt.order_by(sort_column.asc())
        else:
            stmt = stmt.order_by(sort_column.desc())
    else:
        stmt = stmt.order_by(Issue.created_at.desc())

    # Get total count
    count_result = await db.execute(count_stmt)
    total_count = count_result.scalar() or 0

    # Get paginated results
    stmt = stmt.offset(offset).limit(per_page)
    result = await db.execute(stmt)
    issues = list(result.scalars().all())

    return total_count, issues


async def search_users(
    db: AsyncSession,
    query: str,
    page: int = 1,
    per_page: int = 30,
    sort: Optional[str] = None,
    order: str = "desc",
) -> tuple[int, list[User]]:
    """Search users.

    Supports qualifiers: type (User/Organization), location.
    Free text is matched against login, name, and email.

    Args:
        db: Async database session.
        query: Search query string.
        page: Page number (1-indexed).
        per_page: Number of results per page.
        sort: Sort field ("joined", "repositories", "followers").
        order: Sort order ("asc" or "desc").

    Returns:
        Tuple of (total_count, list_of_users).
    """
    free_text, qualifiers = _parse_query(query)
    offset = (page - 1) * per_page

    stmt = select(User)
    count_stmt = select(func.count()).select_from(User)

    # Apply free text search
    if free_text:
        like_pattern = f"%{free_text}%"
        text_filter = or_(
            User.login.ilike(like_pattern),
            User.name.ilike(like_pattern),
            User.email.ilike(like_pattern),
        )
        stmt = stmt.where(text_filter)
        count_stmt = count_stmt.where(text_filter)

    # Apply qualifiers
    if "type" in qualifiers:
        type_filter = User.type == qualifiers["type"]
        stmt = stmt.where(type_filter)
        count_stmt = count_stmt.where(type_filter)

    if "location" in qualifiers:
        loc_filter = User.location.ilike(f"%{qualifiers['location']}%")
        stmt = stmt.where(loc_filter)
        count_stmt = count_stmt.where(loc_filter)

    # Sort
    sort_map = {
        "joined": User.created_at,
    }
    if sort and sort in sort_map:
        sort_column = sort_map[sort]
        if order == "asc":
            stmt = stmt.order_by(sort_column.asc())
        else:
            stmt = stmt.order_by(sort_column.desc())
    else:
        stmt = stmt.order_by(User.login.asc())

    # Get total count
    count_result = await db.execute(count_stmt)
    total_count = count_result.scalar() or 0

    # Get paginated results
    stmt = stmt.offset(offset).limit(per_page)
    result = await db.execute(stmt)
    users = list(result.scalars().all())

    return total_count, users


def _parse_numeric_qualifier(column, value: str):
    """Parse a numeric qualifier like ">10", ">=5", "<100", "<=50", or "42".

    Args:
        column: The SQLAlchemy column to apply the comparison to.
        value: The qualifier value string.

    Returns:
        A SQLAlchemy filter expression, or None if parsing fails.
    """
    if value.startswith(">="):
        try:
            num = int(value[2:])
            return column >= num
        except ValueError:
            return None
    elif value.startswith("<="):
        try:
            num = int(value[2:])
            return column <= num
        except ValueError:
            return None
    elif value.startswith(">"):
        try:
            num = int(value[1:])
            return column > num
        except ValueError:
            return None
    elif value.startswith("<"):
        try:
            num = int(value[1:])
            return column < num
        except ValueError:
            return None
    else:
        try:
            num = int(value)
            return column == num
        except ValueError:
            return None

"""Search endpoints -- repos, issues, users, code, commits."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, or_

from app.api.deps import CurrentUser, DbSession
from app.config import settings
from app.models.repository import Repository
from app.models.issue import Issue
from app.models.user import User
from app.models.search_index import FileContent, CommitMetadata
from app.api.repos import _repo_json
from app.api.issues import _issue_json
from app.schemas.user import UserResponse, _make_node_id

router = APIRouter(tags=["search"])

BASE = settings.BASE_URL


def _parse_qualifiers(q: str) -> tuple[str, dict[str, str]]:
    """Parse GitHub-style qualifiers from query string.

    Returns (free_text, {qualifier: value}).
    Example: "hello repo:owner/name language:python" -> ("hello", {"repo": "owner/name", "language": "python"})
    """
    parts = q.split()
    free_text_parts = []
    qualifiers = {}
    for part in parts:
        if ":" in part and not part.startswith(":"):
            key, _, value = part.partition(":")
            qualifiers[key.lower()] = value
        else:
            free_text_parts.append(part)
    return " ".join(free_text_parts), qualifiers


@router.get("/search/repositories")
async def search_repositories(
    db: DbSession,
    current_user: CurrentUser,
    q: str = Query(...),
    sort: str = Query("best-match"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """Search repositories."""
    query = select(Repository).where(
        or_(
            Repository.name.ilike(f"%{q}%"),
            Repository.full_name.ilike(f"%{q}%"),
            Repository.description.ilike(f"%{q}%"),
        )
    )

    # Hide private repos from unauthenticated users
    if current_user is None:
        query = query.where(Repository.private == False)

    if sort == "stars":
        sort_col = Repository.stargazers_count
    elif sort == "forks":
        sort_col = Repository.forks_count
    elif sort == "updated":
        sort_col = Repository.updated_at
    else:
        sort_col = Repository.stargazers_count

    if order == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    # Get total count
    from sqlalchemy import func as sa_func
    count_q = select(sa_func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.offset((page - 1) * per_page).limit(per_page)
    repos = (await db.execute(query)).scalars().all()

    return {
        "total_count": total,
        "incomplete_results": False,
        "items": [_repo_json(r, BASE) for r in repos],
    }


@router.get("/search/issues")
async def search_issues(
    db: DbSession,
    current_user: CurrentUser,
    q: str = Query(...),
    sort: str = Query("best-match"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """Search issues and pull requests."""
    query = select(Issue).where(
        or_(
            Issue.title.ilike(f"%{q}%"),
            Issue.body.ilike(f"%{q}%"),
        )
    )

    if sort == "created":
        sort_col = Issue.created_at
    elif sort == "updated":
        sort_col = Issue.updated_at
    elif sort == "comments":
        sort_col = Issue.created_at  # approximate
    else:
        sort_col = Issue.updated_at

    if order == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    from sqlalchemy import func as sa_func
    count_q = select(sa_func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.offset((page - 1) * per_page).limit(per_page)
    issues = (await db.execute(query)).scalars().all()

    return {
        "total_count": total,
        "incomplete_results": False,
        "items": [_issue_json(i, BASE) for i in issues],
    }


@router.get("/search/users")
async def search_users(
    db: DbSession,
    current_user: CurrentUser,
    q: str = Query(...),
    sort: str = Query("best-match"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """Search users."""
    query = select(User).where(
        or_(
            User.login.ilike(f"%{q}%"),
            User.name.ilike(f"%{q}%"),
            User.email.ilike(f"%{q}%"),
        )
    )

    if sort == "followers":
        sort_col = User.id  # approximation
    elif sort == "repositories":
        sort_col = User.id
    elif sort == "joined":
        sort_col = User.created_at
    else:
        sort_col = User.login

    if order == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    from sqlalchemy import func as sa_func
    count_q = select(sa_func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.offset((page - 1) * per_page).limit(per_page)
    users = (await db.execute(query)).scalars().all()

    return {
        "total_count": total,
        "incomplete_results": False,
        "items": [UserResponse.from_db(u, BASE).model_dump() for u in users],
    }


@router.get("/search/code")
async def search_code(
    db: DbSession,
    current_user: CurrentUser,
    q: str = Query(...),
    sort: str = Query("best-match"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """Search code in indexed repositories."""
    free_text, qualifiers = _parse_qualifiers(q)

    query = select(FileContent)

    # Apply qualifier filters
    if "repo" in qualifiers:
        repo_full = qualifiers["repo"]
        repo_result = await db.execute(
            select(Repository).where(Repository.full_name == repo_full)
        )
        repo = repo_result.scalar_one_or_none()
        if repo:
            query = query.where(FileContent.repo_id == repo.id)
        else:
            return {"total_count": 0, "incomplete_results": False, "items": []}

    if "language" in qualifiers:
        query = query.where(FileContent.language.ilike(qualifiers["language"]))

    if "path" in qualifiers:
        query = query.where(FileContent.file_path.ilike(f"%{qualifiers['path']}%"))

    # Free text search on content and file path
    if free_text:
        query = query.where(
            or_(
                FileContent.content.ilike(f"%{free_text}%"),
                FileContent.file_path.ilike(f"%{free_text}%"),
            )
        )

    from sqlalchemy import func as sa_func
    count_q = select(sa_func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.offset((page - 1) * per_page).limit(per_page)
    results = (await db.execute(query)).scalars().all()

    # Build response items
    items = []
    for fc in results:
        # Look up repo info
        repo_result = await db.execute(
            select(Repository).where(Repository.id == fc.repo_id)
        )
        repo = repo_result.scalar_one_or_none()
        repo_full = repo.full_name if repo else ""
        owner_login = repo_full.split("/")[0] if "/" in repo_full else ""
        repo_name = repo_full.split("/")[1] if "/" in repo_full else ""

        # Extract text matches (first matching line)
        text_matches = []
        if fc.content and free_text:
            for line in fc.content.split("\n"):
                if free_text.lower() in line.lower():
                    text_matches.append({
                        "fragment": line.strip()[:200],
                        "matches": [{"text": free_text}],
                    })
                    if len(text_matches) >= 3:
                        break

        api = f"{BASE}/api/v3"
        items.append({
            "name": fc.file_path.split("/")[-1],
            "path": fc.file_path,
            "sha": fc.blob_sha,
            "url": f"{api}/repos/{repo_full}/contents/{fc.file_path}?ref={fc.ref}",
            "git_url": f"{api}/repos/{repo_full}/git/blobs/{fc.blob_sha}",
            "html_url": f"{BASE}/{repo_full}/blob/{fc.ref}/{fc.file_path}",
            "repository": _repo_json(repo, BASE) if repo else {},
            "score": 1.0,
            "text_matches": text_matches,
        })

    return {
        "total_count": total,
        "incomplete_results": False,
        "items": items,
    }


@router.get("/search/commits")
async def search_commits(
    db: DbSession,
    current_user: CurrentUser,
    q: str = Query(...),
    sort: str = Query("best-match"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """Search commits in indexed repositories."""
    free_text, qualifiers = _parse_qualifiers(q)

    query = select(CommitMetadata)

    # Apply qualifier filters
    if "repo" in qualifiers:
        repo_full = qualifiers["repo"]
        repo_result = await db.execute(
            select(Repository).where(Repository.full_name == repo_full)
        )
        repo = repo_result.scalar_one_or_none()
        if repo:
            query = query.where(CommitMetadata.repo_id == repo.id)
        else:
            return {"total_count": 0, "incomplete_results": False, "items": []}

    if "author" in qualifiers:
        author = qualifiers["author"]
        query = query.where(
            or_(
                CommitMetadata.author_name.ilike(f"%{author}%"),
                CommitMetadata.author_email.ilike(f"%{author}%"),
            )
        )

    # Free text search on message
    if free_text:
        query = query.where(CommitMetadata.message.ilike(f"%{free_text}%"))

    if sort == "author-date":
        sort_col = CommitMetadata.author_date
    elif sort == "committer-date":
        sort_col = CommitMetadata.committer_date
    else:
        sort_col = CommitMetadata.id

    if order == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    from sqlalchemy import func as sa_func
    count_q = select(sa_func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.offset((page - 1) * per_page).limit(per_page)
    results = (await db.execute(query)).scalars().all()

    items = []
    for cm in results:
        repo_result = await db.execute(
            select(Repository).where(Repository.id == cm.repo_id)
        )
        repo = repo_result.scalar_one_or_none()
        repo_full = repo.full_name if repo else ""

        api = f"{BASE}/api/v3"
        items.append({
            "url": f"{api}/repos/{repo_full}/commits/{cm.commit_sha}",
            "sha": cm.commit_sha,
            "html_url": f"{BASE}/{repo_full}/commit/{cm.commit_sha}",
            "commit": {
                "url": f"{api}/repos/{repo_full}/git/commits/{cm.commit_sha}",
                "message": cm.message,
                "author": {
                    "name": cm.author_name,
                    "email": cm.author_email,
                    "date": cm.author_date,
                },
                "committer": {
                    "name": cm.committer_name,
                    "email": cm.committer_email,
                    "date": cm.committer_date,
                },
            },
            "repository": _repo_json(repo, BASE) if repo else {},
            "score": 1.0,
        })

    return {
        "total_count": total,
        "incomplete_results": False,
        "items": items,
    }

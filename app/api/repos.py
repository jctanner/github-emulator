"""Repository endpoints -- CRUD, listing, and org repos."""

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import select, func as sa_func

from app.api.deps import AuthUser, CurrentUser, DbSession
from app.config import settings
from app.models.repository import Repository
from app.models.user import User
from app.models.organization import Organization
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id

router = APIRouter(tags=["repos"])

BASE = settings.BASE_URL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repo_json(repo: Repository, base_url: str) -> dict:
    """Build a GitHub-compatible repository JSON object."""
    api = f"{base_url}/api/v3"
    owner = repo.owner
    owner_simple = SimpleUser.from_db(owner, base_url).model_dump() if owner else {}

    repo_url = f"{api}/repos/{repo.full_name}"
    html_url = f"{base_url}/{repo.full_name}"

    return {
        "id": repo.id,
        "node_id": _make_node_id("Repository", repo.id),
        "name": repo.name,
        "full_name": repo.full_name,
        "private": repo.private,
        "owner": owner_simple,
        "html_url": html_url,
        "description": repo.description,
        "fork": repo.fork,
        "url": repo_url,
        "forks_url": f"{repo_url}/forks",
        "keys_url": f"{repo_url}/keys{{/key_id}}",
        "collaborators_url": f"{repo_url}/collaborators{{/collaborator}}",
        "teams_url": f"{repo_url}/teams",
        "hooks_url": f"{repo_url}/hooks",
        "issue_events_url": f"{repo_url}/issues/events{{/number}}",
        "events_url": f"{repo_url}/events",
        "assignees_url": f"{repo_url}/assignees{{/user}}",
        "branches_url": f"{repo_url}/branches{{/branch}}",
        "tags_url": f"{repo_url}/tags",
        "blobs_url": f"{repo_url}/git/blobs{{/sha}}",
        "git_tags_url": f"{repo_url}/git/tags{{/sha}}",
        "git_refs_url": f"{repo_url}/git/refs{{/sha}}",
        "trees_url": f"{repo_url}/git/trees{{/sha}}",
        "statuses_url": f"{repo_url}/statuses/{{sha}}",
        "languages_url": f"{repo_url}/languages",
        "stargazers_url": f"{repo_url}/stargazers",
        "contributors_url": f"{repo_url}/contributors",
        "subscribers_url": f"{repo_url}/subscribers",
        "subscription_url": f"{repo_url}/subscription",
        "commits_url": f"{repo_url}/commits{{/sha}}",
        "git_commits_url": f"{repo_url}/git/commits{{/sha}}",
        "comments_url": f"{repo_url}/comments{{/number}}",
        "issue_comment_url": f"{repo_url}/issues/comments{{/number}}",
        "contents_url": f"{repo_url}/contents/{{+path}}",
        "compare_url": f"{repo_url}/compare/{{base}}...{{head}}",
        "merges_url": f"{repo_url}/merges",
        "archive_url": f"{repo_url}/{{archive_format}}{{/ref}}",
        "downloads_url": f"{repo_url}/downloads",
        "issues_url": f"{repo_url}/issues{{/number}}",
        "pulls_url": f"{repo_url}/pulls{{/number}}",
        "milestones_url": f"{repo_url}/milestones{{/number}}",
        "notifications_url": f"{repo_url}/notifications{{?since,all,participating}}",
        "labels_url": f"{repo_url}/labels{{/name}}",
        "releases_url": f"{repo_url}/releases{{/id}}",
        "deployments_url": f"{repo_url}/deployments",
        "created_at": _fmt_dt(repo.created_at),
        "updated_at": _fmt_dt(repo.updated_at),
        "pushed_at": _fmt_dt(repo.pushed_at),
        "git_url": f"git://{base_url.split('://', 1)[-1]}/{repo.full_name}.git",
        "ssh_url": f"git@{base_url.split('://', 1)[-1]}:{repo.full_name}.git",
        "clone_url": f"{base_url}/{repo.full_name}.git",
        "svn_url": f"{base_url}/{repo.full_name}",
        "homepage": repo.homepage,
        "size": repo.size,
        "stargazers_count": repo.stargazers_count,
        "watchers_count": repo.watchers_count,
        "language": repo.language,
        "has_issues": repo.has_issues,
        "has_projects": repo.has_projects,
        "has_downloads": repo.has_downloads,
        "has_wiki": repo.has_wiki,
        "has_pages": repo.has_pages,
        "has_discussions": repo.has_discussions,
        "forks_count": repo.forks_count,
        "mirror_url": None,
        "archived": repo.archived,
        "disabled": repo.disabled,
        "open_issues_count": repo.open_issues_count,
        "license": None,
        "allow_forking": repo.allow_forking,
        "is_template": repo.is_template,
        "web_commit_signoff_required": repo.web_commit_signoff_required,
        "topics": repo.topics or [],
        "visibility": repo.visibility,
        "forks": repo.forks_count,
        "open_issues": repo.open_issues_count,
        "watchers": repo.watchers_count,
        "default_branch": repo.default_branch,
        "permissions": {
            "admin": True,
            "maintain": True,
            "push": True,
            "triage": True,
            "pull": True,
        },
    }


def _pagination_links(
    request: Request, page: int, per_page: int, total: int
) -> str:
    """Build RFC 5988 Link header value for pagination."""
    base = str(request.url).split("?")[0]
    last_page = max(1, (total + per_page - 1) // per_page)
    parts: list[str] = []
    if page < last_page:
        parts.append(f'<{base}?page={page + 1}&per_page={per_page}>; rel="next"')
        parts.append(f'<{base}?page={last_page}&per_page={per_page}>; rel="last"')
    if page > 1:
        parts.append(f'<{base}?page={page - 1}&per_page={per_page}>; rel="prev"')
        parts.append(f'<{base}?page=1&per_page={per_page}>; rel="first"')
    return ", ".join(parts)


async def _init_bare_repo(disk_path: str, default_branch: str = "main") -> None:
    """Initialise a bare git repository on disk."""
    os.makedirs(disk_path, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "git", "init", "--bare", "--initial-branch", default_branch, disk_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/user/repos", status_code=201)
async def create_repo_for_user(body: dict, user: AuthUser, db: DbSession):
    """Create a new repository for the authenticated user."""
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=422, detail="name is required")

    full_name = f"{user.login}/{name}"
    existing = await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=422, detail="Repository already exists")

    disk_path = os.path.join(settings.DATA_DIR, "repos", user.login, f"{name}.git")

    repo = Repository(
        owner_id=user.id,
        owner_type="User",
        name=name,
        full_name=full_name,
        description=body.get("description"),
        private=body.get("private", False),
        default_branch=body.get("default_branch", "main"),
        disk_path=disk_path,
        visibility="private" if body.get("private") else "public",
        has_issues=body.get("has_issues", True),
        has_wiki=body.get("has_wiki", True),
        has_projects=body.get("has_projects", True),
        has_downloads=body.get("has_downloads", True),
        homepage=body.get("homepage"),
        is_template=body.get("is_template", False),
    )

    db.add(repo)
    await db.commit()
    await db.refresh(repo)

    # Initialize bare repo on disk
    await _init_bare_repo(disk_path, repo.default_branch)

    # Create initial commit if auto_init is requested
    if body.get("auto_init"):
        from app.git.bare_repo import create_initial_commit
        commit_sha = await create_initial_commit(
            disk_path,
            repo.default_branch,
            name,
            user.name or user.login,
            user.email or f"{user.login}@github-emulator.local",
        )
        if commit_sha:
            repo.pushed_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(repo)

    return _repo_json(repo, BASE)


@router.get("/repos/{owner}/{repo}")
async def get_repo(owner: str, repo: str, db: DbSession, current_user: CurrentUser):
    """Get a single repository."""
    full_name = f"{owner}/{repo}"
    result = await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise HTTPException(status_code=404, detail="Not Found")

    # Private repo access check
    if repository.private:
        if current_user is None or (
            current_user.id != repository.owner_id and not current_user.site_admin
        ):
            raise HTTPException(status_code=404, detail="Not Found")

    return _repo_json(repository, BASE)


@router.patch("/repos/{owner}/{repo}")
async def update_repo(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession
):
    """Update repository settings."""
    full_name = f"{owner}/{repo}"
    result = await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise HTTPException(status_code=404, detail="Not Found")

    if user.id != repository.owner_id and not user.site_admin:
        raise HTTPException(status_code=403, detail="Forbidden")

    updatable = [
        "description", "homepage", "private", "visibility",
        "has_issues", "has_projects", "has_wiki", "has_downloads",
        "has_pages", "has_discussions", "default_branch", "archived",
        "allow_forking", "is_template", "web_commit_signoff_required",
    ]
    for key in updatable:
        if key in body:
            setattr(repository, key, body[key])

    # Handle name change
    if "name" in body and body["name"] != repository.name:
        new_name = body["name"]
        repository.name = new_name
        repository.full_name = f"{owner}/{new_name}"

    await db.commit()
    await db.refresh(repository)
    return _repo_json(repository, BASE)


@router.delete("/repos/{owner}/{repo}", status_code=204)
async def delete_repo(owner: str, repo: str, user: AuthUser, db: DbSession):
    """Delete a repository."""
    full_name = f"{owner}/{repo}"
    result = await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise HTTPException(status_code=404, detail="Not Found")

    if user.id != repository.owner_id and not user.site_admin:
        raise HTTPException(status_code=403, detail="Must have admin rights to delete")

    await db.delete(repository)
    await db.commit()
    return Response(status_code=204)


@router.get("/users/{username}/repos")
async def list_user_repos(
    username: str,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    type: str = Query("all"),
    sort: str = Query("full_name"),
    direction: str = Query("asc"),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
):
    """List repositories for a user."""
    result = await db.execute(select(User).where(User.login == username))
    owner = result.scalar_one_or_none()
    if owner is None:
        raise HTTPException(status_code=404, detail="Not Found")

    query = select(Repository).where(Repository.owner_id == owner.id)

    # Filter out private repos for unauthenticated / non-owner users
    if current_user is None or (
        current_user.id != owner.id and not current_user.site_admin
    ):
        query = query.where(Repository.private == False)

    if type == "forks":
        query = query.where(Repository.fork == True)
    elif type == "sources":
        query = query.where(Repository.fork == False)

    # Sorting
    sort_col = getattr(Repository, sort, Repository.full_name)
    if direction == "desc":
        query = query.order_by(sort_col.desc())
    else:
        query = query.order_by(sort_col.asc())

    # Total for pagination headers
    count_q = select(sa_func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.offset((page - 1) * per_page).limit(per_page)
    repos = (await db.execute(query)).scalars().all()

    response = Response()
    link = _pagination_links(request, page, per_page, total)
    headers = {}
    if link:
        headers["Link"] = link

    from fastapi.responses import JSONResponse

    return JSONResponse(
        content=[_repo_json(r, BASE) for r in repos],
        headers=headers,
    )


@router.get("/user/repos")
async def list_authenticated_user_repos(
    request: Request,
    user: AuthUser,
    db: DbSession,
    type: str = Query("all"),
    sort: str = Query("full_name"),
    direction: str = Query("asc"),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
):
    """List repositories for the authenticated user."""
    query = select(Repository).where(Repository.owner_id == user.id)

    if type == "public":
        query = query.where(Repository.private == False)
    elif type == "private":
        query = query.where(Repository.private == True)
    elif type == "forks":
        query = query.where(Repository.fork == True)

    sort_col = getattr(Repository, sort, Repository.full_name)
    if direction == "desc":
        query = query.order_by(sort_col.desc())
    else:
        query = query.order_by(sort_col.asc())

    count_q = select(sa_func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.offset((page - 1) * per_page).limit(per_page)
    repos = (await db.execute(query)).scalars().all()

    headers = {}
    link = _pagination_links(request, page, per_page, total)
    if link:
        headers["Link"] = link

    from fastapi.responses import JSONResponse

    return JSONResponse(
        content=[_repo_json(r, BASE) for r in repos],
        headers=headers,
    )


@router.post("/orgs/{org}/repos", status_code=201)
async def create_org_repo(
    org: str, body: dict, user: AuthUser, db: DbSession
):
    """Create a repository under an organisation."""
    result = await db.execute(
        select(Organization).where(Organization.login == org)
    )
    organisation = result.scalar_one_or_none()
    if organisation is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    name = body.get("name")
    if not name:
        raise HTTPException(status_code=422, detail="name is required")

    full_name = f"{org}/{name}"
    existing = await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=422, detail="Repository already exists")

    disk_path = os.path.join(settings.DATA_DIR, "repos", org, f"{name}.git")

    repo = Repository(
        owner_id=user.id,
        owner_type="Organization",
        name=name,
        full_name=full_name,
        description=body.get("description"),
        private=body.get("private", False),
        default_branch=body.get("default_branch", "main"),
        disk_path=disk_path,
        visibility="private" if body.get("private") else "public",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)

    await _init_bare_repo(disk_path, repo.default_branch)

    return _repo_json(repo, BASE)

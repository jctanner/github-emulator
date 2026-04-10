"""Public web frontend routes for browsing repos, issues, PRs, and code."""

import os
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWSError, jws
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.git.bare_repo import (
    get_branches,
    get_commit_count,
    get_commit_diff,
    get_commit_info,
    get_default_branch,
    get_file_content,
    get_log,
    get_tags,
    list_tree,
    write_file,
)
from app.models.comment import IssueComment
from app.models.issue import Issue
from app.models.organization import Organization
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.user import User
from app.services.auth_service import verify_password
from app.services import issue_service, pr_service, repo_service

_WEB_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_DIR = os.path.join(_WEB_DIR, "templates")

templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter(prefix="/ui", tags=["web"])

_URL_PREFIX = "/ui"


# ---------------------------------------------------------------------------
# Session helpers (signed cookie via python-jose JWS)
# ---------------------------------------------------------------------------

_ALGORITHM = "HS256"


def _sign_session(username: str) -> str:
    """Create a JWS-signed session token containing the username."""
    return jws.sign(
        username.encode("utf-8"),
        settings.SECRET_KEY,
        algorithm=_ALGORITHM,
    )


def _verify_session(token: str) -> Optional[str]:
    """Verify a JWS session token and return the username, or None."""
    try:
        payload = jws.verify(token, settings.SECRET_KEY, algorithms=[_ALGORITHM])
        return payload.decode("utf-8")
    except (JWSError, Exception):
        return None


async def _get_current_user(
    request: Request, db: AsyncSession
) -> Optional[User]:
    """Extract the logged-in user from the ui_session cookie."""
    token = request.cookies.get("ui_session")
    if not token:
        return None
    username = _verify_session(token)
    if not username:
        return None
    result = await db.execute(select(User).where(User.login == username))
    return result.scalar_one_or_none()


def _ctx(request: Request, **extra) -> dict:
    context = dict(extra)
    context["request"] = request
    context["url_prefix"] = _URL_PREFIX
    # current_user is set by individual route handlers via extra kwargs
    context.setdefault("current_user", None)
    return context


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Login form."""
    current_user = await _get_current_user(request, db)
    if current_user:
        return RedirectResponse(url="/ui/", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context=_ctx(request, error=None),
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Validate credentials and set session cookie."""
    result = await db.execute(select(User).where(User.login == username))
    user = result.scalar_one_or_none()

    if user and verify_password(password, user.hashed_password):
        response = RedirectResponse(url="/ui/", status_code=302)
        response.set_cookie(
            key="ui_session",
            value=_sign_session(username),
            path="/ui",
            httponly=True,
            samesite="lax",
        )
        return response

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context=_ctx(request, error="Invalid username or password."),
    )


@router.get("/logout")
async def logout():
    """Clear session cookie and redirect to landing page."""
    response = RedirectResponse(url="/ui/", status_code=302)
    response.delete_cookie(key="ui_session", path="/ui")
    return response


# ---------------------------------------------------------------------------
# New repository
# ---------------------------------------------------------------------------

@router.get("/new", response_class=HTMLResponse)
async def new_repo_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Form for creating a new repository."""
    current_user = await _get_current_user(request, db)
    if not current_user:
        return RedirectResponse(url="/ui/login", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="new_repo.html",
        context=_ctx(request, current_user=current_user, error=None),
    )


@router.post("/new", response_class=HTMLResponse)
async def new_repo_submit(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    private: bool = Form(False),
    auto_init: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    """Create a new repository."""
    current_user = await _get_current_user(request, db)
    if not current_user:
        return RedirectResponse(url="/ui/login", status_code=302)

    try:
        repo = await repo_service.create_repo(
            db,
            owner=current_user,
            name=name,
            description=description or None,
            private=private,
            auto_init=auto_init,
        )
        return RedirectResponse(
            url=f"/ui/{current_user.login}/{repo.name}", status_code=302
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request,
            name="new_repo.html",
            context=_ctx(request, current_user=current_user, error=str(exc)),
        )


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def landing(request: Request, db: AsyncSession = Depends(get_db)):
    """Landing page showing recent repositories."""
    current_user = await _get_current_user(request, db)

    result = await db.execute(
        select(Repository).order_by(Repository.updated_at.desc()).limit(20)
    )
    repos = list(result.scalars().all())

    # Attach owner_login for template use
    repo_list = []
    for repo in repos:
        repo.owner_login = repo.owner.login if repo.owner else "unknown"
        repo_list.append(repo)

    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context=_ctx(request, repos=repo_list, current_user=current_user),
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Search repositories and users."""
    current_user = await _get_current_user(request, db)
    repos = []
    users = []
    if q:
        pattern = f"%{q}%"
        result = await db.execute(
            select(Repository).where(
                or_(
                    Repository.name.ilike(pattern),
                    Repository.full_name.ilike(pattern),
                    Repository.description.ilike(pattern),
                )
            ).limit(20)
        )
        repos = list(result.scalars().all())
        for repo in repos:
            repo.owner_login = repo.owner.login if repo.owner else "unknown"

        result = await db.execute(
            select(User).where(
                or_(
                    User.login.ilike(pattern),
                    User.name.ilike(pattern),
                )
            ).limit(20)
        )
        users = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context=_ctx(
            request, query=q, repos=repos, users=users,
            current_user=current_user,
        ),
    )


# ---------------------------------------------------------------------------
# User / Org profile
# ---------------------------------------------------------------------------

@router.get("/{owner}", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    owner: str,
    db: AsyncSession = Depends(get_db),
):
    """User or organization profile page with their repositories."""
    current_user = await _get_current_user(request, db)

    # Try user first
    result = await db.execute(select(User).where(User.login == owner))
    profile = result.scalar_one_or_none()

    if profile is None:
        # Try organization
        result = await db.execute(
            select(Organization).where(Organization.login == owner)
        )
        profile = result.scalar_one_or_none()

    if profile is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    # Get repos
    result = await db.execute(
        select(Repository).where(
            Repository.owner_id == profile.id
        ).order_by(Repository.updated_at.desc())
    )
    repos = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context=_ctx(request, profile=profile, repos=repos, current_user=current_user),
    )


# ---------------------------------------------------------------------------
# Repository overview
# ---------------------------------------------------------------------------

@router.get("/{owner}/{repo_name}", response_class=HTMLResponse)
async def repo_page(
    request: Request,
    owner: str,
    repo_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Repository overview with file tree and README."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    tree_entries = None
    readme_content = None
    default_branch = repo.default_branch or "main"
    commit_count = 0
    branch_count = 0
    tag_count = 0

    if repo.disk_path and os.path.isdir(repo.disk_path):
        tree_entries = await list_tree(repo.disk_path, default_branch)
        if tree_entries:
            # Sort: directories first, then files
            tree_entries.sort(key=lambda e: (0 if e["type"] == "tree" else 1, e["name"]))
            # Try to find and read README
            for entry in tree_entries:
                if entry["name"].lower().startswith("readme"):
                    raw = await get_file_content(
                        repo.disk_path, default_branch, entry["name"]
                    )
                    if raw:
                        try:
                            readme_content = raw.decode("utf-8", errors="replace")
                        except Exception:
                            readme_content = None
                    break

        commit_count = await get_commit_count(repo.disk_path, default_branch)
        branches = await get_branches(repo.disk_path)
        branch_count = len(branches)
        tags = await get_tags(repo.disk_path)
        tag_count = len(tags)

    # Open issue/PR counts for tab counters
    pr_issue_ids = select(PullRequest.issue_id)
    open_issues_count = (await db.execute(
        select(func.count(Issue.id)).where(
            Issue.repo_id == repo.id, Issue.state == "open",
            ~Issue.id.in_(pr_issue_ids),
        )
    )).scalar() or 0

    open_pulls_count = (await db.execute(
        select(func.count(Issue.id)).where(
            Issue.repo_id == repo.id, Issue.state == "open",
            Issue.id.in_(pr_issue_ids),
        )
    )).scalar() or 0

    return templates.TemplateResponse(
        request=request,
        name="repo.html",
        context=_ctx(
            request,
            owner=owner,
            repo=repo,
            repo_name=repo.name,
            tree_entries=tree_entries,
            readme_content=readme_content,
            default_branch=default_branch,
            open_issues_count=open_issues_count,
            open_pulls_count=open_pulls_count,
            commit_count=commit_count,
            branch_count=branch_count,
            tag_count=tag_count,
            current_user=current_user,
        ),
    )


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

@router.get("/{owner}/{repo_name}/issues/new", response_class=HTMLResponse)
async def new_issue_page(
    request: Request,
    owner: str,
    repo_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Form for creating a new issue."""
    current_user = await _get_current_user(request, db)
    if not current_user:
        return RedirectResponse(url="/ui/login", status_code=302)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    return templates.TemplateResponse(
        request=request,
        name="new_issue.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            current_user=current_user, error=None,
        ),
    )


@router.post("/{owner}/{repo_name}/issues/new", response_class=HTMLResponse)
async def new_issue_submit(
    request: Request,
    owner: str,
    repo_name: str,
    title: str = Form(...),
    body: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Create a new issue."""
    current_user = await _get_current_user(request, db)
    if not current_user:
        return RedirectResponse(url="/ui/login", status_code=302)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    issue = await issue_service.create_issue(
        db, repo=repo, user=current_user,
        title=title, body=body or None,
    )
    return RedirectResponse(
        url=f"/ui/{owner}/{repo_name}/issues/{issue.number}", status_code=302
    )


@router.get("/{owner}/{repo_name}/issues", response_class=HTMLResponse)
async def issues_list(
    request: Request,
    owner: str,
    repo_name: str,
    state: str = Query("open"),
    db: AsyncSession = Depends(get_db),
):
    """List issues for a repository."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    pr_issue_ids = select(PullRequest.issue_id)
    query = select(Issue).where(
        Issue.repo_id == repo.id,
        ~Issue.id.in_(pr_issue_ids),
    )
    if state in ("open", "closed"):
        query = query.where(Issue.state == state)
    query = query.order_by(Issue.number.desc())

    result = await db.execute(query)
    issues = list(result.scalars().all())
    for issue in issues:
        issue.user_login = issue.user.login if issue.user else "unknown"

    # Counts
    open_count = (await db.execute(
        select(func.count(Issue.id)).where(
            Issue.repo_id == repo.id, Issue.state == "open",
            ~Issue.id.in_(pr_issue_ids),
        )
    )).scalar() or 0
    closed_count = (await db.execute(
        select(func.count(Issue.id)).where(
            Issue.repo_id == repo.id, Issue.state == "closed",
            ~Issue.id.in_(pr_issue_ids),
        )
    )).scalar() or 0

    return templates.TemplateResponse(
        request=request,
        name="issues.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            issues=issues, state=state,
            open_count=open_count, closed_count=closed_count,
            open_issues_count=open_count,
            current_user=current_user,
        ),
    )


@router.get("/{owner}/{repo_name}/issues/{number:int}", response_class=HTMLResponse)
async def issue_detail(
    request: Request,
    owner: str,
    repo_name: str,
    number: int,
    db: AsyncSession = Depends(get_db),
):
    """Single issue detail with comments."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    result = await db.execute(
        select(Issue).where(Issue.repo_id == repo.id, Issue.number == number)
    )
    issue = result.scalar_one_or_none()
    if issue is None:
        return HTMLResponse(content="<h1>404 - Issue Not Found</h1>", status_code=404)

    issue.user_login = issue.user.login if issue.user else "unknown"

    result = await db.execute(
        select(IssueComment).where(
            IssueComment.issue_id == issue.id
        ).order_by(IssueComment.created_at)
    )
    comments = list(result.scalars().all())
    for c in comments:
        c.user_login = c.user.login if c.user else "unknown"

    return templates.TemplateResponse(
        request=request,
        name="issue_detail.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            issue=issue, comments=comments,
            current_user=current_user,
        ),
    )


# ---------------------------------------------------------------------------
# Pull Requests
# ---------------------------------------------------------------------------

@router.get("/{owner}/{repo_name}/pulls/new", response_class=HTMLResponse)
async def new_pull_page(
    request: Request,
    owner: str,
    repo_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Form for creating a new pull request."""
    current_user = await _get_current_user(request, db)
    if not current_user:
        return RedirectResponse(url="/ui/login", status_code=302)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    branches = []
    default_branch = repo.default_branch or "main"
    if repo.disk_path and os.path.isdir(repo.disk_path):
        branches = await get_branches(repo.disk_path)

    return templates.TemplateResponse(
        request=request,
        name="new_pull.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            branches=branches, default_branch=default_branch,
            current_user=current_user, error=None,
        ),
    )


@router.post("/{owner}/{repo_name}/pulls/new", response_class=HTMLResponse)
async def new_pull_submit(
    request: Request,
    owner: str,
    repo_name: str,
    title: str = Form(...),
    body: str = Form(""),
    head_ref: str = Form(...),
    base_ref: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Create a new pull request."""
    current_user = await _get_current_user(request, db)
    if not current_user:
        return RedirectResponse(url="/ui/login", status_code=302)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    try:
        issue, pr = await pr_service.create_pr(
            db, repo=repo, user=current_user,
            title=title, body=body or None,
            head_ref=head_ref, base_ref=base_ref,
        )
        return RedirectResponse(
            url=f"/ui/{owner}/{repo_name}/pulls/{issue.number}", status_code=302
        )
    except Exception as exc:
        branches = []
        default_branch = repo.default_branch or "main"
        if repo.disk_path and os.path.isdir(repo.disk_path):
            branches = await get_branches(repo.disk_path)
        return templates.TemplateResponse(
            request=request,
            name="new_pull.html",
            context=_ctx(
                request, owner=owner, repo=repo, repo_name=repo.name,
                branches=branches, default_branch=default_branch,
                current_user=current_user, error=str(exc),
            ),
        )


@router.get("/{owner}/{repo_name}/pulls", response_class=HTMLResponse)
async def pulls_list(
    request: Request,
    owner: str,
    repo_name: str,
    state: str = Query("open"),
    db: AsyncSession = Depends(get_db),
):
    """List pull requests for a repository."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    result = await db.execute(
        select(PullRequest).where(
            PullRequest.repo_id == repo.id
        ).order_by(PullRequest.id.desc())
    )
    all_pulls = list(result.scalars().all())

    # Enrich and filter
    pulls = []
    open_count = 0
    closed_count = 0
    for pr in all_pulls:
        pr.number = pr.issue.number if pr.issue else 0
        pr.title = pr.issue.title if pr.issue else "Untitled"
        pr.state = pr.issue.state if pr.issue else "open"
        pr.user_login = pr.issue.user.login if pr.issue and pr.issue.user else "unknown"
        pr.updated_at = pr.issue.updated_at if pr.issue else None
        if pr.state == "open":
            open_count += 1
        else:
            closed_count += 1
        if state == "open" and pr.state == "open":
            pulls.append(pr)
        elif state == "closed" and pr.state != "open":
            pulls.append(pr)
        elif state not in ("open", "closed"):
            pulls.append(pr)

    return templates.TemplateResponse(
        request=request,
        name="pulls.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            pulls=pulls, state=state,
            open_count=open_count, closed_count=closed_count,
            open_pulls_count=open_count,
            current_user=current_user,
        ),
    )


@router.get("/{owner}/{repo_name}/pulls/{number:int}", response_class=HTMLResponse)
async def pull_detail(
    request: Request,
    owner: str,
    repo_name: str,
    number: int,
    db: AsyncSession = Depends(get_db),
):
    """Single pull request detail."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    # Find the issue for this PR number
    result = await db.execute(
        select(Issue).where(Issue.repo_id == repo.id, Issue.number == number)
    )
    issue = result.scalar_one_or_none()
    if issue is None or issue.pull_request is None:
        return HTMLResponse(content="<h1>404 - PR Not Found</h1>", status_code=404)

    pr = issue.pull_request
    pr.number = issue.number
    pr.title = issue.title
    pr.body = issue.body
    pr.state = issue.state
    pr.user_login = issue.user.login if issue.user else "unknown"
    pr.created_at = issue.created_at

    # Get comments on the issue
    result = await db.execute(
        select(IssueComment).where(
            IssueComment.issue_id == issue.id
        ).order_by(IssueComment.created_at)
    )
    comments = list(result.scalars().all())
    for c in comments:
        c.user_login = c.user.login if c.user else "unknown"

    return templates.TemplateResponse(
        request=request,
        name="pull_detail.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            pr=pr, comments=comments,
            current_user=current_user,
        ),
    )


# ---------------------------------------------------------------------------
# Create new file
# ---------------------------------------------------------------------------

@router.get("/{owner}/{repo_name}/new/{ref}", response_class=HTMLResponse)
@router.get("/{owner}/{repo_name}/new/{ref}/{path:path}", response_class=HTMLResponse)
async def new_file_page(
    request: Request,
    owner: str,
    repo_name: str,
    ref: str,
    path: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Form for creating a new file."""
    current_user = await _get_current_user(request, db)
    if not current_user:
        return RedirectResponse(url="/ui/login", status_code=302)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    return templates.TemplateResponse(
        request=request,
        name="new_file.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            ref=ref, dir_path=path, current_user=current_user, error=None,
        ),
    )


@router.post("/{owner}/{repo_name}/new/{ref}", response_class=HTMLResponse)
@router.post("/{owner}/{repo_name}/new/{ref}/{path:path}", response_class=HTMLResponse)
async def new_file_submit(
    request: Request,
    owner: str,
    repo_name: str,
    ref: str,
    path: str = "",
    filename: str = Form(...),
    content: str = Form(""),
    commit_message: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Create a new file in the repository."""
    current_user = await _get_current_user(request, db)
    if not current_user:
        return RedirectResponse(url="/ui/login", status_code=302)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    # Build full file path
    full_path = f"{path}/{filename}" if path else filename

    if not commit_message:
        commit_message = f"Create {full_path}"

    try:
        await write_file(
            disk_path=repo.disk_path,
            branch=ref,
            path=full_path,
            content=content.encode("utf-8"),
            message=commit_message,
            author_name=current_user.name or current_user.login,
            author_email=current_user.email or f"{current_user.login}@users.noreply.github-emulator.local",
        )
        return RedirectResponse(
            url=f"/ui/{owner}/{repo_name}/blob/{ref}/{full_path}",
            status_code=302,
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="new_file.html",
            context=_ctx(
                request, owner=owner, repo=repo, repo_name=repo.name,
                ref=ref, dir_path=path, current_user=current_user,
                error=str(exc),
            ),
        )


# ---------------------------------------------------------------------------
# Edit file
# ---------------------------------------------------------------------------

@router.get("/{owner}/{repo_name}/edit/{ref}/{path:path}", response_class=HTMLResponse)
async def edit_file_page(
    request: Request,
    owner: str,
    repo_name: str,
    ref: str,
    path: str,
    db: AsyncSession = Depends(get_db),
):
    """Edit form for an existing file, pre-filled with current content."""
    current_user = await _get_current_user(request, db)
    if not current_user:
        return RedirectResponse(url="/ui/login", status_code=302)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    content = ""
    if repo.disk_path and os.path.isdir(repo.disk_path):
        raw = await get_file_content(repo.disk_path, ref, path)
        if raw:
            content = raw.decode("utf-8", errors="replace")

    return templates.TemplateResponse(
        request=request,
        name="edit_file.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            ref=ref, path=path, content=content,
            current_user=current_user, error=None,
        ),
    )


@router.post("/{owner}/{repo_name}/edit/{ref}/{path:path}", response_class=HTMLResponse)
async def edit_file_submit(
    request: Request,
    owner: str,
    repo_name: str,
    ref: str,
    path: str,
    content: str = Form(""),
    commit_message: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Save edits to an existing file."""
    current_user = await _get_current_user(request, db)
    if not current_user:
        return RedirectResponse(url="/ui/login", status_code=302)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    if not commit_message:
        commit_message = f"Update {path}"

    try:
        await write_file(
            disk_path=repo.disk_path,
            branch=ref,
            path=path,
            content=content.encode("utf-8"),
            message=commit_message,
            author_name=current_user.name or current_user.login,
            author_email=current_user.email or f"{current_user.login}@users.noreply.github-emulator.local",
        )
        return RedirectResponse(
            url=f"/ui/{owner}/{repo_name}/blob/{ref}/{path}",
            status_code=302,
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="edit_file.html",
            context=_ctx(
                request, owner=owner, repo=repo, repo_name=repo.name,
                ref=ref, path=path, content=content,
                current_user=current_user, error=str(exc),
            ),
        )


# ---------------------------------------------------------------------------
# Commits list
# ---------------------------------------------------------------------------

@router.get("/{owner}/{repo_name}/commits/{ref}", response_class=HTMLResponse)
async def commits_list(
    request: Request,
    owner: str,
    repo_name: str,
    ref: str,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Commit history for a branch."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    per_page = 30
    commits = []
    total = 0
    if repo.disk_path and os.path.isdir(repo.disk_path):
        total = await get_commit_count(repo.disk_path, ref)
        commits = await get_log(
            repo.disk_path, ref=ref,
            max_count=per_page, skip=(page - 1) * per_page,
        )

    has_next = (page * per_page) < total

    return templates.TemplateResponse(
        request=request,
        name="commits.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            ref=ref, commits=commits, page=page, has_next=has_next,
            total=total, current_user=current_user,
        ),
    )


# ---------------------------------------------------------------------------
# Single commit detail
# ---------------------------------------------------------------------------

@router.get("/{owner}/{repo_name}/commit/{sha}", response_class=HTMLResponse)
async def commit_detail_view(
    request: Request,
    owner: str,
    repo_name: str,
    sha: str,
    db: AsyncSession = Depends(get_db),
):
    """Single commit detail with diff."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    commit_info = None
    diff_files = []
    if repo.disk_path and os.path.isdir(repo.disk_path):
        commit_info = await get_commit_info(repo.disk_path, sha)
        diff_files = await get_commit_diff(repo.disk_path, sha)

    if commit_info is None:
        return HTMLResponse(content="<h1>404 - Commit Not Found</h1>", status_code=404)

    return templates.TemplateResponse(
        request=request,
        name="commit_detail.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            commit_info=commit_info, diff_files=diff_files,
            current_user=current_user,
        ),
    )


# ---------------------------------------------------------------------------
# Branches list
# ---------------------------------------------------------------------------

@router.get("/{owner}/{repo_name}/branches", response_class=HTMLResponse)
async def branches_list(
    request: Request,
    owner: str,
    repo_name: str,
    db: AsyncSession = Depends(get_db),
):
    """List all branches."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    branches = []
    default_branch = repo.default_branch or "main"
    if repo.disk_path and os.path.isdir(repo.disk_path):
        branches = await get_branches(repo.disk_path)
        # For each branch, fetch latest commit
        for branch in branches:
            log = await get_log(repo.disk_path, ref=branch["name"], max_count=1)
            branch["last_commit"] = log[0] if log else None
        # Sort: default branch first
        branches.sort(key=lambda b: (0 if b["name"] == default_branch else 1, b["name"]))

    return templates.TemplateResponse(
        request=request,
        name="branches.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            branches=branches, default_branch=default_branch,
            current_user=current_user,
        ),
    )


# ---------------------------------------------------------------------------
# Tags list
# ---------------------------------------------------------------------------

@router.get("/{owner}/{repo_name}/tags", response_class=HTMLResponse)
async def tags_list(
    request: Request,
    owner: str,
    repo_name: str,
    db: AsyncSession = Depends(get_db),
):
    """List all tags."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    tags = []
    if repo.disk_path and os.path.isdir(repo.disk_path):
        tags = await get_tags(repo.disk_path)

    return templates.TemplateResponse(
        request=request,
        name="tags.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            tags=tags, current_user=current_user,
        ),
    )


# ---------------------------------------------------------------------------
# Tree (directory) view
# ---------------------------------------------------------------------------

@router.get("/{owner}/{repo_name}/tree/{ref}/{path:path}", response_class=HTMLResponse)
async def tree_view(
    request: Request,
    owner: str,
    repo_name: str,
    ref: str,
    path: str,
    db: AsyncSession = Depends(get_db),
):
    """Directory listing at a given ref and path."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    entries = None
    if repo.disk_path and os.path.isdir(repo.disk_path):
        entries = await list_tree(repo.disk_path, ref, path)
        if entries:
            entries.sort(key=lambda e: (0 if e["type"] == "tree" else 1, e["name"]))

    return templates.TemplateResponse(
        request=request,
        name="tree.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            ref=ref, path=path, entries=entries,
            current_user=current_user,
        ),
    )


# ---------------------------------------------------------------------------
# Blob (file) view
# ---------------------------------------------------------------------------

@router.get("/{owner}/{repo_name}/blob/{ref}/{path:path}", response_class=HTMLResponse)
async def blob_view(
    request: Request,
    owner: str,
    repo_name: str,
    ref: str,
    path: str,
    db: AsyncSession = Depends(get_db),
):
    """File content viewer."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)

    content = None
    if repo.disk_path and os.path.isdir(repo.disk_path):
        raw = await get_file_content(repo.disk_path, ref, path)
        if raw:
            try:
                content = raw.decode("utf-8", errors="replace")
            except Exception:
                content = None

    return templates.TemplateResponse(
        request=request,
        name="blob.html",
        context=_ctx(
            request, owner=owner, repo=repo, repo_name=repo.name,
            ref=ref, path=path, content=content,
            current_user=current_user,
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_repo(
    db: AsyncSession, owner: str, repo_name: str
) -> Optional[Repository]:
    """Look up a repository by owner login and repo name."""
    full_name = f"{owner}/{repo_name}"
    result = await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    return result.scalar_one_or_none()

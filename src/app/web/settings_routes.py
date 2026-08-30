"""Repository-settings web routes."""

import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.git.bare_repo import get_branches, set_default_branch
from app.models.actions import Runner
from app.models.apps import AppInstallation
from app.models.branch import Branch, BranchProtection
from app.models.repository import Collaborator, Repository
from app.models.user import User
from app.services import repo_service
from app.web.routes import (
    _can_manage_repo,
    _ctx,
    _get_current_user,
    _get_repo,
    templates,
)

router = APIRouter()

# ---------------------------------------------------------------------------
# Repository settings
# ---------------------------------------------------------------------------

async def _render_repository_settings(
    request: Request,
    db: AsyncSession,
    owner: str,
    repo_name: str,
    *,
    saved: str | None = None,
    error: str | None = None,
):
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)
    if not _can_manage_repo(repo, current_user):
        return HTMLResponse(content="<h1>403 - Forbidden</h1>", status_code=403)
    branches = []
    if repo.disk_path and os.path.isdir(repo.disk_path):
        branches = await get_branches(repo.disk_path)
    branch_names = [branch["name"] for branch in branches]
    if repo.default_branch and repo.default_branch not in branch_names:
        branch_names.insert(0, repo.default_branch)
    return templates.TemplateResponse(
        request=request,
        name="repo_settings.html",
        context=_ctx(
            request,
            owner=owner,
            repo=repo,
            current_user=current_user,
            branches=branch_names,
            saved=saved,
            error=error,
        ),
    )


@router.get("/{owner}/{repo_name}/settings", response_class=HTMLResponse)
async def repository_settings_page(
    request: Request,
    owner: str,
    repo_name: str,
    saved: str | None = Query(None),
    error: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Show GitHub-like general repository settings."""
    return await _render_repository_settings(
        request,
        db,
        owner,
        repo_name,
        saved=saved,
        error=error,
    )


@router.post("/{owner}/{repo_name}/settings/general")
async def update_repository_general_settings(
    request: Request,
    owner: str,
    repo_name: str,
    description: str = Form(""),
    homepage: str = Form(""),
    visibility: str = Form("public"),
    is_template: bool = Form(False),
    has_issues: bool = Form(False),
    has_projects: bool = Form(False),
    has_wiki: bool = Form(False),
    has_discussions: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    """Persist general repository metadata and feature toggles."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)
    if not _can_manage_repo(repo, current_user):
        return HTMLResponse(content="<h1>403 - Forbidden</h1>", status_code=403)
    if visibility not in {"public", "private"}:
        return RedirectResponse(
            url=f"/ui/{owner}/{repo_name}/settings?error=Invalid%20visibility",
            status_code=302,
        )
    repo.description = description.strip() or None
    repo.homepage = homepage.strip() or None
    repo.private = visibility == "private"
    repo.visibility = visibility
    repo.is_template = is_template
    repo.has_issues = has_issues
    repo.has_projects = has_projects
    repo.has_wiki = has_wiki
    repo.has_discussions = has_discussions
    await db.commit()
    return RedirectResponse(
        url=f"/ui/{owner}/{repo_name}/settings?saved=general",
        status_code=302,
    )


@router.post("/{owner}/{repo_name}/settings/rename")
async def rename_repository_setting(
    request: Request,
    owner: str,
    repo_name: str,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Rename a repository and redirect to its new settings URL."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)
    if not _can_manage_repo(repo, current_user):
        return HTMLResponse(content="<h1>403 - Forbidden</h1>", status_code=403)
    try:
        repo = await repo_service.rename_repo(db, repo, name.strip())
    except ValueError as exc:
        return RedirectResponse(
            url=(
                f"/ui/{owner}/{repo_name}/settings?error="
                f"{quote(str(exc))}"
            ),
            status_code=302,
        )
    return RedirectResponse(
        url=f"/ui/{owner}/{repo.name}/settings?saved=rename",
        status_code=302,
    )


@router.post("/{owner}/{repo_name}/settings/default-branch")
async def update_repository_default_branch(
    request: Request,
    owner: str,
    repo_name: str,
    default_branch: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Update both repository metadata and the bare repository HEAD."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)
    if not _can_manage_repo(repo, current_user):
        return HTMLResponse(content="<h1>403 - Forbidden</h1>", status_code=403)
    branch = default_branch.strip()
    updated = False
    if repo.disk_path and os.path.isdir(repo.disk_path):
        updated = await set_default_branch(repo.disk_path, branch)
    if not updated:
        return RedirectResponse(
            url=(
                f"/ui/{owner}/{repo_name}/settings?error="
                f"{quote(f'Branch {branch!r} does not exist.') }"
            ),
            status_code=302,
        )
    repo.default_branch = branch
    await db.commit()
    return RedirectResponse(
        url=f"/ui/{owner}/{repo_name}/settings?saved=default-branch",
        status_code=302,
    )


async def _repository_settings_branches(
    db: AsyncSession,
    repo: Repository,
) -> list[Branch]:
    result = await db.execute(
        select(Branch).where(Branch.repo_id == repo.id).order_by(Branch.name)
    )
    branches = list(result.scalars().all())
    branches.sort(
        key=lambda branch: (
            0 if branch.name == (repo.default_branch or "main") else 1,
            branch.name,
        )
    )
    return branches


@router.get("/{owner}/{repo_name}/settings/branches", response_class=HTMLResponse)
async def repository_branch_settings_page(
    request: Request,
    owner: str,
    repo_name: str,
    branch: str | None = Query(None),
    saved: str | None = Query(None),
    error: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Show repository branch-protection rules and the selected rule editor."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)
    if not _can_manage_repo(repo, current_user):
        return HTMLResponse(content="<h1>403 - Forbidden</h1>", status_code=403)

    branches = await _repository_settings_branches(db, repo)
    selected_branch = next(
        (item for item in branches if item.name == branch),
        branches[0] if branches else None,
    )
    return templates.TemplateResponse(
        request=request,
        name="repo_settings_branches.html",
        context=_ctx(
            request,
            owner=owner,
            repo=repo,
            current_user=current_user,
            branches=branches,
            selected_branch=selected_branch,
            saved=saved,
            error=error,
        ),
    )


@router.post("/{owner}/{repo_name}/settings/branches/protection")
async def update_repository_branch_protection(
    request: Request,
    owner: str,
    repo_name: str,
    branch_name: str = Form(...),
    protection_enabled: bool = Form(False),
    require_status_checks: bool = Form(False),
    status_contexts: str = Form(""),
    strict_status_checks: bool = Form(False),
    require_reviews: bool = Form(False),
    required_approving_review_count: int = Form(1),
    dismiss_stale_reviews: bool = Form(False),
    require_last_push_approval: bool = Form(False),
    enforce_admins: bool = Form(False),
    required_linear_history: bool = Form(False),
    allow_force_pushes: bool = Form(False),
    allow_deletions: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    """Create, update, or remove an exact-branch protection rule."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)
    if not _can_manage_repo(repo, current_user):
        return HTMLResponse(content="<h1>403 - Forbidden</h1>", status_code=403)

    branch_name = branch_name.strip()
    result = await db.execute(
        select(Branch).where(
            Branch.repo_id == repo.id,
            Branch.name == branch_name,
        )
    )
    branch = result.scalar_one_or_none()
    redirect_base = f"/ui/{owner}/{repo_name}/settings/branches"
    if branch is None:
        return RedirectResponse(
            url=f"{redirect_base}?error={quote('Branch not found')}",
            status_code=302,
        )
    if not 0 <= required_approving_review_count <= 6:
        return RedirectResponse(
            url=(
                f"{redirect_base}?branch={quote(branch_name)}&error="
                f"{quote('Required approvals must be between 0 and 6') }"
            ),
            status_code=302,
        )

    if not protection_enabled:
        if branch.protection is not None:
            await db.delete(branch.protection)
        branch.protected = False
    else:
        protection = branch.protection
        if protection is None:
            protection = BranchProtection(branch_id=branch.id)
            db.add(protection)

        contexts = [
            value.strip()
            for value in status_contexts.replace(",", "\n").splitlines()
            if value.strip()
        ]
        protection.required_status_checks = (
            {
                "strict": strict_status_checks,
                "contexts": list(dict.fromkeys(contexts)),
                "checks": [],
            }
            if require_status_checks
            else None
        )
        protection.required_pull_request_reviews = (
            {
                "dismissal_restrictions": {"users": [], "teams": [], "apps": []},
                "dismiss_stale_reviews": dismiss_stale_reviews,
                "require_code_owner_reviews": False,
                "required_approving_review_count": required_approving_review_count,
                "require_last_push_approval": require_last_push_approval,
                "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
            }
            if require_reviews
            else None
        )
        protection.enforce_admins = enforce_admins
        protection.restrictions = None
        protection.required_linear_history = required_linear_history
        protection.allow_force_pushes = allow_force_pushes
        protection.allow_deletions = allow_deletions
        protection.block_creations = False
        protection.lock_branch = False
        protection.allow_fork_syncing = False
        branch.protected = True

    await db.commit()
    from app.services.merge_readiness_service import reevaluate_auto_merges

    await reevaluate_auto_merges(db, repo.id)
    return RedirectResponse(
        url=(
            f"{redirect_base}?branch={quote(branch_name)}&saved="
            f"{'updated' if protection_enabled else 'removed'}"
        ),
        status_code=302,
    )


@router.get(
    "/{owner}/{repo_name}/settings/actions/runners",
    response_class=HTMLResponse,
)
async def repository_actions_runner_settings_page(
    request: Request,
    owner: str,
    repo_name: str,
    db: AsyncSession = Depends(get_db),
):
    """List repository Actions runners within the persistent settings shell."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)
    if not _can_manage_repo(repo, current_user):
        return HTMLResponse(content="<h1>403 - Forbidden</h1>", status_code=403)

    runners = list((await db.execute(
        select(Runner)
        .where(Runner.repo_id == repo.id)
        .order_by(Runner.name)
    )).scalars().all())
    return templates.TemplateResponse(
        request=request,
        name="repo_settings_runners.html",
        context=_ctx(
            request,
            owner=owner,
            repo=repo,
            current_user=current_user,
            runners=runners,
        ),
    )


_COLLABORATOR_PERMISSIONS = ("pull", "triage", "push", "maintain", "admin")


async def _repository_collaborators(
    db: AsyncSession,
    repo: Repository,
) -> list[Collaborator]:
    result = await db.execute(
        select(Collaborator)
        .join(User, Collaborator.user_id == User.id)
        .where(Collaborator.repo_id == repo.id)
        .order_by(User.login)
    )
    return list(result.scalars().all())


@router.get("/{owner}/{repo_name}/settings/access", response_class=HTMLResponse)
async def repository_collaborator_settings_page(
    request: Request,
    owner: str,
    repo_name: str,
    saved: str | None = Query(None),
    error: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Manage direct repository collaborators within the settings shell."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)
    if not _can_manage_repo(repo, current_user):
        return HTMLResponse(content="<h1>403 - Forbidden</h1>", status_code=403)
    collaborators = await _repository_collaborators(db, repo)
    return templates.TemplateResponse(
        request=request,
        name="repo_settings_collaborators.html",
        context=_ctx(
            request,
            owner=owner,
            repo=repo,
            current_user=current_user,
            collaborators=collaborators,
            permissions=_COLLABORATOR_PERMISSIONS,
            saved=saved,
            error=error,
        ),
    )


def _collaborator_settings_redirect(
    owner: str,
    repo_name: str,
    *,
    saved: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    url = f"/ui/{owner}/{repo_name}/settings/access"
    if saved:
        url += f"?saved={quote(saved)}"
    elif error:
        url += f"?error={quote(error)}"
    return RedirectResponse(url=url, status_code=302)


@router.post("/{owner}/{repo_name}/settings/access/collaborators")
async def add_repository_collaborator_setting(
    request: Request,
    owner: str,
    repo_name: str,
    username: str = Form(...),
    permission: str = Form("push"),
    db: AsyncSession = Depends(get_db),
):
    """Add a direct collaborator or update an existing collaborator."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)
    if not _can_manage_repo(repo, current_user):
        return HTMLResponse(content="<h1>403 - Forbidden</h1>", status_code=403)
    if permission not in _COLLABORATOR_PERMISSIONS:
        return _collaborator_settings_redirect(
            owner, repo_name, error="Invalid collaborator permission"
        )

    login = username.strip()
    target = (
        await db.execute(select(User).where(User.login == login))
    ).scalar_one_or_none()
    if target is None:
        return _collaborator_settings_redirect(
            owner, repo_name, error=f"User {login!r} was not found"
        )
    if target.id == repo.owner_id:
        return _collaborator_settings_redirect(
            owner, repo_name, error="The repository owner already has admin access"
        )

    collaborator = (
        await db.execute(
            select(Collaborator).where(
                Collaborator.repo_id == repo.id,
                Collaborator.user_id == target.id,
            )
        )
    ).scalar_one_or_none()
    if collaborator is None:
        collaborator = Collaborator(
            repo_id=repo.id,
            user_id=target.id,
            permission=permission,
        )
        db.add(collaborator)
    else:
        collaborator.permission = permission
    await db.commit()
    return _collaborator_settings_redirect(owner, repo_name, saved="collaborator")


@router.post(
    "/{owner}/{repo_name}/settings/access/collaborators/{username}/permission"
)
async def update_repository_collaborator_permission_setting(
    request: Request,
    owner: str,
    repo_name: str,
    username: str,
    permission: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Change a direct collaborator's repository permission."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)
    if not _can_manage_repo(repo, current_user):
        return HTMLResponse(content="<h1>403 - Forbidden</h1>", status_code=403)
    if permission not in _COLLABORATOR_PERMISSIONS:
        return _collaborator_settings_redirect(
            owner, repo_name, error="Invalid collaborator permission"
        )

    collaborator = (
        await db.execute(
            select(Collaborator)
            .join(User, Collaborator.user_id == User.id)
            .where(
                Collaborator.repo_id == repo.id,
                User.login == username,
            )
        )
    ).scalar_one_or_none()
    if collaborator is None:
        return _collaborator_settings_redirect(
            owner, repo_name, error="Collaborator not found"
        )
    collaborator.permission = permission
    await db.commit()
    return _collaborator_settings_redirect(owner, repo_name, saved="permission")


@router.post(
    "/{owner}/{repo_name}/settings/access/collaborators/{username}/remove"
)
async def remove_repository_collaborator_setting(
    request: Request,
    owner: str,
    repo_name: str,
    username: str,
    db: AsyncSession = Depends(get_db),
):
    """Remove a direct collaborator from a repository."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)
    if not _can_manage_repo(repo, current_user):
        return HTMLResponse(content="<h1>403 - Forbidden</h1>", status_code=403)

    collaborator = (
        await db.execute(
            select(Collaborator)
            .join(User, Collaborator.user_id == User.id)
            .where(
                Collaborator.repo_id == repo.id,
                User.login == username,
            )
        )
    ).scalar_one_or_none()
    if collaborator is None:
        return _collaborator_settings_redirect(
            owner, repo_name, error="Collaborator not found"
        )
    await db.delete(collaborator)
    await db.commit()
    return _collaborator_settings_redirect(owner, repo_name, saved="removed")


@router.get(
    "/{owner}/{repo_name}/settings/installations",
    response_class=HTMLResponse,
)
async def repository_github_apps_settings_page(
    request: Request,
    owner: str,
    repo_name: str,
    db: AsyncSession = Depends(get_db),
):
    """List GitHub App installations that grant access to this repository."""
    current_user = await _get_current_user(request, db)
    repo = await _get_repo(db, owner, repo_name)
    if repo is None:
        return HTMLResponse(content="<h1>404 - Not Found</h1>", status_code=404)
    if not _can_manage_repo(repo, current_user):
        return HTMLResponse(content="<h1>403 - Forbidden</h1>", status_code=403)

    all_installations = list((await db.execute(
        select(AppInstallation).order_by(AppInstallation.created_at, AppInstallation.id)
    )).scalars().all())
    installations = [
        installation
        for installation in all_installations
        if repo.full_name in (installation.repositories or [])
    ]
    return templates.TemplateResponse(
        request=request,
        name="repo_settings_apps.html",
        context=_ctx(
            request,
            owner=owner,
            repo=repo,
            current_user=current_user,
            installations=installations,
        ),
    )




__all__ = ["router"]

"""Branch and branch-protection endpoints."""

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.middleware.error_handler import ValidationError
from app.models.branch import Branch, BranchProtection
from app.models.repository import Collaborator, Repository

router = APIRouter(tags=["branches"])

BASE = settings.BASE_URL


def _enabled(value: bool) -> dict:
    return {"enabled": bool(value)}


def _status_checks_json(protection: BranchProtection, api_url: str) -> dict | None:
    value = protection.required_status_checks
    if value is None:
        return None
    return {
        "url": f"{api_url}/required_status_checks",
        "strict": bool(value.get("strict", False)),
        "contexts": list(value.get("contexts", [])),
        "checks": list(value.get("checks", [])),
        "contexts_url": f"{api_url}/required_status_checks/contexts",
    }


def _reviews_json(protection: BranchProtection, api_url: str) -> dict | None:
    value = protection.required_pull_request_reviews
    if value is None:
        return None
    return {
        "url": f"{api_url}/required_pull_request_reviews",
        "dismissal_restrictions": value.get(
            "dismissal_restrictions", {"users": [], "teams": [], "apps": []}
        ),
        "dismiss_stale_reviews": bool(value.get("dismiss_stale_reviews", False)),
        "require_code_owner_reviews": False,
        "required_approving_review_count": int(
            value.get("required_approving_review_count", 1)
        ),
        "require_last_push_approval": bool(
            value.get("require_last_push_approval", False)
        ),
        "bypass_pull_request_allowances": value.get(
            "bypass_pull_request_allowances", {"users": [], "teams": [], "apps": []}
        ),
    }


def _protection_json(
    protection: BranchProtection, owner: str, repo: str, branch: str
) -> dict:
    api_url = f"{BASE}/api/v3/repos/{owner}/{repo}/branches/{branch}/protection"
    return {
        "url": api_url,
        "required_status_checks": _status_checks_json(protection, api_url),
        "required_pull_request_reviews": _reviews_json(protection, api_url),
        "enforce_admins": {
            "url": f"{api_url}/enforce_admins",
            "enabled": bool(protection.enforce_admins),
        },
        "restrictions": protection.restrictions,
        "required_linear_history": _enabled(protection.required_linear_history),
        "allow_force_pushes": _enabled(protection.allow_force_pushes),
        "allow_deletions": _enabled(protection.allow_deletions),
        "block_creations": _enabled(protection.block_creations),
        "required_conversation_resolution": _enabled(False),
        "lock_branch": _enabled(protection.lock_branch),
        "allow_fork_syncing": _enabled(protection.allow_fork_syncing),
        "required_signatures": _enabled(False),
    }


def _branch_json(branch: Branch, owner: str, repo_name: str, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    status_checks = None
    if branch.protection is not None:
        status_checks = branch.protection.required_status_checks
    return {
        "name": branch.name,
        "commit": {
            "sha": branch.sha,
            "url": f"{api}/repos/{owner}/{repo_name}/commits/{branch.sha}",
        },
        "protected": branch.protected,
        "protection": {
            "enabled": branch.protected,
            "required_status_checks": {
                "enforcement_level": "non_admins" if branch.protected else "off",
                "contexts": list((status_checks or {}).get("contexts", [])),
                "checks": list((status_checks or {}).get("checks", [])),
            },
        },
        "protection_url": f"{api}/repos/{owner}/{repo_name}/branches/{branch.name}/protection",
    }


async def _get_branch(repository: Repository, branch: str, db) -> Branch:
    result = await db.execute(
        select(Branch).where(
            Branch.repo_id == repository.id,
            Branch.name == branch,
        )
    )
    value = result.scalar_one_or_none()
    if value is None:
        raise HTTPException(status_code=404, detail="Branch not found")
    return value


async def _require_repository_admin(repository: Repository, user, db) -> None:
    if user.site_admin or repository.owner_id == user.id:
        return
    result = await db.execute(
        select(Collaborator).where(
            Collaborator.repo_id == repository.id,
            Collaborator.user_id == user.id,
            Collaborator.permission == "admin",
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="Must have admin rights to Repository.")


def _unsupported(message: str) -> None:
    raise ValidationError(message=f"Unsupported branch protection rule: {message}")


def _normalize_status_checks(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="required_status_checks must be an object or null")
    contexts = value.get("contexts", [])
    checks = value.get("checks", [])
    if not isinstance(contexts, list) or not all(isinstance(item, str) for item in contexts):
        raise HTTPException(status_code=422, detail="required_status_checks.contexts must be a list of strings")
    if not isinstance(checks, list) or not all(
        isinstance(item, dict) and isinstance(item.get("context"), str)
        for item in checks
    ):
        raise HTTPException(status_code=422, detail="required_status_checks.checks must contain context objects")
    if any(item.get("app_id") is not None for item in checks):
        _unsupported("status-check app pinning")
    normalized_checks = [
        {
            "context": item["context"],
            **({"app_id": item["app_id"]} if item.get("app_id") is not None else {}),
        }
        for item in checks
    ]
    return {
        "strict": bool(value.get("strict", False)),
        "contexts": list(dict.fromkeys(contexts)),
        "checks": normalized_checks,
    }


def _actor_list_is_nonempty(value) -> bool:
    return isinstance(value, dict) and any(value.get(key) for key in ("users", "teams", "apps"))


def _normalize_reviews(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=422,
            detail="required_pull_request_reviews must be an object or null",
        )
    if value.get("require_code_owner_reviews"):
        _unsupported("code-owner reviews")
    if _actor_list_is_nonempty(value.get("dismissal_restrictions")):
        _unsupported("review dismissal actor restrictions")
    if _actor_list_is_nonempty(value.get("bypass_pull_request_allowances")):
        _unsupported("pull-request bypass actors")
    count = value.get("required_approving_review_count", 1)
    if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 6:
        raise HTTPException(
            status_code=422,
            detail="required_approving_review_count must be between 0 and 6",
        )
    return {
        "dismissal_restrictions": {"users": [], "teams": [], "apps": []},
        "dismiss_stale_reviews": bool(value.get("dismiss_stale_reviews", False)),
        "require_code_owner_reviews": False,
        "required_approving_review_count": count,
        "require_last_push_approval": bool(value.get("require_last_push_approval", False)),
        "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
    }


def _bool_setting(body: dict, name: str, default: bool = False) -> bool:
    value = body.get(name, default)
    if isinstance(value, dict):
        value = value.get("enabled", default)
    if not isinstance(value, bool):
        raise HTTPException(status_code=422, detail=f"{name} must be a boolean")
    return value


def _validate_protection_body(body: dict) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Body must be an object")
    if body.get("required_conversation_resolution"):
        _unsupported("required conversation resolution")
    if body.get("required_signatures"):
        _unsupported("required signed commits")
    if body.get("required_deployments"):
        _unsupported("required deployments")
    if body.get("required_merge_queue"):
        _unsupported("merge queues")
    if body.get("restrictions") is not None:
        _unsupported("push actor restrictions")
    if _bool_setting(body, "block_creations"):
        _unsupported("blocking branch creation")
    if _bool_setting(body, "lock_branch"):
        _unsupported("locking branches")
    if _bool_setting(body, "allow_fork_syncing"):
        _unsupported("fork branch syncing")
    return {
        "required_status_checks": _normalize_status_checks(body.get("required_status_checks")),
        "required_pull_request_reviews": _normalize_reviews(
            body.get("required_pull_request_reviews")
        ),
        "enforce_admins": _bool_setting(body, "enforce_admins"),
        "restrictions": None,
        "required_linear_history": _bool_setting(body, "required_linear_history"),
        "allow_force_pushes": _bool_setting(body, "allow_force_pushes"),
        "allow_deletions": _bool_setting(body, "allow_deletions"),
        "block_creations": False,
        "lock_branch": False,
        "allow_fork_syncing": False,
    }


async def _reevaluate(repository: Repository, db) -> None:
    from app.services.merge_readiness_service import reevaluate_auto_merges

    await reevaluate_auto_merges(db, repository.id)


@router.get("/repos/{owner}/{repo}/branches")
async def list_branches(
    owner: str,
    repo: str,
    db: DbSession,
    current_user: CurrentUser,
    protected: bool | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    repository = await get_repo_or_404(owner, repo, db)
    query = select(Branch).where(Branch.repo_id == repository.id)
    if protected is not None:
        query = query.where(Branch.protected == protected)
    query = query.order_by(Branch.name).offset((page - 1) * per_page).limit(per_page)
    branches = (await db.execute(query)).scalars().all()
    return [_branch_json(branch, owner, repo, BASE) for branch in branches]


@router.get("/repos/{owner}/{repo}/branches/{branch:path}/protection")
async def get_branch_protection(
    owner: str, repo: str, branch: str, db: DbSession, current_user: CurrentUser
):
    repository = await get_repo_or_404(owner, repo, db)
    value = await _get_branch(repository, branch, db)
    if not value.protected or value.protection is None:
        raise HTTPException(status_code=404, detail="Branch not protected")
    return _protection_json(value.protection, owner, repo, branch)


@router.put("/repos/{owner}/{repo}/branches/{branch:path}/protection")
async def update_branch_protection(
    owner: str,
    repo: str,
    branch: str,
    body: dict,
    user: AuthUser,
    db: DbSession,
):
    repository = await get_repo_or_404(owner, repo, db)
    await _require_repository_admin(repository, user, db)
    if any(character in branch for character in "*?[]"):
        _unsupported("branch patterns")
    value = await _get_branch(repository, branch, db)
    settings_values = _validate_protection_body(body)
    protection = value.protection
    if protection is None:
        protection = BranchProtection(branch_id=value.id)
        db.add(protection)
    for name, setting in settings_values.items():
        setattr(protection, name, setting)
    value.protected = True
    await db.commit()
    await db.refresh(protection)
    await _reevaluate(repository, db)
    return _protection_json(protection, owner, repo, branch)


@router.delete("/repos/{owner}/{repo}/branches/{branch:path}/protection", status_code=204)
async def delete_branch_protection(
    owner: str, repo: str, branch: str, user: AuthUser, db: DbSession
):
    repository = await get_repo_or_404(owner, repo, db)
    await _require_repository_admin(repository, user, db)
    value = await _get_branch(repository, branch, db)
    if value.protection is None:
        raise HTTPException(status_code=404, detail="Branch not protected")
    await db.delete(value.protection)
    value.protected = False
    await db.commit()
    await _reevaluate(repository, db)
    return Response(status_code=204)


@router.get("/repos/{owner}/{repo}/branches/{branch:path}/protection/required_status_checks")
async def get_required_status_checks(
    owner: str, repo: str, branch: str, db: DbSession, current_user: CurrentUser
):
    repository = await get_repo_or_404(owner, repo, db)
    value = await _get_branch(repository, branch, db)
    if value.protection is None or value.protection.required_status_checks is None:
        raise HTTPException(status_code=404, detail="Required status checks not enabled")
    api_url = f"{BASE}/api/v3/repos/{owner}/{repo}/branches/{branch}/protection"
    return _status_checks_json(value.protection, api_url)


@router.patch("/repos/{owner}/{repo}/branches/{branch:path}/protection/required_status_checks")
async def update_required_status_checks(
    owner: str, repo: str, branch: str, body: dict, user: AuthUser, db: DbSession
):
    repository = await get_repo_or_404(owner, repo, db)
    await _require_repository_admin(repository, user, db)
    value = await _get_branch(repository, branch, db)
    if value.protection is None:
        raise HTTPException(status_code=404, detail="Branch not protected")
    value.protection.required_status_checks = _normalize_status_checks(body)
    await db.commit()
    await db.refresh(value.protection)
    await _reevaluate(repository, db)
    api_url = f"{BASE}/api/v3/repos/{owner}/{repo}/branches/{branch}/protection"
    return _status_checks_json(value.protection, api_url)


@router.delete(
    "/repos/{owner}/{repo}/branches/{branch:path}/protection/required_status_checks",
    status_code=204,
)
async def delete_required_status_checks(
    owner: str, repo: str, branch: str, user: AuthUser, db: DbSession
):
    repository = await get_repo_or_404(owner, repo, db)
    await _require_repository_admin(repository, user, db)
    value = await _get_branch(repository, branch, db)
    if value.protection is None or value.protection.required_status_checks is None:
        raise HTTPException(status_code=404, detail="Required status checks not enabled")
    value.protection.required_status_checks = None
    await db.commit()
    await _reevaluate(repository, db)
    return Response(status_code=204)


@router.get(
    "/repos/{owner}/{repo}/branches/{branch:path}/protection/required_pull_request_reviews"
)
async def get_required_pull_request_reviews(
    owner: str, repo: str, branch: str, db: DbSession, current_user: CurrentUser
):
    repository = await get_repo_or_404(owner, repo, db)
    value = await _get_branch(repository, branch, db)
    if value.protection is None or value.protection.required_pull_request_reviews is None:
        raise HTTPException(status_code=404, detail="Required reviews not enabled")
    api_url = f"{BASE}/api/v3/repos/{owner}/{repo}/branches/{branch}/protection"
    return _reviews_json(value.protection, api_url)


@router.patch(
    "/repos/{owner}/{repo}/branches/{branch:path}/protection/required_pull_request_reviews"
)
async def update_required_pull_request_reviews(
    owner: str, repo: str, branch: str, body: dict, user: AuthUser, db: DbSession
):
    repository = await get_repo_or_404(owner, repo, db)
    await _require_repository_admin(repository, user, db)
    value = await _get_branch(repository, branch, db)
    if value.protection is None:
        raise HTTPException(status_code=404, detail="Branch not protected")
    value.protection.required_pull_request_reviews = _normalize_reviews(body)
    await db.commit()
    await db.refresh(value.protection)
    await _reevaluate(repository, db)
    api_url = f"{BASE}/api/v3/repos/{owner}/{repo}/branches/{branch}/protection"
    return _reviews_json(value.protection, api_url)


@router.delete(
    "/repos/{owner}/{repo}/branches/{branch:path}/protection/required_pull_request_reviews",
    status_code=204,
)
async def delete_required_pull_request_reviews(
    owner: str, repo: str, branch: str, user: AuthUser, db: DbSession
):
    repository = await get_repo_or_404(owner, repo, db)
    await _require_repository_admin(repository, user, db)
    value = await _get_branch(repository, branch, db)
    if value.protection is None or value.protection.required_pull_request_reviews is None:
        raise HTTPException(status_code=404, detail="Required reviews not enabled")
    value.protection.required_pull_request_reviews = None
    await db.commit()
    await _reevaluate(repository, db)
    return Response(status_code=204)


@router.get("/repos/{owner}/{repo}/branches/{branch:path}/protection/enforce_admins")
async def get_admin_enforcement(
    owner: str, repo: str, branch: str, db: DbSession, current_user: CurrentUser
):
    repository = await get_repo_or_404(owner, repo, db)
    value = await _get_branch(repository, branch, db)
    if value.protection is None:
        raise HTTPException(status_code=404, detail="Branch not protected")
    api_url = f"{BASE}/api/v3/repos/{owner}/{repo}/branches/{branch}/protection"
    return {
        "url": f"{api_url}/enforce_admins",
        "enabled": bool(value.protection.enforce_admins),
    }


@router.post("/repos/{owner}/{repo}/branches/{branch:path}/protection/enforce_admins")
async def enable_admin_enforcement(
    owner: str, repo: str, branch: str, user: AuthUser, db: DbSession
):
    repository = await get_repo_or_404(owner, repo, db)
    await _require_repository_admin(repository, user, db)
    value = await _get_branch(repository, branch, db)
    if value.protection is None:
        raise HTTPException(status_code=404, detail="Branch not protected")
    value.protection.enforce_admins = True
    await db.commit()
    await _reevaluate(repository, db)
    api_url = f"{BASE}/api/v3/repos/{owner}/{repo}/branches/{branch}/protection"
    return {"url": f"{api_url}/enforce_admins", "enabled": True}


@router.delete(
    "/repos/{owner}/{repo}/branches/{branch:path}/protection/enforce_admins",
    status_code=204,
)
async def disable_admin_enforcement(
    owner: str, repo: str, branch: str, user: AuthUser, db: DbSession
):
    repository = await get_repo_or_404(owner, repo, db)
    await _require_repository_admin(repository, user, db)
    value = await _get_branch(repository, branch, db)
    if value.protection is None:
        raise HTTPException(status_code=404, detail="Branch not protected")
    value.protection.enforce_admins = False
    await db.commit()
    await _reevaluate(repository, db)
    return Response(status_code=204)


@router.get("/repos/{owner}/{repo}/branches/{branch:path}")
async def get_branch(
    owner: str, repo: str, branch: str, db: DbSession, current_user: CurrentUser
):
    repository = await get_repo_or_404(owner, repo, db)
    value = await _get_branch(repository, branch, db)
    return _branch_json(value, owner, repo, BASE)

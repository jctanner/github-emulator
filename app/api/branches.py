"""Branch endpoints -- list, get, and branch protection."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.branch import Branch, BranchProtection
from app.schemas.user import _make_node_id

router = APIRouter(tags=["branches"])

BASE = settings.BASE_URL


def _branch_json(branch: Branch, owner: str, repo_name: str, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
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
                "enforcement_level": "off",
                "contexts": [],
                "checks": [],
            },
        },
        "protection_url": f"{api}/repos/{owner}/{repo_name}/branches/{branch.name}/protection",
    }


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
    """List branches for a repository."""
    repository = await get_repo_or_404(owner, repo, db)

    query = select(Branch).where(Branch.repo_id == repository.id)
    if protected is not None:
        query = query.where(Branch.protected == protected)

    query = query.order_by(Branch.name).offset((page - 1) * per_page).limit(per_page)
    branches = (await db.execute(query)).scalars().all()

    return [_branch_json(b, owner, repo, BASE) for b in branches]


@router.get("/repos/{owner}/{repo}/branches/{branch}")
async def get_branch(
    owner: str, repo: str, branch: str, db: DbSession, current_user: CurrentUser
):
    """Get a single branch."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(Branch).where(
            Branch.repo_id == repository.id, Branch.name == branch
        )
    )
    b = result.scalar_one_or_none()
    if b is None:
        raise HTTPException(status_code=404, detail="Branch not found")

    return _branch_json(b, owner, repo, BASE)


@router.get("/repos/{owner}/{repo}/branches/{branch}/protection")
async def get_branch_protection(
    owner: str, repo: str, branch: str, db: DbSession, current_user: CurrentUser
):
    """Get branch protection settings."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(Branch).where(
            Branch.repo_id == repository.id, Branch.name == branch
        )
    )
    b = result.scalar_one_or_none()
    if b is None:
        raise HTTPException(status_code=404, detail="Branch not found")

    if not b.protected or b.protection is None:
        raise HTTPException(status_code=404, detail="Branch not protected")

    prot = b.protection
    api = f"{BASE}/api/v3"
    return {
        "url": f"{api}/repos/{owner}/{repo}/branches/{branch}/protection",
        "required_status_checks": prot.required_status_checks,
        "enforce_admins": {
            "url": f"{api}/repos/{owner}/{repo}/branches/{branch}/protection/enforce_admins",
            "enabled": prot.enforce_admins,
        },
        "required_pull_request_reviews": prot.required_pull_request_reviews,
        "restrictions": prot.restrictions,
    }

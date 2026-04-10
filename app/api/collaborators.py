"""Collaborator endpoints -- list, check, add, remove, permissions."""

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.repository import Collaborator
from app.models.user import User
from app.schemas.user import SimpleUser, _make_node_id

router = APIRouter(tags=["collaborators"])

BASE = settings.BASE_URL


def _collab_json(user_obj: User, permission: str, base_url: str) -> dict:
    simple = SimpleUser.from_db(user_obj, base_url).model_dump()
    perm_map = {
        "admin": {"admin": True, "maintain": True, "push": True, "triage": True, "pull": True},
        "maintain": {"admin": False, "maintain": True, "push": True, "triage": True, "pull": True},
        "push": {"admin": False, "maintain": False, "push": True, "triage": True, "pull": True},
        "triage": {"admin": False, "maintain": False, "push": False, "triage": True, "pull": True},
        "pull": {"admin": False, "maintain": False, "push": False, "triage": False, "pull": True},
    }
    simple["permissions"] = perm_map.get(permission, perm_map["pull"])
    simple["role_name"] = permission
    return simple


@router.get("/repos/{owner}/{repo}/collaborators")
async def list_collaborators(
    owner: str, repo: str, db: DbSession, user: AuthUser,
):
    """List collaborators for a repository."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Collaborator).where(Collaborator.repo_id == repository.id)
    )
    collabs = result.scalars().all()

    # Include the owner
    owner_user = repository.owner
    items = []
    if owner_user:
        items.append(_collab_json(owner_user, "admin", BASE))
    for c in collabs:
        if c.user:
            items.append(_collab_json(c.user, c.permission, BASE))
    return items


@router.get("/repos/{owner}/{repo}/collaborators/{username}")
async def check_collaborator(
    owner: str, repo: str, username: str, db: DbSession, user: AuthUser,
):
    """Check if a user is a collaborator (204 = yes, 404 = no)."""
    repository = await get_repo_or_404(owner, repo, db)

    # Owner is always a collaborator
    if repository.owner and repository.owner.login == username:
        return Response(status_code=204)

    result = await db.execute(
        select(Collaborator)
        .join(User, Collaborator.user_id == User.id)
        .where(Collaborator.repo_id == repository.id, User.login == username)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return Response(status_code=204)


@router.put("/repos/{owner}/{repo}/collaborators/{username}", status_code=201)
async def add_collaborator(
    owner: str, repo: str, username: str, body: dict, user: AuthUser, db: DbSession,
):
    """Add a collaborator to a repository."""
    repository = await get_repo_or_404(owner, repo, db)
    permission = body.get("permission", "push")

    result = await db.execute(select(User).where(User.login == username))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already a collaborator
    existing = await db.execute(
        select(Collaborator).where(
            Collaborator.repo_id == repository.id,
            Collaborator.user_id == target_user.id,
        )
    )
    collab = existing.scalar_one_or_none()
    if collab:
        collab.permission = permission
    else:
        collab = Collaborator(
            repo_id=repository.id,
            user_id=target_user.id,
            permission=permission,
        )
        db.add(collab)

    await db.commit()
    return {"message": "Invitation created"}


@router.delete("/repos/{owner}/{repo}/collaborators/{username}", status_code=204)
async def remove_collaborator(
    owner: str, repo: str, username: str, user: AuthUser, db: DbSession,
):
    """Remove a collaborator."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(Collaborator)
        .join(User, Collaborator.user_id == User.id)
        .where(Collaborator.repo_id == repository.id, User.login == username)
    )
    collab = result.scalar_one_or_none()
    if collab is None:
        raise HTTPException(status_code=404, detail="Not Found")

    await db.delete(collab)
    await db.commit()


@router.get("/repos/{owner}/{repo}/collaborators/{username}/permission")
async def get_collaborator_permission(
    owner: str, repo: str, username: str, db: DbSession, user: AuthUser,
):
    """Get a collaborator's permission level."""
    repository = await get_repo_or_404(owner, repo, db)

    # Check owner
    if repository.owner and repository.owner.login == username:
        return {
            "permission": "admin",
            "role_name": "admin",
            "user": SimpleUser.from_db(repository.owner, BASE).model_dump(),
        }

    result = await db.execute(
        select(Collaborator)
        .join(User, Collaborator.user_id == User.id)
        .where(Collaborator.repo_id == repository.id, User.login == username)
    )
    collab = result.scalar_one_or_none()
    if collab is None:
        raise HTTPException(status_code=404, detail="Not Found")

    return {
        "permission": collab.permission,
        "role_name": collab.permission,
        "user": SimpleUser.from_db(collab.user, BASE).model_dump(),
    }

"""Collaborator endpoints -- list, check, add, remove, permissions."""

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.repository import Collaborator, Repository
from app.models.user import User
from app.schemas.user import SimpleUser, _make_node_id
from app.schemas.settings import CollaboratorResponse

router = APIRouter(tags=["collaborators"])

BASE = settings.BASE_URL
VALID_PERMISSIONS = {"pull", "triage", "push", "maintain", "admin"}


async def _require_repository_admin(
    repository: Repository, user: User, db
) -> None:
    from app.services.repository_access import is_repository_admin

    if not await is_repository_admin(db, repository, user):
        raise HTTPException(
            status_code=403,
            detail="Must have admin rights to Repository.",
        )


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


@router.get(
    "/repos/{owner}/{repo}/collaborators",
    response_model=list[CollaboratorResponse],
)
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
    await _require_repository_admin(repository, user, db)
    permission = body.get("permission", "push")
    if permission not in VALID_PERMISSIONS:
        raise HTTPException(status_code=422, detail="Invalid collaborator permission")

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
    await _require_repository_admin(repository, user, db)

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


# GitHub answers this endpoint in two vocabularies: ``permission`` is the
# legacy one (admin, write, read or none; maintain reads as write and triage
# as read) and ``role_name`` names the role itself. Clients such as Fullsend's
# dispatch read ``role_name`` and treat anything outside GitHub's names as no
# access, so the emulator's internal "push"/"pull" must not leak through.
_ROLE_NAME = {"admin": "admin", "maintain": "maintain", "push": "write", "triage": "triage", "pull": "read"}
_LEGACY_PERMISSION = {"admin": "admin", "maintain": "write", "push": "write", "triage": "read", "pull": "read"}


def _permission_json(user_obj: User, permission: str) -> dict:
    user_json = _collab_json(user_obj, permission, BASE)
    user_json["role_name"] = _ROLE_NAME[permission]
    return {
        "permission": _LEGACY_PERMISSION[permission],
        "role_name": _ROLE_NAME[permission],
        "user": user_json,
    }


@router.get("/repos/{owner}/{repo}/collaborators/{username}/permission")
async def get_collaborator_permission(
    owner: str, repo: str, username: str, db: DbSession, user: AuthUser,
):
    """A user's permission on the repository.

    For a person it is ``repository_access.repository_permission``: the
    owner of a user-owned repository, a collaborator, or a member of the
    owning organization through an organization role (or as an owner). A
    GitHub App's bot is not a collaborator; its access is what the App's
    installation grants, and GitHub answers this endpoint with it. Anyone
    else is 404.
    """
    from app.services.repository_access import installation_role, repository_permission

    repository = await get_repo_or_404(owner, repo, db)
    target = (await db.execute(select(User).where(User.login == username))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Not Found")

    permission = await repository_permission(db, repository, target)
    if permission is None:
        installed = await installation_role(db, repository, target)
        permission = {"write": "push", "read": "pull"}.get(installed or "")
    if permission is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _permission_json(target, permission)

"""GitHub's predefined organization roles, and what they grant.

An organization role assigned to a member grants a permission on every
repository the organization owns, including repositories created after the
assignment. That is how a fleet of throwaway repositories gives the same
people access without a collaborator grant per repository, which would be
lost each time a repository is deleted and recreated (Fullsend's behaviour
tests rely on exactly that).

Only the five predefined all-repository roles exist here; custom roles and
team assignments are not emulated. The ids are fixed so a client may cache
them, and the names are GitHub's, so a client that resolves a role by name
("all_repo_write") finds it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Repository permissions from lowest to highest, in the collaborator API's
# vocabulary ("push" is GitHub's REST name for write).
PERMISSION_ORDER = ("pull", "triage", "push", "maintain", "admin")

PREDEFINED_ROLES = (
    {"id": 8132, "name": "all_repo_read", "base_role": "read", "permission": "pull",
     "description": "Grants read access to all repositories in the organization."},
    {"id": 8133, "name": "all_repo_triage", "base_role": "triage", "permission": "triage",
     "description": "Grants triage access to all repositories in the organization."},
    {"id": 8134, "name": "all_repo_write", "base_role": "write", "permission": "push",
     "description": "Grants write access to all repositories in the organization."},
    {"id": 8135, "name": "all_repo_maintain", "base_role": "maintain", "permission": "maintain",
     "description": "Grants maintenance access to all repositories in the organization."},
    {"id": 8136, "name": "all_repo_admin", "base_role": "admin", "permission": "admin",
     "description": "Grants admin access to all repositories in the organization."},
)

ROLES_BY_ID = {role["id"]: role for role in PREDEFINED_ROLES}


def highest(*permissions: str | None) -> str | None:
    """The highest of the given repository permissions, or None."""
    ranked = [p for p in permissions if p in PERMISSION_ORDER]
    if not ranked:
        return None
    return max(ranked, key=PERMISSION_ORDER.index)


async def org_role_permission(db: AsyncSession, org_id: int, user_id: int) -> str | None:
    """The repository permission an active member holds on every repository
    of ``org_id`` through the organization, or None.

    An organization owner (membership role "admin") has admin on every
    repository, as on GitHub; any other member has what their organization
    roles grant. A role held by someone who is no longer an active member
    grants nothing, as on GitHub, where leaving the organization removes it.
    """
    from app.models.organization import OrgMembership, OrgRoleAssignment

    membership_role = (
        await db.execute(
            select(OrgMembership.role).where(
                OrgMembership.org_id == org_id,
                OrgMembership.user_id == user_id,
                OrgMembership.state == "active",
            )
        )
    ).scalar_one_or_none()
    if membership_role is None:
        return None
    if membership_role == "admin":
        return "admin"
    role_ids = (
        await db.execute(
            select(OrgRoleAssignment.role_id).where(
                OrgRoleAssignment.org_id == org_id,
                OrgRoleAssignment.user_id == user_id,
            )
        )
    ).scalars().all()
    return highest(*(ROLES_BY_ID[r]["permission"] for r in role_ids if r in ROLES_BY_ID))

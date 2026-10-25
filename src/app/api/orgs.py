"""Organization endpoints."""

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func as sa_func, select

from app.api.deps import AuthUser, CurrentUser, DbSession
from app.config import settings
from app.models.organization import Organization, OrgMembership
from app.models.user import User
from app.schemas.organization import OrganizationResponse
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id
from app.services import org_service

router = APIRouter(tags=["orgs"])

BASE = settings.BASE_URL


def _org_json(org: Organization, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    return {
        "login": org.login,
        "id": org.id,
        "node_id": _make_node_id("Organization", org.id),
        "url": f"{api}/orgs/{org.login}",
        "repos_url": f"{api}/orgs/{org.login}/repos",
        "events_url": f"{api}/orgs/{org.login}/events",
        "hooks_url": f"{api}/orgs/{org.login}/hooks",
        "issues_url": f"{api}/orgs/{org.login}/issues",
        "members_url": f"{api}/orgs/{org.login}/members{{/member}}",
        "public_members_url": f"{api}/orgs/{org.login}/public_members{{/member}}",
        "avatar_url": org.avatar_url or f"{base_url}/avatars/{org.login}",
        "description": org.description,
        "name": org.name,
        "company": org.company,
        "blog": org.blog or "",
        "location": org.location,
        "email": org.email,
        "is_verified": False,
        "has_organization_projects": True,
        "has_repository_projects": True,
        "public_repos": 0,
        "public_gists": 0,
        "followers": 0,
        "following": 0,
        "html_url": f"{base_url}/{org.login}",
        "created_at": _fmt_dt(org.created_at),
        "updated_at": _fmt_dt(org.updated_at),
        "type": "Organization",
    }


@router.post("/orgs", status_code=201, response_model=OrganizationResponse)
async def create_org(body: dict, user: AuthUser, db: DbSession):
    """Create an organization."""
    login = body.get("login")
    if not login:
        raise HTTPException(status_code=422, detail="login is required")

    existing = await db.execute(
        select(Organization).where(Organization.login == login)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=422, detail="Organization already exists")

    org = await org_service.create_organization(
        db,
        login,
        user,
        name=body.get("name"),
        description=body.get("description"),
        email=body.get("email"),
        blog=body.get("blog"),
        location=body.get("location"),
        company=body.get("company"),
        billing_email=body.get("billing_email"),
    )
    return _org_json(org, BASE)


@router.post("/admin/organizations", status_code=201, response_model=OrganizationResponse)
async def admin_create_org(body: dict, user: AuthUser, db: DbSession):
    """Create an organization owned by ``admin`` (GitHub Enterprise; site admins only)."""
    if not user.site_admin:
        raise HTTPException(status_code=403, detail="Must be a site administrator")
    login = body.get("login")
    owner_login = body.get("admin")
    if not login or not owner_login:
        raise HTTPException(status_code=422, detail="login and admin are required")
    if (await db.execute(select(Organization).where(Organization.login == login))).scalar_one_or_none():
        raise HTTPException(status_code=422, detail="Organization already exists")
    owner = await org_service.find_user(db, owner_login)
    if owner is None:
        raise HTTPException(status_code=422, detail=f"admin {owner_login} does not exist")

    org = await org_service.create_organization(
        db, login, owner, name=body.get("profile_name")
    )
    return _org_json(org, BASE)


@router.get("/orgs/{org}", response_model=OrganizationResponse)
async def get_org(org: str, db: DbSession, current_user: CurrentUser):
    """Get an organization."""
    result = await db.execute(select(Organization).where(Organization.login == org))
    organisation = result.scalar_one_or_none()
    if organisation is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _org_json(organisation, BASE)


@router.patch("/orgs/{org}", response_model=OrganizationResponse)
async def update_org(org: str, body: dict, user: AuthUser, db: DbSession):
    """Update an organization."""
    result = await db.execute(select(Organization).where(Organization.login == org))
    organisation = result.scalar_one_or_none()
    if organisation is None:
        raise HTTPException(status_code=404, detail="Not Found")

    for key in ("name", "description", "email", "blog", "location", "company", "billing_email"):
        if key in body:
            setattr(organisation, key, body[key])

    await db.commit()
    await db.refresh(organisation)
    return _org_json(organisation, BASE)


@router.get("/organizations", response_model=list[OrganizationResponse])
async def list_all_organizations(
    db: DbSession,
    current_user: CurrentUser,
    since: int = Query(0, description="Only organizations with an id greater than this"),
    per_page: int = Query(30, ge=1, le=100),
):
    """List every organization, in id order.

    GitHub's list-all-organizations endpoint. It is how a client enumerates
    organizations without already knowing their names — `/orgs/{org}` only
    answers when you can name it, and `/user/orgs` only lists ones the caller
    belongs to. The admin frontend has its own `/organizations`, but that is a
    different router with a different response shape and is not what an API
    client reaches.

    Paginated with `since` rather than `page`, as GitHub does here: the cursor
    is the last id seen.
    """
    result = await db.execute(
        select(Organization)
        .where(Organization.id > since)
        .order_by(Organization.id)
        .limit(per_page)
    )
    return [_org_json(o, BASE) for o in result.scalars().all()]


@router.get("/user/orgs", response_model=list[OrganizationResponse])
async def list_user_orgs(user: AuthUser, db: DbSession):
    """List organizations for the authenticated user."""
    result = await db.execute(
        select(Organization)
        .join(OrgMembership, OrgMembership.org_id == Organization.id)
        .where(OrgMembership.user_id == user.id, OrgMembership.state == "active")
    )
    orgs = result.scalars().all()
    return [_org_json(o, BASE) for o in orgs]


@router.get("/users/{username}/orgs", response_model=list[OrganizationResponse])
async def list_user_orgs_public(
    username: str, db: DbSession, current_user: CurrentUser,
):
    """List public organizations for a user."""
    result = await db.execute(select(User).where(User.login == username))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Not Found")

    result = await db.execute(
        select(Organization)
        .join(OrgMembership, OrgMembership.org_id == Organization.id)
        .where(OrgMembership.user_id == target.id, OrgMembership.state == "active")
    )
    orgs = result.scalars().all()
    return [_org_json(o, BASE) for o in orgs]


@router.get("/orgs/{org}/members")
async def list_org_members(
    org: str, db: DbSession, current_user: CurrentUser,
    role: str = Query("all"),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List organization members, optionally only admins or only members."""
    if role not in ("all", "admin", "member"):
        raise HTTPException(status_code=422, detail="role must be one of all, admin, member")
    organisation = await _get_org_or_404(org, db)

    query = select(OrgMembership).where(
        OrgMembership.org_id == organisation.id, OrgMembership.state == "active"
    )
    if role != "all":
        query = query.where(OrgMembership.role == role)
    query = query.order_by(OrgMembership.id).offset((page - 1) * per_page).limit(per_page)
    memberships = (await db.execute(query)).scalars().all()
    return [SimpleUser.from_db(m.user, BASE).model_dump() for m in memberships if m.user]


# ---------------------------------------------------------------------------
# Memberships
#
# Memberships added through the API are active straight away. GitHub sends
# a new member an invitation and keeps the membership pending until they
# accept it; test automation needs the member to be usable at once, so the
# emulator skips that step. A pending row (one written directly to the
# database) is still reported as pending and can be accepted with
# PATCH /user/memberships/orgs/{org}.
# ---------------------------------------------------------------------------

MEMBERSHIP_ROLES = ("admin", "member")


async def _get_org_or_404(login: str, db) -> Organization:
    result = await db.execute(select(Organization).where(Organization.login == login))
    organisation = result.scalar_one_or_none()
    if organisation is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return organisation


async def _get_user_or_404(login: str, db) -> User:
    result = await db.execute(select(User).where(User.login == login))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return user


async def _get_membership(organisation: Organization, user: User, db) -> OrgMembership | None:
    result = await db.execute(
        select(OrgMembership).where(
            OrgMembership.org_id == organisation.id,
            OrgMembership.user_id == user.id,
        )
    )
    return result.scalar_one_or_none()


async def _is_org_admin(organisation: Organization, user: User, db) -> bool:
    if user.site_admin:
        return True
    membership = await _get_membership(organisation, user, db)
    return (
        membership is not None
        and membership.state == "active"
        and membership.role == "admin"
    )


async def _require_org_admin(organisation: Organization, user: User, db) -> None:
    if not await _is_org_admin(organisation, user, db):
        raise HTTPException(
            status_code=403, detail="You must be an admin of this organization"
        )


async def _active_admin_count(organisation: Organization, db) -> int:
    result = await db.execute(
        select(sa_func.count()).select_from(OrgMembership).where(
            OrgMembership.org_id == organisation.id,
            OrgMembership.role == "admin",
            OrgMembership.state == "active",
        )
    )
    return result.scalar() or 0


async def _guard_last_admin(organisation: Organization, membership: OrgMembership, db) -> None:
    """Refuse to leave an organization with no active admin, as GitHub does."""
    if membership.role == "admin" and membership.state == "active":
        if await _active_admin_count(organisation, db) <= 1:
            raise HTTPException(
                status_code=422,
                detail="Cannot remove or demote the last admin of an organization",
            )


def _membership_json(membership: OrgMembership, organisation: Organization, user: User) -> dict:
    api = f"{BASE}/api/v3"
    return {
        "url": f"{api}/orgs/{organisation.login}/memberships/{user.login}",
        "state": membership.state,
        "role": membership.role,
        "organization_url": f"{api}/orgs/{organisation.login}",
        "organization": _org_json(organisation, BASE),
        "user": SimpleUser.from_db(user, BASE).model_dump(),
    }


@router.get("/orgs/{org}/members/{username}")
async def check_org_member(
    org: str, username: str, db: DbSession, current_user: CurrentUser,
):
    """204 when the user is an active member of the organization, else 404."""
    organisation = await _get_org_or_404(org, db)
    target = await _get_user_or_404(username, db)
    membership = await _get_membership(organisation, target, db)
    if membership is None or membership.state != "active":
        raise HTTPException(status_code=404, detail="Not Found")
    return Response(status_code=204)


@router.get("/orgs/{org}/memberships/{username}")
async def get_org_membership(org: str, username: str, user: AuthUser, db: DbSession):
    """One user's membership. Visible to the user, org members and site admins."""
    organisation = await _get_org_or_404(org, db)
    target = await _get_user_or_404(username, db)
    if target.id != user.id and not user.site_admin:
        own = await _get_membership(organisation, user, db)
        if own is None or own.state != "active":
            raise HTTPException(
                status_code=403, detail="You must be a member of this organization"
            )
    membership = await _get_membership(organisation, target, db)
    if membership is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _membership_json(membership, organisation, target)


@router.put("/orgs/{org}/memberships/{username}")
async def set_org_membership(
    org: str, username: str, user: AuthUser, db: DbSession, body: dict | None = None,
):
    """Add a user to the organization or change their role (org admins, site admins)."""
    organisation = await _get_org_or_404(org, db)
    await _require_org_admin(organisation, user, db)
    target = await _get_user_or_404(username, db)

    role = (body or {}).get("role", "member")
    if role not in MEMBERSHIP_ROLES:
        raise HTTPException(status_code=422, detail="role must be one of admin, member")

    membership = await _get_membership(organisation, target, db)
    if membership is None:
        membership = OrgMembership(
            org_id=organisation.id, user_id=target.id, role=role, state="active"
        )
        db.add(membership)
    elif membership.role != role:
        if role == "member":
            await _guard_last_admin(organisation, membership, db)
        membership.role = role
    await db.commit()
    await db.refresh(membership)
    return _membership_json(membership, organisation, target)


async def _remove_membership(org: str, username: str, user, db) -> Response:
    organisation = await _get_org_or_404(org, db)
    await _require_org_admin(organisation, user, db)
    target = await _get_user_or_404(username, db)
    membership = await _get_membership(organisation, target, db)
    if membership is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await _guard_last_admin(organisation, membership, db)
    await db.delete(membership)
    await db.commit()
    return Response(status_code=204)


@router.delete("/orgs/{org}/memberships/{username}", status_code=204)
async def delete_org_membership(org: str, username: str, user: AuthUser, db: DbSession):
    """Remove a user from the organization (org admins, site admins)."""
    return await _remove_membership(org, username, user, db)


@router.delete("/orgs/{org}/members/{username}", status_code=204)
async def delete_org_member(org: str, username: str, user: AuthUser, db: DbSession):
    """Remove a member from the organization (org admins, site admins)."""
    return await _remove_membership(org, username, user, db)


@router.get("/user/memberships/orgs")
async def list_own_memberships(
    user: AuthUser, db: DbSession,
    state: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """The authenticated user's organization memberships."""
    if state not in (None, "active", "pending"):
        raise HTTPException(status_code=422, detail="state must be one of active, pending")
    query = select(OrgMembership).where(OrgMembership.user_id == user.id)
    if state:
        query = query.where(OrgMembership.state == state)
    query = query.order_by(OrgMembership.id).offset((page - 1) * per_page).limit(per_page)
    memberships = (await db.execute(query)).scalars().all()
    return [_membership_json(m, m.organization, user) for m in memberships]


@router.get("/user/memberships/orgs/{org}")
async def get_own_membership(org: str, user: AuthUser, db: DbSession):
    """The authenticated user's membership of one organization."""
    organisation = await _get_org_or_404(org, db)
    membership = await _get_membership(organisation, user, db)
    if membership is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _membership_json(membership, organisation, user)


@router.patch("/user/memberships/orgs/{org}")
async def update_own_membership(org: str, body: dict, user: AuthUser, db: DbSession):
    """Accept a pending membership; ``state`` may only be set to ``active``."""
    if body.get("state") != "active":
        raise HTTPException(status_code=422, detail="state must be active")
    organisation = await _get_org_or_404(org, db)
    membership = await _get_membership(organisation, user, db)
    if membership is None:
        raise HTTPException(status_code=404, detail="Not Found")
    membership.state = "active"
    await db.commit()
    await db.refresh(membership)
    return _membership_json(membership, organisation, user)

"""Organization endpoints."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession
from app.config import settings
from app.models.organization import Organization, OrgMembership
from app.models.user import User
from app.schemas.user import _fmt_dt, _make_node_id

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


@router.post("/orgs", status_code=201)
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

    org = Organization(
        login=login,
        name=body.get("name"),
        description=body.get("description"),
        email=body.get("email"),
        blog=body.get("blog"),
        location=body.get("location"),
        company=body.get("company"),
        billing_email=body.get("billing_email"),
    )
    db.add(org)

    # Add creator as admin member
    membership = OrgMembership(
        org_id=0,  # will be set after flush
        user_id=user.id,
        role="admin",
        state="active",
    )
    await db.flush()
    membership.org_id = org.id
    db.add(membership)
    await db.commit()
    await db.refresh(org)

    return _org_json(org, BASE)


@router.get("/orgs/{org}")
async def get_org(org: str, db: DbSession, current_user: CurrentUser):
    """Get an organization."""
    result = await db.execute(select(Organization).where(Organization.login == org))
    organisation = result.scalar_one_or_none()
    if organisation is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _org_json(organisation, BASE)


@router.patch("/orgs/{org}")
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


@router.get("/user/orgs")
async def list_user_orgs(user: AuthUser, db: DbSession):
    """List organizations for the authenticated user."""
    result = await db.execute(
        select(Organization)
        .join(OrgMembership, OrgMembership.org_id == Organization.id)
        .where(OrgMembership.user_id == user.id, OrgMembership.state == "active")
    )
    orgs = result.scalars().all()
    return [_org_json(o, BASE) for o in orgs]


@router.get("/users/{username}/orgs")
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
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List organization members."""
    result = await db.execute(select(Organization).where(Organization.login == org))
    organisation = result.scalar_one_or_none()
    if organisation is None:
        raise HTTPException(status_code=404, detail="Not Found")

    from app.schemas.user import SimpleUser

    query = (
        select(OrgMembership)
        .where(OrgMembership.org_id == organisation.id, OrgMembership.state == "active")
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    memberships = (await db.execute(query)).scalars().all()
    return [SimpleUser.from_db(m.user, BASE).model_dump() for m in memberships if m.user]

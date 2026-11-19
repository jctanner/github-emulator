"""Team endpoints -- CRUD under /orgs/{org}/teams."""

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, require_installation_grant
from app.config import settings
from app.models.organization import Organization
from app.models.team import Team, TeamMembership
from app.models.user import User
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id

router = APIRouter(tags=["teams"])

BASE = settings.BASE_URL


def _team_json(team: Team, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    org = team.organization
    org_login = org.login if org else "unknown"
    return {
        "id": team.id,
        "node_id": _make_node_id("Team", team.id),
        "url": f"{api}/teams/{team.id}",
        "html_url": f"{base_url}/orgs/{org_login}/teams/{team.slug}",
        "name": team.name,
        "slug": team.slug,
        "description": team.description,
        "privacy": team.privacy,
        "permission": team.permission,
        "members_url": f"{api}/teams/{team.id}/members{{/member}}",
        "repositories_url": f"{api}/teams/{team.id}/repos",
        "created_at": _fmt_dt(team.created_at),
        "updated_at": _fmt_dt(team.updated_at),
        "members_count": len(team.members) if team.members else 0,
        "repos_count": len(team.repos) if team.repos else 0,
        "organization": {
            "login": org_login,
            "id": org.id if org else 0,
        },
    }


@router.get("/orgs/{org}/teams")
async def list_teams(
    org: str, db: DbSession, user: AuthUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List teams in an organization."""
    result = await db.execute(select(Organization).where(Organization.login == org))
    organisation = result.scalar_one_or_none()
    if organisation is None:
        raise HTTPException(status_code=404, detail="Not Found")

    query = (
        select(Team)
        .where(Team.org_id == organisation.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    teams = (await db.execute(query)).scalars().all()
    return [_team_json(t, BASE) for t in teams]


@router.post("/orgs/{org}/teams", status_code=201)
async def create_team(org: str, body: dict, user: AuthUser, db: DbSession):
    """Create a team."""
    result = await db.execute(select(Organization).where(Organization.login == org))
    organisation = result.scalar_one_or_none()
    if organisation is None:
        raise HTTPException(status_code=404, detail="Not Found")

    name = body.get("name")
    if not name:
        raise HTTPException(status_code=422, detail="name is required")

    slug = name.lower().replace(" ", "-")

    team = Team(
        org_id=organisation.id,
        name=name,
        slug=slug,
        description=body.get("description"),
        privacy=body.get("privacy", "closed"),
        permission=body.get("permission", "pull"),
    )
    db.add(team)
    await db.commit()
    await db.refresh(team)
    return _team_json(team, BASE)


@router.get("/teams/{team_id}")
async def get_team(team_id: int, db: DbSession, user: AuthUser):
    """Get a team by ID."""
    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _team_json(team, BASE)


@router.get("/orgs/{org}/teams/{team_slug}")
async def get_team_by_slug(org: str, team_slug: str, db: DbSession, user: AuthUser):
    """Get a team by slug."""
    result = await db.execute(select(Organization).where(Organization.login == org))
    organisation = result.scalar_one_or_none()
    if organisation is None:
        raise HTTPException(status_code=404, detail="Not Found")

    result = await db.execute(
        select(Team).where(Team.org_id == organisation.id, Team.slug == team_slug)
    )
    team = result.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _team_json(team, BASE)


@router.patch("/teams/{team_id}")
async def update_team(team_id: int, body: dict, user: AuthUser, db: DbSession):
    """Update a team."""
    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Not Found")

    for key in ("name", "description", "privacy", "permission"):
        if key in body:
            setattr(team, key, body[key])
    if "name" in body:
        team.slug = body["name"].lower().replace(" ", "-")

    await db.commit()
    await db.refresh(team)
    return _team_json(team, BASE)


@router.delete("/teams/{team_id}", status_code=204)
async def delete_team(team_id: int, user: AuthUser, db: DbSession):
    """Delete a team."""
    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(team)
    await db.commit()


@router.get("/teams/{team_id}/members")
async def list_team_members(
    team_id: int, db: DbSession, user: AuthUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List team members."""
    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Not Found")

    query = (
        select(TeamMembership)
        .where(TeamMembership.team_id == team.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    memberships = (await db.execute(query)).scalars().all()
    return [SimpleUser.from_db(m.user, BASE).model_dump() for m in memberships if m.user]


# -- Team membership for a user ------------------------------------------

TEAM_ROLES = ("member", "maintainer")


async def _team_by_slug_or_404(organisation: Organization, team_slug: str, db) -> Team:
    team = (
        await db.execute(select(Team).where(Team.org_id == organisation.id, Team.slug == team_slug))
    ).scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return team


async def _team_membership(team: Team, target: User, db) -> TeamMembership | None:
    return (
        await db.execute(
            select(TeamMembership).where(
                TeamMembership.team_id == team.id, TeamMembership.user_id == target.id
            )
        )
    ).scalar_one_or_none()


async def _require_team_manager(organisation, team: Team, user: User, db) -> None:
    """Who may add, change or remove a team's members: org admins, site admins
    and the team's own maintainers who are still active members of the
    organization, as on GitHub."""
    from app.api.orgs import _get_membership, _is_org_admin

    if await _is_org_admin(organisation, user, db):
        return
    own = await _team_membership(team, user, db)
    org_membership = await _get_membership(organisation, user, db)
    if (
        own is None
        or own.role != "maintainer"
        or org_membership is None
        or org_membership.state != "active"
    ):
        raise HTTPException(
            status_code=403, detail="You must be an organization owner or a maintainer of this team"
        )


def _team_membership_json(membership: TeamMembership, team: Team, org_login: str, target: User) -> dict:
    return {
        "url": f"{BASE}/api/v3/orgs/{org_login}/teams/{team.slug}/memberships/{target.login}",
        "role": membership.role,
        "state": "active",
    }


@router.get("/orgs/{org}/teams/{team_slug}/memberships/{username}")
async def get_team_membership(
    org: str, team_slug: str, username: str, request: Request, db: DbSession, user: AuthUser,
):
    """A user's membership in a team: 200 with role and state, 404 when the
    user is not a member. The user, active members of the organization, site
    admins and an App installed on the organization with ``members: read``
    can see it. A secret team is visible only to its own members, org admins,
    site admins and such an App, as on GitHub; to anyone else it does not
    exist."""
    from app.api.orgs import _get_membership, _get_org_or_404, _get_user_or_404, _is_org_admin

    organisation = await _get_org_or_404(org, db)
    by_app = require_installation_grant(request, organisation.login, "members", write=False)
    if not by_app and user.login != username and not user.site_admin:
        own = await _get_membership(organisation, user, db)
        if own is None or own.state != "active":
            raise HTTPException(status_code=404, detail="Not Found")
    team = await _team_by_slug_or_404(organisation, team_slug, db)
    if (
        not by_app
        and team.privacy == "secret"
        and not await _is_org_admin(organisation, user, db)
        and await _team_membership(team, user, db) is None
    ):
        raise HTTPException(status_code=404, detail="Not Found")
    target = await _get_user_or_404(username, db)
    membership = await _team_membership(team, target, db)
    if membership is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _team_membership_json(membership, team, organisation.login, target)


@router.put("/orgs/{org}/teams/{team_slug}/memberships/{username}")
async def set_team_membership(
    org: str, team_slug: str, username: str, request: Request, db: DbSession, user: AuthUser,
    body: dict | None = None,
):
    """Add a user to a team or change their team role (org admins, site
    admins and the team's maintainers).

    GitHub invites a user who is not yet in the organization (state
    "pending"); the emulator has no team invitations, so it refuses that
    case with 422 instead of reporting a membership that does not exist.
    """
    from app.api.orgs import _get_membership, _get_org_or_404, _get_user_or_404

    organisation = await _get_org_or_404(org, db)
    team = await _team_by_slug_or_404(organisation, team_slug, db)
    if not require_installation_grant(request, organisation.login, "members", write=True):
        await _require_team_manager(organisation, team, user, db)
    target = await _get_user_or_404(username, db)
    role = (body or {}).get("role", "member")
    if role not in TEAM_ROLES:
        raise HTTPException(status_code=422, detail="role must be one of member, maintainer")
    org_membership = await _get_membership(organisation, target, db)
    if org_membership is None or org_membership.state != "active":
        raise HTTPException(
            status_code=422,
            detail="the user must be an active member of the organization; team invitations are not emulated",
        )
    if org_membership.role == "admin":
        # GitHub makes an organization owner a maintainer of any team they join.
        role = "maintainer"
    membership = await _team_membership(team, target, db)
    if membership is None:
        membership = TeamMembership(team_id=team.id, user_id=target.id, role=role)
        db.add(membership)
    else:
        membership.role = role
    await db.commit()
    await db.refresh(membership)
    return _team_membership_json(membership, team, organisation.login, target)


@router.delete("/orgs/{org}/teams/{team_slug}/memberships/{username}", status_code=204)
async def remove_team_membership(
    org: str, team_slug: str, username: str, request: Request, db: DbSession, user: AuthUser,
):
    """Remove a user from a team (org admins, site admins and the team's
    maintainers)."""
    from fastapi import Response

    from app.api.orgs import _get_org_or_404, _get_user_or_404

    organisation = await _get_org_or_404(org, db)
    team = await _team_by_slug_or_404(organisation, team_slug, db)
    if not require_installation_grant(request, organisation.login, "members", write=True):
        await _require_team_manager(organisation, team, user, db)
    target = await _get_user_or_404(username, db)
    membership = await _team_membership(team, target, db)
    if membership is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(membership)
    await db.commit()
    return Response(status_code=204)

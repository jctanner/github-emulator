"""Team endpoints -- CRUD under /orgs/{org}/teams."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession
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

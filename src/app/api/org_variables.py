"""Organization Actions variables (github-emulator#18).

``/orgs/{org}/actions/variables`` manages an organization's variables and,
for a variable whose visibility is ``selected``, the repositories selected
for it. ``/repos/{owner}/{repo}/actions/organization-variables`` lists the
ones a repository can use (``services.org_variables``), which a workflow's
``vars`` context also includes.

Who may: an organization admin (or site admin); an installation token on the
organization holding ``organization_actions_variables`` (read for reads,
write for changes). A workflow job token may not, as on GitHub.
"""

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import delete, func, select

from app.api.actions import _paged_json
from app.api.deps import AuthUser, DbSession, get_repo_or_404
from app.api.orgs import _get_org_or_404, _is_org_admin
from app.config import settings
from app.models.actions import OrgVariable, OrgVariableRepository
from app.models.repository import Repository
from app.schemas.user import _fmt_dt
from app.services.org_variables import VISIBILITIES, visible_to

router = APIRouter(tags=["actions"])

# GitHub caps these lists at 30 per page.
_MAX_PER_PAGE = 30


def _url(path: str) -> str:
    return f"{settings.BASE_URL}/api/v3{path}"


def _variable_json(org_login: str, variable: OrgVariable) -> dict:
    body = {
        "name": variable.name,
        "value": variable.value,
        "created_at": _fmt_dt(variable.created_at),
        "updated_at": _fmt_dt(variable.updated_at),
        "visibility": variable.visibility,
    }
    if variable.visibility == "selected":
        body["selected_repositories_url"] = _url(f"/orgs/{org_login}/actions/variables/{variable.name}/repositories")
    return body


async def _authorize(request: Request, organisation, user, db, write: bool) -> None:
    state = request.state
    token = getattr(state, "installation_token", None)
    if token is not None:
        installation = token.installation
        granted = str((token.permissions or {}).get("organization_actions_variables", "none")).lower()
        if (
            str(installation.account_login).lower() != organisation.login.lower()
            or not (granted == "write" or (granted == "read" and not write))
        ):
            raise HTTPException(status_code=403, detail="Resource not accessible by integration")
        return
    if getattr(state, "workflow_job", None) is not None:
        raise HTTPException(status_code=403, detail="Resource not accessible by integration")
    if not await _is_org_admin(organisation, user, db):
        raise HTTPException(status_code=403, detail="You must be an admin of this organization")


async def _organisation(request: Request, org: str, user, db, write: bool = False):
    organisation = await _get_org_or_404(org, db)
    await _authorize(request, organisation, user, db, write)
    return organisation


async def _variable_or_404(db, organisation, name: str) -> OrgVariable:
    variable = (await db.execute(
        select(OrgVariable).where(OrgVariable.org_id == organisation.id, OrgVariable.name == name)
    )).scalar_one_or_none()
    if variable is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return variable


def _visibility(body: dict, default: str | None = None) -> str:
    visibility = str(body.get("visibility", default or "")).lower()
    if visibility not in VISIBILITIES:
        raise HTTPException(status_code=422, detail="visibility must be one of all, private, selected")
    return visibility


async def _organisation_repositories(db, organisation, ids) -> list[int]:
    """``ids`` as repository ids of ``organisation``; 422 otherwise."""
    if not isinstance(ids, list) or not all(isinstance(item, int) and not isinstance(item, bool) for item in ids):
        raise HTTPException(status_code=422, detail="selected_repository_ids must be a list of repository ids")
    found = set((await db.execute(
        select(Repository.id).where(Repository.id.in_(ids), Repository.organization_id == organisation.id)
    )).scalars().all()) if ids else set()
    missing = [str(item) for item in ids if item not in found]
    if missing:
        raise HTTPException(status_code=422, detail=f"Repositories not in {organisation.login}: {', '.join(missing)}")
    return list(dict.fromkeys(ids))


async def _select(db, variable: OrgVariable, repo_ids: list[int]) -> None:
    await db.execute(delete(OrgVariableRepository).where(OrgVariableRepository.org_variable_id == variable.id))
    for repo_id in repo_ids:
        db.add(OrgVariableRepository(org_variable_id=variable.id, repo_id=repo_id))


def _require_selected(variable: OrgVariable) -> None:
    if variable.visibility != "selected":
        raise HTTPException(
            status_code=409,
            detail="The visibility of this variable is not set to selected",
        )


@router.get("/orgs/{org}/actions/variables")
async def list_org_variables(
    org: str, request: Request, user: AuthUser, db: DbSession,
    per_page: int = Query(30, ge=1), page: int = Query(1, ge=1),
):
    """List an organization's variables."""
    organisation = await _organisation(request, org, user, db)
    per_page = min(per_page, _MAX_PER_PAGE)
    query = select(OrgVariable).where(OrgVariable.org_id == organisation.id)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    variables = (await db.execute(
        query.order_by(OrgVariable.name).offset((page - 1) * per_page).limit(per_page)
    )).scalars().all()
    return _paged_json(request, page, per_page, total, {
        "total_count": total,
        "variables": [_variable_json(organisation.login, variable) for variable in variables],
    })


@router.post("/orgs/{org}/actions/variables", status_code=201)
async def create_org_variable(org: str, body: dict, request: Request, user: AuthUser, db: DbSession):
    """Create an organization variable."""
    organisation = await _organisation(request, org, user, db, write=True)
    name = str(body.get("name") or "")
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if "value" not in body:
        raise HTTPException(status_code=422, detail="value is required")
    visibility = _visibility(body)
    selected = []
    if visibility == "selected":
        selected = await _organisation_repositories(db, organisation, body.get("selected_repository_ids") or [])
    existing = (await db.execute(
        select(OrgVariable.id).where(OrgVariable.org_id == organisation.id, OrgVariable.name == name)
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Variable {name} already exists. Use PATCH to update it.")
    variable = OrgVariable(org_id=organisation.id, name=name, value=str(body["value"]), visibility=visibility)
    db.add(variable)
    await db.flush()
    await _select(db, variable, selected)
    await db.commit()
    return Response(status_code=201, content="{}", media_type="application/json")


@router.get("/orgs/{org}/actions/variables/{name}")
async def get_org_variable(org: str, name: str, request: Request, user: AuthUser, db: DbSession):
    """Get an organization variable."""
    organisation = await _organisation(request, org, user, db)
    return _variable_json(organisation.login, await _variable_or_404(db, organisation, name))


@router.patch("/orgs/{org}/actions/variables/{name}", status_code=204)
async def update_org_variable(org: str, name: str, body: dict, request: Request, user: AuthUser, db: DbSession):
    """Update an organization variable's name, value, visibility, or selection."""
    organisation = await _organisation(request, org, user, db, write=True)
    variable = await _variable_or_404(db, organisation, name)
    new_name = body.get("name")
    if new_name and new_name != variable.name:
        clash = (await db.execute(
            select(OrgVariable.id).where(OrgVariable.org_id == organisation.id, OrgVariable.name == new_name)
        )).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(status_code=409, detail=f"Variable {new_name} already exists.")
        variable.name = str(new_name)
    if "value" in body:
        variable.value = str(body["value"])
    if "visibility" in body:
        variable.visibility = _visibility(body)
    if variable.visibility != "selected":
        await _select(db, variable, [])
    elif "selected_repository_ids" in body:
        await _select(db, variable, await _organisation_repositories(db, organisation, body["selected_repository_ids"]))
    await db.commit()
    return Response(status_code=204)


@router.delete("/orgs/{org}/actions/variables/{name}", status_code=204)
async def delete_org_variable(org: str, name: str, request: Request, user: AuthUser, db: DbSession):
    """Delete an organization variable."""
    organisation = await _organisation(request, org, user, db, write=True)
    variable = await _variable_or_404(db, organisation, name)
    await _select(db, variable, [])
    await db.delete(variable)
    await db.commit()
    return Response(status_code=204)


@router.get("/orgs/{org}/actions/variables/{name}/repositories")
async def list_selected_repositories(
    org: str, name: str, request: Request, user: AuthUser, db: DbSession,
    per_page: int = Query(30, ge=1), page: int = Query(1, ge=1),
):
    """List the repositories selected for an organization variable."""
    organisation = await _organisation(request, org, user, db)
    variable = await _variable_or_404(db, organisation, name)
    _require_selected(variable)
    per_page = min(per_page, 100)
    query = (
        select(Repository)
        .join(OrgVariableRepository, OrgVariableRepository.repo_id == Repository.id)
        .where(
            OrgVariableRepository.org_variable_id == variable.id,
            Repository.organization_id == organisation.id,
        )
    )
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    repositories = (await db.execute(
        query.order_by(Repository.id).offset((page - 1) * per_page).limit(per_page)
    )).scalars().all()
    from app.api.repos import _repo_json

    return _paged_json(request, page, per_page, total, {
        "total_count": total,
        "repositories": [_repo_json(repository, settings.BASE_URL) for repository in repositories],
    })


@router.put("/orgs/{org}/actions/variables/{name}/repositories", status_code=204)
async def set_selected_repositories(org: str, name: str, body: dict, request: Request, user: AuthUser, db: DbSession):
    """Replace the repositories selected for an organization variable."""
    organisation = await _organisation(request, org, user, db, write=True)
    variable = await _variable_or_404(db, organisation, name)
    _require_selected(variable)
    if "selected_repository_ids" not in body:
        raise HTTPException(status_code=422, detail="selected_repository_ids is required")
    await _select(db, variable, await _organisation_repositories(db, organisation, body["selected_repository_ids"]))
    await db.commit()
    return Response(status_code=204)


@router.put("/orgs/{org}/actions/variables/{name}/repositories/{repository_id}", status_code=204)
async def add_selected_repository(
    org: str, name: str, repository_id: int, request: Request, user: AuthUser, db: DbSession,
):
    """Add a repository to an organization variable's selection."""
    organisation = await _organisation(request, org, user, db, write=True)
    variable = await _variable_or_404(db, organisation, name)
    _require_selected(variable)
    await _organisation_repositories(db, organisation, [repository_id])
    present = (await db.execute(
        select(OrgVariableRepository.id).where(
            OrgVariableRepository.org_variable_id == variable.id, OrgVariableRepository.repo_id == repository_id,
        )
    )).scalar_one_or_none()
    if present is None:
        db.add(OrgVariableRepository(org_variable_id=variable.id, repo_id=repository_id))
        await db.commit()
    return Response(status_code=204)


@router.delete("/orgs/{org}/actions/variables/{name}/repositories/{repository_id}", status_code=204)
async def remove_selected_repository(
    org: str, name: str, repository_id: int, request: Request, user: AuthUser, db: DbSession,
):
    """Remove a repository from an organization variable's selection."""
    organisation = await _organisation(request, org, user, db, write=True)
    variable = await _variable_or_404(db, organisation, name)
    _require_selected(variable)
    await db.execute(delete(OrgVariableRepository).where(
        OrgVariableRepository.org_variable_id == variable.id, OrgVariableRepository.repo_id == repository_id,
    ))
    await db.commit()
    return Response(status_code=204)


async def _require_repository_collaborator(request: Request, repository, user, db) -> None:
    """403 unless the caller has collaborator-level access to ``repository``:
    its owner, a site admin, a collaborator, an admin of its organization, or
    an App installed on it. Installation tokens were scoped upstream."""
    from app.models.organization import Organization
    from app.models.repository import Collaborator
    from app.services.repository_access import installation_role

    state = request.state
    if getattr(state, "installation_token", None) is not None:
        return
    if getattr(state, "workflow_job", None) is not None:
        raise HTTPException(status_code=403, detail="Resource not accessible by integration")
    if user.site_admin or user.id == repository.owner_id:
        return
    collaborator = (await db.execute(
        select(Collaborator.id).where(Collaborator.repo_id == repository.id, Collaborator.user_id == user.id)
    )).scalar_one_or_none()
    if collaborator is not None or await installation_role(db, repository, user) is not None:
        return
    if repository.organization_id is not None:
        organisation = (await db.execute(
            select(Organization).where(Organization.id == repository.organization_id)
        )).scalar_one()
        if await _is_org_admin(organisation, user, db):
            return
    raise HTTPException(status_code=403, detail="Must have admin rights to Repository.")


@router.get("/repos/{owner}/{repo}/actions/organization-variables")
async def list_repository_organization_variables(
    owner: str, repo: str, request: Request, user: AuthUser, db: DbSession,
    per_page: int = Query(30, ge=1), page: int = Query(1, ge=1),
):
    """List the organization variables this repository can use.

    GitHub requires collaborator access to the repository. An installation
    token is held to ``actions_variables`` by the authentication chokepoint;
    a workflow job token has no Variables permission and is refused.
    """
    repository = await get_repo_or_404(owner, repo, db)
    await _require_repository_collaborator(request, repository, user, db)
    per_page = min(per_page, _MAX_PER_PAGE)
    variables = await visible_to(db, repository)
    org_login = owner.split("/", 1)[0]
    if repository.organization_id is not None:
        from app.models.organization import Organization

        org_login = (await db.execute(
            select(Organization.login).where(Organization.id == repository.organization_id)
        )).scalar_one()
    window = variables[(page - 1) * per_page: page * per_page]
    return _paged_json(request, page, per_page, len(variables), {
        "total_count": len(variables),
        "variables": [_variable_json(org_login, variable) for variable in window],
    })

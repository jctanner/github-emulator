"""Which organization Actions variables a repository can use.

An organization variable reaches a repository of that organization by its
``visibility``: ``all`` reaches every one, ``private`` the private and
internal ones, ``selected`` only those selected for it. A workflow's ``vars``
context is those variables overlaid with the repository's own, which take
precedence over an organization variable of the same name.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.actions import OrgVariable, OrgVariableRepository, Variable
from app.models.repository import Repository

VISIBILITIES = ("all", "private", "selected")


def is_private(repository: Repository) -> bool:
    return bool(repository.private) or (repository.visibility or "").lower() in ("private", "internal")


async def visible_to(db: AsyncSession, repository: Repository) -> list[OrgVariable]:
    """The organization variables ``repository`` can use, by name."""
    if repository is None or repository.organization_id is None:
        return []
    variables = (await db.execute(
        select(OrgVariable).where(OrgVariable.org_id == repository.organization_id).order_by(OrgVariable.name)
    )).scalars().all()
    selected = set((await db.execute(
        select(OrgVariableRepository.org_variable_id).where(OrgVariableRepository.repo_id == repository.id)
    )).scalars().all())
    private = is_private(repository)
    return [
        variable for variable in variables
        if variable.visibility == "all"
        or (variable.visibility == "private" and private)
        or (variable.visibility == "selected" and variable.id in selected)
    ]


async def vars_context(db: AsyncSession, repo_id: int) -> dict[str, str]:
    """A workflow run's ``vars``: visible organization variables, then the
    repository's own over them."""
    repository = (await db.execute(select(Repository).where(Repository.id == repo_id))).scalar_one_or_none()
    context = {variable.name: variable.value for variable in await visible_to(db, repository)}
    for variable in (await db.execute(select(Variable).where(Variable.repo_id == repo_id))).scalars().all():
        context[variable.name] = variable.value
    return context

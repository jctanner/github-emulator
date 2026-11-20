"""What a job token (a run's GITHUB_TOKEN) may see beyond its own repository.

A job token authenticates as the run's actor here, so every read that is not
checked separately would see what that user sees. GitHub's GITHUB_TOKEN is an
installation token for the run's repository: it is refused user-scoped
endpoints ("Resource not accessible by integration"), and anything else it
lists or searches holds its own repository plus public ones.

``app.api.deps`` records the run's repository for a job-token request
(:func:`set_job_repository`); listing routes narrow their queries with
:func:`visible_repositories` or :func:`visible_repository_ids`, and search
with ``app.services.repository_access.readable_repositories``, which applies
:func:`job_token_condition`. The value is per request:
``RequestContextMiddleware`` clears it.
"""

from contextvars import ContextVar

from sqlalchemy import and_, func, or_, select

from app.models.repository import Repository

job_repository_id: ContextVar[int | None] = ContextVar("job_repository_id", default=None)


def set_job_repository(repo_id: int | None) -> None:
    job_repository_id.set(repo_id)


def _public():
    return and_(Repository.private.is_not(True), func.coalesce(Repository.visibility, "public") == "public")


def job_token_condition():
    """The condition over Repository a job token is held to: its own
    repository and public ones. None for any other credential."""
    own = job_repository_id.get()
    if own is None:
        return None
    return or_(Repository.id == own, _public())


def visible_repositories(query):
    """``query`` over Repository, narrowed for a job token to its own
    repository and public ones; unchanged for any other credential."""
    condition = job_token_condition()
    if condition is None:
        return query
    return query.where(condition)


def visible_repository_ids(query, column):
    """``query`` whose ``column`` is a repository id, narrowed the same way."""
    own = job_repository_id.get()
    if own is None:
        return query
    return query.where(or_(column == own, column.in_(select(Repository.id).where(_public()))))

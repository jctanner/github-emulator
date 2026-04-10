"""Strawberry GraphQL types for GitHub users."""

import base64
from datetime import datetime
from typing import Annotated, Optional

import strawberry
from strawberry.types import Info
from sqlalchemy import select

from app.graphql.connections import Connection, build_connection
from app.graphql.types.enums import (
    OrderDirection,
    RepositoryAffiliation,
    RepositoryOrder,
    RepositoryOrderField,
    RepositoryPrivacy,
)


def _node_id(type_name: str, db_id: int) -> strawberry.ID:
    """Generate a GitHub-style global node ID."""
    return strawberry.ID(base64.b64encode(f"{type_name}:{db_id}".encode()).decode())


@strawberry.type(name="User")
class GitHubUser:
    """A GitHub user account."""
    login: str
    database_id: int
    name: Optional[str] = None
    email: Optional[str] = None
    bio: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    avatar_url: Optional[str] = None
    url: str = ""
    created_at: datetime = strawberry.UNSET
    is_site_admin: bool = False

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("User", self.database_id)

    @strawberry.field
    async def repositories(
        self,
        info: Info,
        first: Optional[int] = 30,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
        privacy: Optional[RepositoryPrivacy] = None,
        is_fork: Optional[bool] = None,
        owner_affiliations: Optional[list[RepositoryAffiliation]] = None,
        order_by: Optional[RepositoryOrder] = None,
    ) -> Connection[Annotated["Repository", strawberry.lazy("app.graphql.types.repository")]]:
        from app.models.repository import Repository as RepoModel
        from app.graphql.types.repository import repository_from_model

        db = info.context["db"]
        query = select(RepoModel).where(RepoModel.owner_id == self.database_id)

        if privacy is not None:
            if privacy == RepositoryPrivacy.PUBLIC:
                query = query.where(RepoModel.private == False)  # noqa: E712
            elif privacy == RepositoryPrivacy.PRIVATE:
                query = query.where(RepoModel.private == True)  # noqa: E712

        if is_fork is not None:
            query = query.where(RepoModel.fork == is_fork)

        if order_by is not None:
            col_map = {
                RepositoryOrderField.CREATED_AT: RepoModel.created_at,
                RepositoryOrderField.UPDATED_AT: RepoModel.updated_at,
                RepositoryOrderField.PUSHED_AT: RepoModel.pushed_at,
                RepositoryOrderField.NAME: RepoModel.name,
                RepositoryOrderField.STARGAZERS: RepoModel.stargazers_count,
            }
            col = col_map.get(order_by.field, RepoModel.updated_at)
            if order_by.direction == OrderDirection.ASC:
                query = query.order_by(col.asc())
            else:
                query = query.order_by(col.desc())
        else:
            query = query.order_by(RepoModel.updated_at.desc())

        result = await db.execute(query)
        all_repos = result.scalars().all()

        return build_connection(
            all_repos, repository_from_model, len(all_repos),
            first=first, after=after, last=last, before=before,
        )


@strawberry.type
class SimpleUser:
    """A simplified user type for embedding in other objects."""
    login: str
    database_id: int
    avatar_url: Optional[str] = None
    url: str = ""

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("User", self.database_id)


def user_from_model(user) -> GitHubUser:
    """Convert a SQLAlchemy User model to a GitHubUser Strawberry type."""
    from app.config import settings
    base_url = settings.BASE_URL
    return GitHubUser(
        login=user.login,
        database_id=user.id,
        name=user.name,
        email=user.email,
        bio=user.bio,
        company=user.company,
        location=user.location,
        avatar_url=user.avatar_url or f"{base_url}/avatars/{user.login}",
        url=f"{base_url}/users/{user.login}",
        created_at=user.created_at,
        is_site_admin=user.site_admin,
    )


def simple_user_from_model(user) -> SimpleUser:
    """Convert a SQLAlchemy User model to a SimpleUser Strawberry type."""
    from app.config import settings
    base_url = settings.BASE_URL
    return SimpleUser(
        login=user.login,
        database_id=user.id,
        avatar_url=user.avatar_url or f"{base_url}/avatars/{user.login}",
        url=f"{base_url}/users/{user.login}",
    )

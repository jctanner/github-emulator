"""Strawberry GraphQL types for GitHub users."""

import base64
from datetime import datetime
from typing import Annotated, Optional

import strawberry
from sqlalchemy import select
from strawberry.types import Info

from app.graphql.connections import Connection, build_connection
from app.graphql.types.enums import (
    OrderDirection,
    RepositoryAffiliation,
    RepositoryOrder,
    RepositoryOrderField,
    RepositoryPrivacy,
)
from app.graphql.types.node import Node


def _node_id(type_name: str, db_id: int) -> strawberry.ID:
    """Generate a GitHub-style global node ID."""
    return strawberry.ID(base64.b64encode(f"{type_name}:{db_id}".encode()).decode())


@strawberry.interface(name="Actor")
class Actor:
    """Who did something: a User, or an App's Bot (GitHub's Actor interface)."""
    login: str
    avatar_url: Optional[str] = None
    url: str = ""


@strawberry.type(name="Bot")
class Bot(Actor, Node):
    """A GitHub App acting on its own. Its login is the App slug, without the
    "[bot]" suffix REST shows; gh tells a bot by the missing User fields and
    prints it as "app/<login>"."""
    database_id: int

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("Bot", self.database_id)


@strawberry.type(name="User")
class GitHubUser(Actor, Node):
    """A GitHub user account."""
    database_id: int
    name: Optional[str] = None
    email: Optional[str] = None
    bio: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    created_at: datetime = strawberry.UNSET
    is_site_admin: bool = False

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("User", self.database_id)

    @strawberry.field
    async def contributions_collection(
        self,
        info: Info,
        from_: Annotated[Optional[datetime], strawberry.argument(name="from")] = None,
        to: Optional[datetime] = None,
    ) -> Annotated["ContributionsCollection", strawberry.lazy("app.graphql.types.contributions")]:
        """The user's contribution calendar and totals over a window of at
        most a year (see app.graphql.types.contributions)."""
        from app.graphql.types.contributions import resolve_contributions_collection
        return await resolve_contributions_collection(info, self.database_id, from_, to)

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
        from app.graphql.types.repository import repository_from_model
        from app.models.repository import Repository as RepoModel

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

        from app.graphql.authz import visible_only

        result = await db.execute(query)
        all_repos = await visible_only(info, result.scalars().all())

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


def actor_from_model(user) -> Actor:
    """The Actor for an account: a Bot for an App's bot account, else a User."""
    if getattr(user, "type", "User") != "Bot":
        return user_from_model(user)
    from app.config import settings
    slug = user.login.removesuffix("[bot]")
    return Bot(
        login=slug,
        database_id=user.id,
        avatar_url=user.avatar_url or f"{settings.BASE_URL}/avatars/{user.login}",
        url=f"{settings.BASE_URL}/apps/{slug}",
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

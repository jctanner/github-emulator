"""Root GraphQL query resolvers."""

import base64
from enum import Enum
from typing import Annotated, Optional, Union

import strawberry
from strawberry.types import Info
from sqlalchemy import select, or_

from app.graphql.connections import Connection, build_connection
from app.graphql.types.enums import (
    OrderDirection,
    RepositoryOrder,
    RepositoryOrderField,
    RepositoryPrivacy,
)
from app.graphql.types.user import GitHubUser, user_from_model, _node_id
from app.graphql.types.repository import Repository, repository_from_model
from app.graphql.types.issue import Issue, issue_from_model
from app.graphql.types.pull_request import PullRequest, pull_request_from_model


@strawberry.enum
class SearchType(Enum):
    """The type of search to perform."""
    REPOSITORY = "REPOSITORY"
    ISSUE = "ISSUE"
    ISSUE_ADVANCED = "ISSUE_ADVANCED"
    USER = "USER"


# Union type for search results — includes PullRequest for gh CLI compatibility
SearchResultItem = Annotated[
    Union[Repository, Issue, PullRequest, GitHubUser],
    strawberry.union("SearchResultItem"),
]


@strawberry.type
class SearchResultConnection:
    """Search results with pagination info."""
    nodes: list[SearchResultItem]
    total_count: int

    @strawberry.field
    def repository_count(self) -> int:
        return self.total_count

    @strawberry.field
    def issue_count(self) -> int:
        return self.total_count

    @strawberry.field
    def user_count(self) -> int:
        return self.total_count


@strawberry.type
class OrganizationType:
    """A GitHub organization."""
    database_id: int
    login: str
    name: Optional[str] = None
    description: Optional[str] = None
    email: Optional[str] = None
    location: Optional[str] = None
    avatar_url: Optional[str] = None
    url: str = ""
    created_at: strawberry.Private[object] = None

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("Organization", self.database_id)

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
        order_by: Optional[RepositoryOrder] = None,
    ) -> Connection[Repository]:
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

        result = await db.execute(query)
        all_repos = result.scalars().all()

        return build_connection(
            all_repos, repository_from_model, len(all_repos),
            first=first, after=after, last=last, before=before,
        )


def organization_from_model(org) -> OrganizationType:
    """Convert a SQLAlchemy Organization model to an OrganizationType."""
    from app.config import settings
    base_url = settings.BASE_URL
    return OrganizationType(
        database_id=org.id,
        login=org.login,
        name=org.name,
        description=org.description,
        email=org.email,
        location=org.location,
        avatar_url=org.avatar_url or f"{base_url}/avatars/{org.login}",
        url=f"{base_url}/orgs/{org.login}",
        created_at=org.created_at,
    )


# Union type for repositoryOwner
RepositoryOwner = Annotated[
    Union[GitHubUser, OrganizationType],
    strawberry.union("RepositoryOwner"),
]


def _decode_node_id(global_id: str) -> tuple[str, int]:
    """Decode a GitHub-style global node ID.

    GitHub encodes node IDs as base64 strings of the form "04:TypeName<id>".
    We use a simplified version: base64("Type:id").
    """
    try:
        raw = base64.b64decode(global_id).decode("utf-8")
        type_name, id_str = raw.split(":", 1)
        return type_name, int(id_str)
    except Exception:
        raise ValueError(f"Invalid node ID: {global_id}")


def _encode_node_id(type_name: str, db_id: int) -> str:
    """Encode a type name and database ID into a global node ID."""
    raw = f"{type_name}:{db_id}"
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


# Union type for the node query
Node = Annotated[
    Union[Repository, Issue, PullRequest, GitHubUser],
    strawberry.union("Node"),
]


@strawberry.type
class Query:
    """Root query type for the GitHub GraphQL API emulator."""

    @strawberry.field
    async def repository(
        self,
        info: Info,
        owner: str,
        name: str,
    ) -> Optional[Repository]:
        """Look up a repository by owner and name."""
        from app.models.repository import Repository as RepoModel
        db = info.context["db"]
        full_name = f"{owner}/{name}"
        result = await db.execute(
            select(RepoModel).where(RepoModel.full_name == full_name)
        )
        repo = result.scalar_one_or_none()
        if repo:
            return repository_from_model(repo)
        return None

    @strawberry.field
    async def user(self, info: Info, login: str) -> Optional[GitHubUser]:
        """Look up a user by login."""
        from app.models.user import User
        db = info.context["db"]
        result = await db.execute(select(User).where(User.login == login))
        user = result.scalar_one_or_none()
        if user:
            return user_from_model(user)
        return None

    @strawberry.field
    async def viewer(self, info: Info) -> GitHubUser:
        """Return the currently authenticated user."""
        current_user = info.context.get("user")
        if current_user is None:
            raise PermissionError("Authentication required")
        return user_from_model(current_user)

    @strawberry.field
    async def organization(
        self, info: Info, login: str
    ) -> Optional[OrganizationType]:
        """Look up an organization by login."""
        from app.models.organization import Organization
        db = info.context["db"]
        result = await db.execute(
            select(Organization).where(Organization.login == login)
        )
        org = result.scalar_one_or_none()
        if org:
            return organization_from_model(org)
        return None

    @strawberry.field
    async def repository_owner(
        self, info: Info, login: str
    ) -> Optional[RepositoryOwner]:
        """Look up a repository owner (user or organization) by login."""
        from app.models.user import User
        from app.models.organization import Organization

        db = info.context["db"]

        # Try user first
        result = await db.execute(select(User).where(User.login == login))
        user = result.scalar_one_or_none()
        if user:
            return user_from_model(user)

        # Try organization
        result = await db.execute(
            select(Organization).where(Organization.login == login)
        )
        org = result.scalar_one_or_none()
        if org:
            return organization_from_model(org)

        return None

    @strawberry.field
    async def node(self, info: Info, id: strawberry.ID) -> Optional[Node]:
        """Look up a node by its global ID.

        The global ID is a base64-encoded string of the form 'Type:database_id'.
        """
        try:
            type_name, db_id = _decode_node_id(id)
        except ValueError:
            return None

        db = info.context["db"]

        if type_name == "Repository":
            from app.models.repository import Repository as RepoModel
            result = await db.execute(
                select(RepoModel).where(RepoModel.id == db_id)
            )
            repo = result.scalar_one_or_none()
            if repo:
                return repository_from_model(repo)

        elif type_name == "Issue":
            from app.models.issue import Issue as IssueModel
            result = await db.execute(
                select(IssueModel).where(IssueModel.id == db_id)
            )
            issue = result.scalar_one_or_none()
            if issue:
                return issue_from_model(issue)

        elif type_name == "PullRequest":
            from app.models.pull_request import PullRequest as PRModel
            result = await db.execute(
                select(PRModel).where(PRModel.id == db_id)
            )
            pr = result.scalar_one_or_none()
            if pr:
                return pull_request_from_model(pr)

        elif type_name == "User":
            from app.models.user import User
            result = await db.execute(
                select(User).where(User.id == db_id)
            )
            user = result.scalar_one_or_none()
            if user:
                return user_from_model(user)

        return None

    @strawberry.field
    async def search(
        self,
        info: Info,
        query: str,
        type: SearchType,
        first: int = 10,
    ) -> SearchResultConnection:
        """Search for repositories, issues, or users.

        Supports basic keyword search against relevant fields.
        """
        db = info.context["db"]
        nodes: list = []
        total_count = 0
        search_term = f"%{query}%"

        if type == SearchType.REPOSITORY:
            from app.models.repository import Repository as RepoModel
            result = await db.execute(
                select(RepoModel)
                .where(
                    or_(
                        RepoModel.full_name.ilike(search_term),
                        RepoModel.name.ilike(search_term),
                        RepoModel.description.ilike(search_term),
                    )
                )
                .limit(first)
            )
            repos = result.scalars().all()
            nodes = [repository_from_model(r) for r in repos]
            total_count = len(nodes)

        elif type in (SearchType.ISSUE, SearchType.ISSUE_ADVANCED):
            from app.models.issue import Issue as IssueModel
            from app.models.pull_request import PullRequest as PRModel

            # Search issues
            issue_result = await db.execute(
                select(IssueModel)
                .where(
                    or_(
                        IssueModel.title.ilike(search_term),
                        IssueModel.body.ilike(search_term),
                    )
                )
                .limit(first)
            )
            issues = issue_result.scalars().all()

            # Check which issues have associated PRs
            issue_ids = [i.id for i in issues]
            pr_result = await db.execute(
                select(PRModel).where(PRModel.issue_id.in_(issue_ids))
            ) if issue_ids else None

            pr_by_issue_id = {}
            if pr_result:
                for pr in pr_result.scalars().all():
                    pr_by_issue_id[pr.issue_id] = pr

            for issue in issues:
                if issue.id in pr_by_issue_id:
                    nodes.append(pull_request_from_model(pr_by_issue_id[issue.id]))
                else:
                    nodes.append(issue_from_model(issue))

            total_count = len(nodes)

        elif type == SearchType.USER:
            from app.models.user import User
            result = await db.execute(
                select(User)
                .where(
                    or_(
                        User.login.ilike(search_term),
                        User.name.ilike(search_term),
                        User.email.ilike(search_term),
                    )
                )
                .limit(first)
            )
            users = result.scalars().all()
            nodes = [user_from_model(u) for u in users]
            total_count = len(nodes)

        return SearchResultConnection(nodes=nodes, total_count=total_count)

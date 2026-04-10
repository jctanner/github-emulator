"""Strawberry GraphQL types for GitHub repositories."""

from datetime import datetime
from typing import Annotated, Optional, Union

import strawberry
from strawberry.types import Info
from sqlalchemy import select, func as sa_func

from app.graphql.connections import Connection, build_connection
from app.graphql.types.user import GitHubUser, user_from_model, _node_id
from app.graphql.types.enums import IssueState, IssueOrder, IssueFilters, LabelOrder
from app.graphql.types.stubs import (
    LicenseInfo,
    RepositoryTopic,
    FundingLink,
    CodeOfConduct,
    ContactLink,
    IssueTemplate,
    PullRequestTemplate,
    ReleaseStub,
    ProjectV2Stub,
    ProjectCardStub,
    empty_connection,
)


@strawberry.type
class Language:
    """A programming language."""
    name: str
    color: Optional[str] = None


@strawberry.type
class Ref:
    """A Git reference (branch or tag)."""
    name: str
    prefix: str

    @strawberry.field
    def id(self) -> str:
        return f"{self.prefix}{self.name}"


@strawberry.type
class Label:
    """A label on a repository."""
    database_id: int
    name: str
    color: str
    description: Optional[str] = None

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("Label", self.database_id)


@strawberry.type
class MilestoneType:
    """A milestone on a repository."""
    database_id: int
    number: int
    title: str
    description: Optional[str] = None
    state: str = "OPEN"
    created_at: datetime = strawberry.UNSET
    updated_at: datetime = strawberry.UNSET
    closed_at: Optional[datetime] = None
    due_on: Optional[datetime] = None

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("Milestone", self.database_id)


def label_from_model(label) -> Label:
    """Convert a SQLAlchemy Label model to a Label Strawberry type."""
    return Label(
        database_id=label.id,
        name=label.name,
        color=label.color,
        description=label.description,
    )


def milestone_from_model(ms) -> MilestoneType:
    """Convert a SQLAlchemy Milestone model to a MilestoneType Strawberry type."""
    return MilestoneType(
        database_id=ms.id,
        number=ms.number,
        title=ms.title,
        description=ms.description,
        state=ms.state.upper(),
        created_at=ms.created_at,
        updated_at=ms.updated_at,
        closed_at=ms.closed_at,
        due_on=ms.due_on,
    )


def ref_from_branch(branch) -> Ref:
    """Convert a SQLAlchemy Branch model to a Ref Strawberry type."""
    return Ref(name=branch.name, prefix="refs/heads/")


@strawberry.type
class Repository:
    """A GitHub repository."""
    database_id: int
    name: str
    name_with_owner: str
    description: Optional[str] = None
    url: str = ""
    is_private: bool = False
    is_fork: bool = False
    is_archived: bool = False
    is_template: bool = False
    created_at: datetime = strawberry.UNSET
    updated_at: datetime = strawberry.UNSET
    pushed_at: Optional[datetime] = None
    disk_usage: int = 0
    stargazer_count: int = 0
    fork_count: int = 0
    has_issues_enabled: bool = True
    has_wiki_enabled: bool = True
    has_projects_enabled: bool = True

    # Internal fields (not exposed directly, used by resolvers)
    _owner_id: strawberry.Private[int] = 0
    _default_branch: strawberry.Private[str] = "main"
    _language: strawberry.Private[Optional[str]] = None
    _open_issues_count: strawberry.Private[int] = 0
    _visibility: strawberry.Private[str] = "public"
    _homepage: strawberry.Private[Optional[str]] = None
    _has_discussions: strawberry.Private[bool] = False
    _is_in_organization: strawberry.Private[bool] = False

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("Repository", self.database_id)

    @strawberry.field
    def primary_language(self) -> Optional[Language]:
        if self._language:
            return Language(name=self._language)
        return None

    # --- Real data fields ---

    @strawberry.field
    def visibility(self) -> str:
        return self._visibility.upper()

    @strawberry.field
    def ssh_url(self) -> str:
        from app.config import settings
        return f"git@{settings.HOSTNAME}:{self.name_with_owner}.git"

    @strawberry.field
    def has_discussions_enabled(self) -> bool:
        return self._has_discussions

    # --- Viewer context fields (single-user emulator defaults) ---

    @strawberry.field
    def viewer_permission(self) -> str:
        return "ADMIN"

    @strawberry.field
    def viewer_can_administer(self) -> bool:
        return True

    @strawberry.field
    def viewer_has_starred(self) -> bool:
        return False

    @strawberry.field
    def viewer_subscription(self) -> str:
        return "UNSUBSCRIBED"

    @strawberry.field
    def viewer_default_commit_email(self) -> Optional[str]:
        return None

    @strawberry.field
    def viewer_default_merge_method(self) -> str:
        return "MERGE"

    @strawberry.field
    def viewer_possible_commit_emails(self) -> list[str]:
        return []

    # --- Merge settings (stubs) ---

    @strawberry.field
    def merge_commit_allowed(self) -> bool:
        return True

    @strawberry.field
    def squash_merge_allowed(self) -> bool:
        return True

    @strawberry.field
    def rebase_merge_allowed(self) -> bool:
        return True

    @strawberry.field
    def delete_branch_on_merge(self) -> bool:
        return False

    # --- Boolean flags (stubs) ---

    @strawberry.field
    def is_empty(self) -> bool:
        return False

    @strawberry.field
    def is_in_organization(self) -> bool:
        return self._is_in_organization

    @strawberry.field
    def is_mirror(self) -> bool:
        return False

    @strawberry.field
    def is_blank_issues_enabled(self) -> bool:
        return True

    @strawberry.field
    def is_security_policy_enabled(self) -> bool:
        return False

    @strawberry.field
    def is_user_configuration_repository(self) -> bool:
        return False

    # --- Optional string fields (stubs) ---

    @strawberry.field
    def mirror_url(self) -> Optional[str]:
        return None

    @strawberry.field
    def security_policy_url(self) -> Optional[str]:
        return None

    @strawberry.field
    def open_graph_image_url(self) -> Optional[str]:
        return None

    @strawberry.field
    def homepage_url(self) -> Optional[str]:
        return self._homepage

    @strawberry.field
    def archived_at(self) -> Optional[str]:
        return None

    # --- Resolver fields returning None/empty (stubs) ---

    @strawberry.field
    def parent(self) -> Optional["Repository"]:
        return None

    @strawberry.field
    def template_repository(self) -> Optional["Repository"]:
        return None

    @strawberry.field
    def license_info(self) -> Optional[LicenseInfo]:
        return None

    @strawberry.field
    def repository_topics(self) -> Connection[RepositoryTopic]:
        return empty_connection()

    @strawberry.field
    def languages(self) -> Connection[Language]:
        return empty_connection()

    @strawberry.field
    def watchers(self) -> Connection[GitHubUser]:
        return empty_connection()

    @strawberry.field
    def funding_links(self) -> list[FundingLink]:
        return []

    @strawberry.field
    def contact_links(self) -> list[ContactLink]:
        return []

    @strawberry.field
    def code_of_conduct(self) -> Optional[CodeOfConduct]:
        return None

    @strawberry.field
    def issue_templates(self) -> list[IssueTemplate]:
        return []

    @strawberry.field
    def pull_request_templates(self) -> list[PullRequestTemplate]:
        return []

    @strawberry.field
    def latest_release(self) -> Optional[ReleaseStub]:
        return None

    @strawberry.field
    def assignable_users(self) -> Connection[GitHubUser]:
        return empty_connection()

    @strawberry.field
    def mentionable_users(self) -> Connection[GitHubUser]:
        return empty_connection()

    @strawberry.field
    def projects(self) -> Connection[ProjectCardStub]:
        return empty_connection()

    @strawberry.field
    def projects_v2(self) -> Connection[ProjectV2Stub]:
        return empty_connection()

    # --- Async resolvers ---

    @strawberry.field
    async def owner(self, info: Info) -> GitHubUser:
        from app.models.user import User
        db = info.context["db"]
        result = await db.execute(select(User).where(User.id == self._owner_id))
        user = result.scalar_one_or_none()
        if user:
            return user_from_model(user)
        # Fallback: should not happen if data integrity is maintained
        return GitHubUser(login="unknown", database_id=0)

    @strawberry.field
    async def default_branch_ref(self, info: Info) -> Optional[Ref]:
        from app.models.branch import Branch
        db = info.context["db"]
        result = await db.execute(
            select(Branch).where(
                Branch.repo_id == self.database_id,
                Branch.name == self._default_branch,
            )
        )
        branch = result.scalar_one_or_none()
        if branch:
            return ref_from_branch(branch)
        # Return a synthetic ref even if the branch row doesn't exist yet
        return Ref(name=self._default_branch, prefix="refs/heads/")

    @strawberry.field
    async def open_issues(self) -> "OpenIssueCount":
        return OpenIssueCount(total_count=self._open_issues_count)

    @strawberry.field
    async def issue(
        self,
        info: Info,
        number: int,
    ) -> Optional[Annotated["Issue", strawberry.lazy("app.graphql.types.issue")]]:
        """Look up a single issue by number."""
        from app.models.issue import Issue
        from app.graphql.types.issue import issue_from_model

        db = info.context["db"]
        result = await db.execute(
            select(Issue).where(
                Issue.repo_id == self.database_id,
                Issue.number == number,
            )
        )
        issue = result.scalar_one_or_none()
        if issue:
            return issue_from_model(issue)
        return None

    @strawberry.field
    async def issue_or_pull_request(
        self,
        info: Info,
        number: int,
    ) -> Optional[Annotated[
        Union[
            Annotated["Issue", strawberry.lazy("app.graphql.types.issue")],
            Annotated["PullRequest", strawberry.lazy("app.graphql.types.pull_request")],
        ],
        strawberry.union("IssueOrPullRequest"),
    ]]:
        """Look up an issue or pull request by number."""
        from app.models.issue import Issue
        from app.models.pull_request import PullRequest as PRModel
        from app.graphql.types.issue import issue_from_model
        from app.graphql.types.pull_request import pull_request_from_model

        db = info.context["db"]
        result = await db.execute(
            select(Issue).where(
                Issue.repo_id == self.database_id,
                Issue.number == number,
            )
        )
        issue = result.scalar_one_or_none()
        if not issue:
            return None

        # Check if this is a pull request
        pr_result = await db.execute(
            select(PRModel).where(PRModel.issue_id == issue.id)
        )
        pr = pr_result.scalar_one_or_none()
        if pr:
            return pull_request_from_model(pr)
        return issue_from_model(issue)

    @strawberry.field
    async def pull_request(
        self,
        info: Info,
        number: int,
    ) -> Optional[Annotated["PullRequest", strawberry.lazy("app.graphql.types.pull_request")]]:
        """Look up a single pull request by number."""
        from app.models.pull_request import PullRequest as PRModel
        from app.models.issue import Issue
        from app.graphql.types.pull_request import pull_request_from_model

        db = info.context["db"]
        result = await db.execute(
            select(PRModel)
            .join(Issue, PRModel.issue_id == Issue.id)
            .where(
                PRModel.repo_id == self.database_id,
                Issue.number == number,
            )
        )
        pr = result.scalar_one_or_none()
        if pr:
            return pull_request_from_model(pr)
        return None

    @strawberry.field
    async def issues(
        self,
        info: Info,
        first: Optional[int] = 10,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
        states: Optional[list[IssueState]] = None,
        order_by: Optional[IssueOrder] = None,
        filter_by: Optional[IssueFilters] = None,
    ) -> Connection[Annotated["Issue", strawberry.lazy("app.graphql.types.issue")]]:
        from app.models.issue import Issue
        from app.graphql.types.issue import issue_from_model

        db = info.context["db"]
        query = select(Issue).where(Issue.repo_id == self.database_id)

        # Filter by states (enum values like OPEN/CLOSED)
        if states:
            lower_states = [s.value.lower() if hasattr(s, 'value') else s.lower() for s in states]
            query = query.where(Issue.state.in_(lower_states))

        # Filter by labels, assignee, etc.
        if filter_by is not None:
            if filter_by.assignee is not None:
                from app.models.issue import IssueAssignee
                from app.models.user import User
                assignee_subq = (
                    select(IssueAssignee.issue_id)
                    .join(User, User.id == IssueAssignee.user_id)
                    .where(User.login == filter_by.assignee)
                )
                query = query.where(Issue.id.in_(assignee_subq))
            if filter_by.mentioned is not None:
                pass  # stub
            if filter_by.created_by is not None:
                from app.models.user import User
                creator_subq = select(User.id).where(User.login == filter_by.created_by)
                query = query.where(Issue.user_id.in_(creator_subq))
            if filter_by.labels is not None and filter_by.labels:
                from app.models.label import Label as LabelModel
                from app.models.issue import IssueLabel
                for label_name in filter_by.labels:
                    label_subq = (
                        select(IssueLabel.issue_id)
                        .join(LabelModel, LabelModel.id == IssueLabel.label_id)
                        .where(LabelModel.name == label_name)
                    )
                    query = query.where(Issue.id.in_(label_subq))
            if filter_by.states is not None:
                filter_lower = [s.value.lower() if hasattr(s, 'value') else s.lower() for s in filter_by.states]
                query = query.where(Issue.state.in_(filter_lower))

        # Ordering
        if order_by is not None:
            from app.graphql.types.enums import IssueOrderField, OrderDirection
            col_map = {
                IssueOrderField.CREATED_AT: Issue.created_at,
                IssueOrderField.UPDATED_AT: Issue.updated_at,
                IssueOrderField.COMMENTS: Issue.created_at,  # fallback
            }
            col = col_map.get(order_by.field, Issue.created_at)
            if order_by.direction == OrderDirection.ASC:
                query = query.order_by(col.asc())
            else:
                query = query.order_by(col.desc())
        else:
            query = query.order_by(Issue.number.asc())

        result = await db.execute(query)
        all_issues = result.scalars().all()

        count_query = select(sa_func.count()).select_from(Issue).where(
            Issue.repo_id == self.database_id
        )
        if states:
            lower_states = [s.value.lower() if hasattr(s, 'value') else s.lower() for s in states]
            count_query = count_query.where(Issue.state.in_(lower_states))
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0

        return build_connection(
            all_issues, issue_from_model, total,
            first=first, after=after, last=last, before=before,
        )

    @strawberry.field
    async def pull_requests(
        self,
        info: Info,
        first: Optional[int] = 10,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
        states: Optional[list[str]] = None,
        head_ref_name: Optional[str] = None,
    ) -> Connection[Annotated["PullRequest", strawberry.lazy("app.graphql.types.pull_request")]]:
        from app.models.pull_request import PullRequest
        from app.models.issue import Issue
        from app.graphql.types.pull_request import pull_request_from_model

        db = info.context["db"]
        query = (
            select(PullRequest)
            .join(Issue, PullRequest.issue_id == Issue.id)
            .where(PullRequest.repo_id == self.database_id)
        )

        if head_ref_name:
            query = query.where(PullRequest.head_ref == head_ref_name)

        if states:
            upper_states = [s.upper() for s in states]
            # Map states: MERGED is a special state derived from merged flag
            state_filters = []
            if "OPEN" in upper_states:
                state_filters.append(
                    (Issue.state == "open") & (PullRequest.merged == False)  # noqa: E712
                )
            if "CLOSED" in upper_states:
                state_filters.append(
                    (Issue.state == "closed") & (PullRequest.merged == False)  # noqa: E712
                )
            if "MERGED" in upper_states:
                state_filters.append(PullRequest.merged == True)  # noqa: E712
            if state_filters:
                from sqlalchemy import or_
                query = query.where(or_(*state_filters))

        query = query.order_by(Issue.number.asc())
        result = await db.execute(query)
        all_prs = result.scalars().all()

        count_query = (
            select(sa_func.count())
            .select_from(PullRequest)
            .where(PullRequest.repo_id == self.database_id)
        )
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0

        return build_connection(
            all_prs, pull_request_from_model, total,
            first=first, after=after, last=last, before=before,
        )

    @strawberry.field
    async def labels(
        self,
        info: Info,
        first: Optional[int] = 30,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
        query: Optional[str] = None,
        order_by: Optional[LabelOrder] = None,
    ) -> Connection[Label]:
        from app.models.label import Label as LabelModel
        db = info.context["db"]
        q = select(LabelModel).where(LabelModel.repo_id == self.database_id)
        if query:
            q = q.where(LabelModel.name.ilike(f"%{query}%"))
        if order_by is not None:
            from app.graphql.types.enums import LabelOrderField, OrderDirection
            col_map = {
                LabelOrderField.CREATED_AT: LabelModel.id,  # labels don't have created_at; use id as proxy
                LabelOrderField.NAME: LabelModel.name,
            }
            col = col_map.get(order_by.field, LabelModel.name)
            if order_by.direction == OrderDirection.ASC:
                q = q.order_by(col.asc())
            else:
                q = q.order_by(col.desc())
        else:
            q = q.order_by(LabelModel.name.asc())
        result = await db.execute(q)
        all_labels = result.scalars().all()
        return build_connection(
            all_labels, label_from_model, len(all_labels),
            first=first, after=after, last=last, before=before,
        )

    @strawberry.field
    async def milestones(
        self,
        info: Info,
        first: Optional[int] = 10,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
    ) -> Connection[MilestoneType]:
        from app.models.milestone import Milestone
        db = info.context["db"]
        result = await db.execute(
            select(Milestone)
            .where(Milestone.repo_id == self.database_id)
            .order_by(Milestone.number.asc())
        )
        all_milestones = result.scalars().all()
        return build_connection(
            all_milestones, milestone_from_model, len(all_milestones),
            first=first, after=after, last=last, before=before,
        )

    @strawberry.field
    async def refs(
        self,
        info: Info,
        first: Optional[int] = 30,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
        ref_prefix: str = "refs/heads/",
    ) -> Connection[Ref]:
        from app.models.branch import Branch
        db = info.context["db"]
        result = await db.execute(
            select(Branch)
            .where(Branch.repo_id == self.database_id)
            .order_by(Branch.name.asc())
        )
        all_branches = result.scalars().all()
        return build_connection(
            all_branches, ref_from_branch, len(all_branches),
            first=first, after=after, last=last, before=before,
        )


@strawberry.type
class OpenIssueCount:
    """Wrapper type for issue count, matching GitHub's totalCount pattern."""
    total_count: int


def repository_from_model(repo) -> Repository:
    """Convert a SQLAlchemy Repository model to a Repository Strawberry type."""
    from app.config import settings
    base_url = settings.BASE_URL
    return Repository(
        database_id=repo.id,
        name=repo.name,
        name_with_owner=repo.full_name,
        description=repo.description,
        url=f"{base_url}/{repo.full_name}",
        is_private=repo.private,
        is_fork=repo.fork,
        is_archived=repo.archived,
        is_template=repo.is_template,
        created_at=repo.created_at,
        updated_at=repo.updated_at,
        pushed_at=repo.pushed_at,
        disk_usage=repo.size,
        stargazer_count=repo.stargazers_count,
        fork_count=repo.forks_count,
        has_issues_enabled=repo.has_issues,
        has_wiki_enabled=repo.has_wiki,
        has_projects_enabled=repo.has_projects,
        _owner_id=repo.owner_id,
        _default_branch=repo.default_branch,
        _language=repo.language,
        _open_issues_count=repo.open_issues_count,
        _visibility=getattr(repo, 'visibility', 'private' if repo.private else 'public'),
        _homepage=getattr(repo, 'homepage', None),
        _has_discussions=getattr(repo, 'has_discussions', False),
        _is_in_organization=getattr(repo, 'owner_type', 'User') == 'Organization',
    )

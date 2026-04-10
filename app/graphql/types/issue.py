"""Strawberry GraphQL types for GitHub issues."""

from datetime import datetime
from typing import Annotated, Optional

import strawberry
from strawberry.types import Info
from sqlalchemy import select, func as sa_func

from app.graphql.connections import Connection, build_connection
from app.graphql.types.user import GitHubUser, user_from_model, _node_id
from app.graphql.types.enums import IssueState
from app.graphql.types.repository import (
    Label,
    MilestoneType,
    label_from_model,
    milestone_from_model,
)
from app.graphql.types.stubs import (
    ReactionGroup,
    STANDARD_REACTION_GROUPS,
    ProjectCardStub,
    ProjectV2Stub,
    empty_connection,
)


@strawberry.type
class IssueComment:
    """A comment on an issue."""
    database_id: int
    body: str
    created_at: datetime = strawberry.UNSET
    updated_at: datetime = strawberry.UNSET
    _user_id: strawberry.Private[int] = 0

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("IssueComment", self.database_id)

    @strawberry.field
    async def author(self, info: Info) -> Optional[GitHubUser]:
        from app.models.user import User
        db = info.context["db"]
        result = await db.execute(select(User).where(User.id == self._user_id))
        user = result.scalar_one_or_none()
        if user:
            return user_from_model(user)
        return None

    @strawberry.field
    def url(self) -> str:
        return ""

    @strawberry.field
    def author_association(self) -> str:
        return "NONE"

    @strawberry.field
    def includes_created_edit(self) -> bool:
        return False

    @strawberry.field
    def is_minimized(self) -> bool:
        return False

    @strawberry.field
    def minimized_reason(self) -> Optional[str]:
        return None

    @strawberry.field
    def reaction_groups(self) -> list[ReactionGroup]:
        return list(STANDARD_REACTION_GROUPS)


def comment_from_model(comment) -> IssueComment:
    """Convert a SQLAlchemy IssueComment model to a Strawberry IssueComment."""
    return IssueComment(
        database_id=comment.id,
        body=comment.body,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
        _user_id=comment.user_id,
    )


@strawberry.type
class Issue:
    """A GitHub issue."""
    database_id: int
    number: int
    title: str
    body: Optional[str] = None
    state: IssueState = IssueState.OPEN
    created_at: datetime = strawberry.UNSET
    updated_at: datetime = strawberry.UNSET
    closed_at: Optional[datetime] = None
    locked: bool = False

    # Private fields for lazy resolution
    _user_id: strawberry.Private[int] = 0
    _repo_id: strawberry.Private[int] = 0
    _milestone_id: strawberry.Private[Optional[int]] = None
    _url: strawberry.Private[str] = ""
    _state_reason: strawberry.Private[Optional[str]] = None

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("Issue", self.database_id)

    @strawberry.field
    def url(self) -> str:
        return self._url

    @strawberry.field
    def closed(self) -> bool:
        return self.state == IssueState.CLOSED

    @strawberry.field
    def is_pinned(self) -> bool:
        return False

    @strawberry.field
    def state_reason(self) -> Optional[str]:
        return self._state_reason

    @strawberry.field
    def reaction_groups(self) -> list[ReactionGroup]:
        return list(STANDARD_REACTION_GROUPS)

    @strawberry.field
    async def author(self, info: Info) -> Optional[GitHubUser]:
        from app.models.user import User
        db = info.context["db"]
        result = await db.execute(select(User).where(User.id == self._user_id))
        user = result.scalar_one_or_none()
        if user:
            return user_from_model(user)
        return None

    @strawberry.field
    async def milestone(self, info: Info) -> Optional[MilestoneType]:
        if not self._milestone_id:
            return None
        from app.models.milestone import Milestone
        db = info.context["db"]
        result = await db.execute(
            select(Milestone).where(Milestone.id == self._milestone_id)
        )
        ms = result.scalar_one_or_none()
        if ms:
            return milestone_from_model(ms)
        return None

    @strawberry.field
    async def labels(
        self,
        info: Info,
        first: Optional[int] = 30,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
    ) -> Connection[Label]:
        from app.models.label import Label as LabelModel
        from app.models.issue import IssueLabel

        db = info.context["db"]
        result = await db.execute(
            select(LabelModel)
            .join(IssueLabel, LabelModel.id == IssueLabel.label_id)
            .where(IssueLabel.issue_id == self.database_id)
            .order_by(LabelModel.name.asc())
        )
        all_labels = result.scalars().all()
        return build_connection(
            all_labels, label_from_model, len(all_labels),
            first=first, after=after, last=last, before=before,
        )

    @strawberry.field
    async def assignees(
        self,
        info: Info,
        first: Optional[int] = 10,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
    ) -> Connection[GitHubUser]:
        from app.models.user import User
        from app.models.issue import IssueAssignee

        db = info.context["db"]
        result = await db.execute(
            select(User)
            .join(IssueAssignee, User.id == IssueAssignee.user_id)
            .where(IssueAssignee.issue_id == self.database_id)
        )
        all_assignees = result.scalars().all()
        return build_connection(
            all_assignees, user_from_model, len(all_assignees),
            first=first, after=after, last=last, before=before,
        )

    @strawberry.field
    async def comments(
        self,
        info: Info,
        first: Optional[int] = 10,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
    ) -> Connection[IssueComment]:
        from app.models.comment import IssueComment as IssueCommentModel

        db = info.context["db"]
        result = await db.execute(
            select(IssueCommentModel)
            .where(IssueCommentModel.issue_id == self.database_id)
            .order_by(IssueCommentModel.created_at.asc())
        )
        all_comments = result.scalars().all()

        count_query = (
            select(sa_func.count())
            .select_from(IssueCommentModel)
            .where(IssueCommentModel.issue_id == self.database_id)
        )
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0

        return build_connection(
            all_comments, comment_from_model, total,
            first=first, after=after, last=last, before=before,
        )

    @strawberry.field
    def closed_by_pull_requests_references(
        self,
        first: Optional[int] = 10,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
    ) -> Connection[Annotated["PullRequest", strawberry.lazy("app.graphql.types.pull_request")]]:
        return empty_connection()

    @strawberry.field
    def project_cards(self) -> Connection[ProjectCardStub]:
        return empty_connection()

    @strawberry.field
    def project_items(
        self,
        first: Optional[int] = 10,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
    ) -> Connection[ProjectV2Stub]:
        return empty_connection()


def issue_from_model(issue) -> Issue:
    """Convert a SQLAlchemy Issue model to an Issue Strawberry type."""
    from app.config import settings
    base_url = settings.BASE_URL

    state = IssueState.OPEN if issue.state.lower() == "open" else IssueState.CLOSED

    # Build URL from repo full_name and issue number
    url = ""
    if hasattr(issue, 'repository') and issue.repository:
        url = f"{base_url}/{issue.repository.full_name}/issues/{issue.number}"
    else:
        url = f"{base_url}/issues/{issue.number}"

    return Issue(
        database_id=issue.id,
        number=issue.number,
        title=issue.title,
        body=issue.body,
        state=state,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
        closed_at=issue.closed_at,
        locked=issue.locked,
        _user_id=issue.user_id,
        _repo_id=issue.repo_id,
        _milestone_id=issue.milestone_id,
        _url=url,
        _state_reason=getattr(issue, 'state_reason', None),
    )

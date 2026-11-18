"""Strawberry GraphQL types for GitHub issues."""

from datetime import datetime
from typing import Annotated, Optional

import strawberry
from sqlalchemy import func as sa_func
from sqlalchemy import select
from strawberry.types import Info

from app.graphql.connections import Connection, build_connection
from app.graphql.types.enums import IssueState, IssueStateReason
from app.graphql.types.minimizable import Minimizable
from app.graphql.types.repository import (
    Label,
    MilestoneType,
    label_from_model,
    milestone_from_model,
)
from app.graphql.types.stubs import (
    STANDARD_REACTION_GROUPS,
    ProjectCardStub,
    ProjectV2Stub,
    ReactionGroup,
    empty_connection,
)
from app.graphql.types.user import Actor, GitHubUser, _node_id, actor_from_model, user_from_model


@strawberry.type
class IssueComment(Minimizable):
    """A comment on an issue."""

    _MINIMIZE_KIND = "IssueComment"
    database_id: int
    body: str
    created_at: datetime = strawberry.UNSET
    updated_at: datetime = strawberry.UNSET
    _user_id: strawberry.Private[int] = 0
    _minimized_reason: strawberry.Private[Optional[str]] = None

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("IssueComment", self.database_id)

    @strawberry.field
    async def author(self, info: Info) -> Optional[Actor]:
        from app.models.user import User
        db = info.context["db"]
        result = await db.execute(select(User).where(User.id == self._user_id))
        user = result.scalar_one_or_none()
        if user:
            return actor_from_model(user)
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
        _minimized_reason=getattr(comment, "minimized_reason", None),
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
    def state_reason(self) -> Optional[IssueStateReason]:
        # Stored as REST reports it ("not_planned"); GraphQL has the enum.
        try:
            return IssueStateReason((self._state_reason or "").upper())
        except ValueError:
            return None

    @strawberry.field
    def reaction_groups(self) -> list[ReactionGroup]:
        return list(STANDARD_REACTION_GROUPS)

    @strawberry.field
    async def author(self, info: Info) -> Optional[Actor]:
        from app.models.user import User
        db = info.context["db"]
        result = await db.execute(select(User).where(User.id == self._user_id))
        user = result.scalar_one_or_none()
        if user:
            return actor_from_model(user)
        return None

    @strawberry.field
    async def milestone(self, info: Info) -> Optional[MilestoneType]:
        if not self._milestone_id:
            return None
        from app.models.milestone import Milestone
        db = info.context["db"]
        result = await db.execute(
            # Only the issue's own repository's milestone: an association made
            # before inputs were validated may name another repository's.
            select(Milestone).where(Milestone.id == self._milestone_id, Milestone.repo_id == self._repo_id)
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
        from app.models.issue import IssueLabel
        from app.models.label import Label as LabelModel

        db = info.context["db"]
        result = await db.execute(
            select(LabelModel)
            .join(IssueLabel, LabelModel.id == IssueLabel.label_id)
            .where(IssueLabel.issue_id == self.database_id, LabelModel.repo_id == self._repo_id)
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
        from app.models.issue import IssueAssignee
        from app.models.user import User

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
    async def closed_by_pull_requests_references(
        self,
        info: Info,
        first: Optional[int] = 10,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
        include_closed_prs: Optional[bool] = False,
        order_by_state: Optional[bool] = False,
        user_linked_only: Optional[bool] = False,
    ) -> Connection[Annotated["PullRequest", strawberry.lazy("app.graphql.types.pull_request")]]:
        """Pull requests whose description closes this issue. Pull requests
        closed without merging are left out unless ``includeClosedPrs``.
        Links made by hand in the web UI are not emulated, so
        ``userLinkedOnly`` finds none."""
        if user_linked_only:
            return empty_connection()
        from app.graphql.authz import visible
        from app.graphql.types.pull_request import pull_request_from_model
        from app.models.issue import Issue as IssueModel
        from app.services.closing_issue_service import resolve_closing_pull_requests

        db = info.context["db"]
        issue = (
            await db.execute(select(IssueModel).where(IssueModel.id == self.database_id))
        ).scalar_one_or_none()
        if issue is None:
            return empty_connection()
        pulls = [
            pull
            for pull in await resolve_closing_pull_requests(db, issue)
            if (include_closed_prs or pull.merged or pull.issue.state == "open")
            and await visible(info, pull.repository)
        ]
        if order_by_state:
            # Open first, then merged, then closed; oldest first within each.
            pulls.sort(key=lambda pull: 0 if pull.issue.state == "open" else 1 if pull.merged else 2)
        return build_connection(
            pulls, pull_request_from_model, len(pulls),
            first=first, after=after, last=last, before=before,
        )

    @strawberry.field
    def sub_issues(
        self,
        first: Optional[int] = 10,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
    ) -> Connection[Annotated["Issue", strawberry.lazy("app.graphql.types.issue")]]:
        """GitHub's sub-issues. Not modelled here, so always empty: clients
        that ask whether an issue has sub-issues get totalCount 0 instead
        of a validation error for an unknown field."""
        return empty_connection()

    @strawberry.field
    def parent(self) -> Optional[Annotated["Issue", strawberry.lazy("app.graphql.types.issue")]]:
        """The issue this one is a sub-issue of; never set here."""
        return None

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

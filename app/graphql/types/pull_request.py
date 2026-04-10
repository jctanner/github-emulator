"""Strawberry GraphQL types for GitHub pull requests."""

from datetime import datetime
from enum import Enum
from typing import Annotated, Optional

import strawberry
from strawberry.types import Info
from sqlalchemy import select, func as sa_func

from app.graphql.connections import Connection, build_connection
from app.graphql.types.user import GitHubUser, user_from_model, _node_id
from app.graphql.types.enums import IssueState
from app.graphql.types.repository import Label, MilestoneType, label_from_model, milestone_from_model
from app.graphql.types.issue import IssueComment, comment_from_model
from app.graphql.types.stubs import (
    ReactionGroup,
    STANDARD_REACTION_GROUPS,
    StatusCheckRollup,
    AutoMergeRequest,
    ReviewRequestStub,
    ProjectCardStub,
    ProjectV2Stub,
    empty_connection,
)


# PullRequest uses IssueState (OPEN, CLOSED, MERGED) imported from enums
PRState = IssueState


@strawberry.enum
class MergeableState(Enum):
    """Whether a pull request can be merged."""
    MERGEABLE = "MERGEABLE"
    CONFLICTING = "CONFLICTING"
    UNKNOWN = "UNKNOWN"


@strawberry.type
class ReviewType:
    """A pull request review."""
    database_id: int
    body: Optional[str] = None
    state: str = ""
    submitted_at: Optional[datetime] = None
    created_at: datetime = strawberry.UNSET
    _user_id: strawberry.Private[int] = 0

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("PullRequestReview", self.database_id)

    @strawberry.field
    async def author(self, info: Info) -> Optional[GitHubUser]:
        from app.models.user import User
        db = info.context["db"]
        result = await db.execute(select(User).where(User.id == self._user_id))
        user = result.scalar_one_or_none()
        if user:
            return user_from_model(user)
        return None


def review_from_model(review) -> ReviewType:
    """Convert a SQLAlchemy Review model to a ReviewType Strawberry type."""
    return ReviewType(
        database_id=review.id,
        body=review.body,
        state=review.state,
        submitted_at=review.submitted_at,
        created_at=review.created_at,
        _user_id=review.user_id,
    )


@strawberry.type
class Commit:
    """A Git commit within a pull request."""
    oid: str
    message: str = ""


@strawberry.type
class PullRequest:
    """A GitHub pull request."""
    database_id: int
    number: int
    title: str
    body: Optional[str] = None
    state: PRState = PRState.OPEN
    created_at: datetime = strawberry.UNSET
    updated_at: datetime = strawberry.UNSET
    closed_at: Optional[datetime] = None
    merged_at: Optional[datetime] = None
    merged: bool = False
    mergeable: MergeableState = MergeableState.UNKNOWN
    head_ref_name: str = ""
    base_ref_name: str = ""
    draft: bool = False

    # Private fields for lazy resolution
    _user_id: strawberry.Private[int] = 0
    _issue_id: strawberry.Private[int] = 0
    _pr_id: strawberry.Private[int] = 0
    _repo_id: strawberry.Private[int] = 0
    _url: strawberry.Private[str] = ""
    _head_sha: strawberry.Private[str] = ""
    _base_sha: strawberry.Private[str] = ""
    _merge_commit_sha: strawberry.Private[Optional[str]] = None
    _head_repo_id: strawberry.Private[Optional[int]] = None
    _merged_by_id: strawberry.Private[Optional[int]] = None

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("PullRequest", self.database_id)

    @strawberry.field
    def is_draft(self) -> bool:
        return self.draft

    @strawberry.field
    def url(self) -> str:
        return self._url

    @strawberry.field
    def closed(self) -> bool:
        return self.state in (PRState.CLOSED, PRState.MERGED)

    @strawberry.field
    def additions(self) -> int:
        return 0

    @strawberry.field
    def deletions(self) -> int:
        return 0

    @strawberry.field
    def changed_files(self) -> int:
        return 0

    @strawberry.field
    def head_ref_oid(self) -> str:
        return self._head_sha

    @strawberry.field
    def base_ref_oid(self) -> str:
        return self._base_sha

    @strawberry.field
    def is_cross_repository(self) -> bool:
        return False

    @strawberry.field
    def review_decision(self) -> Optional[str]:
        return None

    @strawberry.field
    def merge_state_status(self) -> str:
        if self.draft:
            return "DRAFT"
        if self.merged:
            return "CLEAN"
        return "UNKNOWN"

    @strawberry.field
    def maintainer_can_modify(self) -> bool:
        return False

    @strawberry.field
    def full_database_id(self) -> int:
        return self.database_id

    @strawberry.field
    def status_check_rollup(self) -> Optional[StatusCheckRollup]:
        return None

    @strawberry.field
    def auto_merge_request(self) -> Optional[AutoMergeRequest]:
        return None

    @strawberry.field
    def reaction_groups(self) -> list[ReactionGroup]:
        return list(STANDARD_REACTION_GROUPS)

    @strawberry.field
    def merge_commit(self) -> Optional[Commit]:
        if self._merge_commit_sha:
            return Commit(oid=self._merge_commit_sha, message="")
        return None

    @strawberry.field
    def potential_merge_commit(self) -> Optional[Commit]:
        if self._merge_commit_sha:
            return Commit(oid=self._merge_commit_sha, message="")
        return None

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
    async def head_repository(self, info: Info) -> Optional[Annotated["Repository", strawberry.lazy("app.graphql.types.repository")]]:
        if not self._head_repo_id:
            return None
        from app.models.repository import Repository as RepoModel
        from app.graphql.types.repository import repository_from_model
        db = info.context["db"]
        result = await db.execute(
            select(RepoModel).where(RepoModel.id == self._head_repo_id)
        )
        repo = result.scalar_one_or_none()
        if repo:
            return repository_from_model(repo)
        return None

    @strawberry.field
    async def head_repository_owner(self, info: Info) -> Optional[GitHubUser]:
        if not self._head_repo_id:
            return None
        from app.models.repository import Repository as RepoModel
        from app.models.user import User
        db = info.context["db"]
        result = await db.execute(
            select(RepoModel).where(RepoModel.id == self._head_repo_id)
        )
        repo = result.scalar_one_or_none()
        if repo:
            user_result = await db.execute(
                select(User).where(User.id == repo.owner_id)
            )
            user = user_result.scalar_one_or_none()
            if user:
                return user_from_model(user)
        return None

    @strawberry.field
    async def merged_by(self, info: Info) -> Optional[GitHubUser]:
        if not self._merged_by_id:
            return None
        from app.models.user import User
        db = info.context["db"]
        result = await db.execute(
            select(User).where(User.id == self._merged_by_id)
        )
        user = result.scalar_one_or_none()
        if user:
            return user_from_model(user)
        return None

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
            .where(IssueAssignee.issue_id == self._issue_id)
        )
        all_assignees = result.scalars().all()
        return build_connection(
            all_assignees, user_from_model, len(all_assignees),
            first=first, after=after, last=last, before=before,
        )

    @strawberry.field
    async def milestone(self, info: Info) -> Optional[MilestoneType]:
        from app.models.issue import Issue as IssueModel
        from app.models.milestone import Milestone
        db = info.context["db"]

        issue_result = await db.execute(
            select(IssueModel).where(IssueModel.id == self._issue_id)
        )
        issue = issue_result.scalar_one_or_none()
        if not issue or not issue.milestone_id:
            return None

        ms_result = await db.execute(
            select(Milestone).where(Milestone.id == issue.milestone_id)
        )
        ms = ms_result.scalar_one_or_none()
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
            .where(IssueLabel.issue_id == self._issue_id)
            .order_by(LabelModel.name.asc())
        )
        all_labels = result.scalars().all()
        return build_connection(
            all_labels, label_from_model, len(all_labels),
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
            .where(IssueCommentModel.issue_id == self._issue_id)
            .order_by(IssueCommentModel.created_at.asc())
        )
        all_comments = result.scalars().all()
        return build_connection(
            all_comments, comment_from_model, len(all_comments),
            first=first, after=after, last=last, before=before,
        )

    @strawberry.field
    async def commits(
        self,
        info: Info,
        first: Optional[int] = 10,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
    ) -> Connection[Commit]:
        """Return a connection of commits. Currently returns the head SHA as a
        single commit since the emulator does not track individual commits in
        the database."""
        from app.models.pull_request import PullRequest as PRModel
        db = info.context["db"]
        result = await db.execute(
            select(PRModel).where(PRModel.id == self._pr_id)
        )
        pr = result.scalar_one_or_none()
        commits = []
        if pr:
            commits = [Commit(oid=pr.head_sha, message="")]
        return build_connection(
            commits, lambda c: c, len(commits),
            first=first, after=after, last=last, before=before,
        )

    @strawberry.field
    async def reviews(
        self,
        info: Info,
        first: Optional[int] = 10,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
    ) -> Connection[ReviewType]:
        from app.models.review import Review

        db = info.context["db"]
        result = await db.execute(
            select(Review)
            .where(Review.pull_request_id == self._pr_id)
            .order_by(Review.created_at.asc())
        )
        all_reviews = result.scalars().all()
        return build_connection(
            all_reviews, review_from_model, len(all_reviews),
            first=first, after=after, last=last, before=before,
        )

    @strawberry.field
    def closing_issues_references(
        self,
        first: Optional[int] = 10,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
    ) -> Connection[Annotated["Issue", strawberry.lazy("app.graphql.types.issue")]]:
        return empty_connection()

    @strawberry.field
    def review_requests(self) -> Connection[ReviewRequestStub]:
        return empty_connection()

    @strawberry.field
    def files(self) -> Connection[Commit]:
        return empty_connection()

    @strawberry.field
    def project_cards(self) -> Connection[ProjectCardStub]:
        return empty_connection()

    @strawberry.field
    def project_items(self) -> Connection[ProjectV2Stub]:
        return empty_connection()


def pull_request_from_model(pr) -> PullRequest:
    """Convert a SQLAlchemy PullRequest model (with joined issue) to a
    PullRequest Strawberry type.

    The PullRequest model has a relationship to Issue which carries the
    shared fields like title, body, state, etc.
    """
    from app.config import settings
    base_url = settings.BASE_URL

    issue = pr.issue

    # Determine GraphQL state
    if pr.merged:
        state = PRState.MERGED
    elif issue.state == "closed":
        state = PRState.CLOSED
    else:
        state = PRState.OPEN

    # Determine mergeable state
    if pr.mergeable is True:
        mergeable = MergeableState.MERGEABLE
    elif pr.mergeable is False:
        mergeable = MergeableState.CONFLICTING
    else:
        mergeable = MergeableState.UNKNOWN

    # Build URL
    url = ""
    if hasattr(issue, 'repository') and issue.repository:
        url = f"{base_url}/{issue.repository.full_name}/pull/{issue.number}"
    else:
        url = f"{base_url}/pull/{issue.number}"

    return PullRequest(
        database_id=pr.id,
        number=issue.number,
        title=issue.title,
        body=issue.body,
        state=state,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
        closed_at=issue.closed_at,
        merged_at=pr.merged_at,
        merged=pr.merged,
        mergeable=mergeable,
        head_ref_name=pr.head_ref,
        base_ref_name=pr.base_ref,
        draft=pr.draft,
        _user_id=issue.user_id,
        _issue_id=issue.id,
        _pr_id=pr.id,
        _repo_id=pr.repo_id,
        _url=url,
        _head_sha=pr.head_sha or "",
        _base_sha=pr.base_sha or "",
        _merge_commit_sha=pr.merge_commit_sha,
        _head_repo_id=pr.head_repo_id,
        _merged_by_id=pr.merged_by_id,
    )

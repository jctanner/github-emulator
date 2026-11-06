"""GitHub's ``Minimizable`` interface: comments that can be hidden.

``minimizeComment`` hides an issue comment, a pull request review comment, or
a pull request review, and the hidden item then reports ``isMinimized`` and
``minimizedReason``. Fullsend's review agent hides its own stale reviews and
summaries this way (github-emulator#20).

Implementers keep the stored reason in ``_minimized_reason`` and their row id
in ``database_id``, and name their kind in ``_MINIMIZE_KIND``. Whether the
viewer may minimize is ``_can_minimize``, which an implementer that already
knows the answer overrides.
"""

from dataclasses import dataclass
from typing import ClassVar, Optional

import strawberry
from sqlalchemy import select
from strawberry.types import Info


@dataclass
class Subject:
    """A minimizable row, its repository, and the App permissions that
    govern hiding it."""

    kind: str
    row: object
    repo: object
    scopes: tuple[str, ...]


async def load_subject(db, kind: str, db_id: int) -> Optional[Subject]:
    """The minimizable item behind a node id, or None."""
    from app.models.comment import IssueComment, PRReviewComment
    from app.models.issue import Issue
    from app.models.pull_request import PullRequest
    from app.models.repository import Repository
    from app.models.review import Review

    if kind == "IssueComment":
        row = (await db.execute(select(IssueComment).where(IssueComment.id == db_id))).scalar_one_or_none()
        issue_id = row.issue_id if row else None
    elif kind in ("PullRequestReviewComment", "PullRequestReview"):
        model = PRReviewComment if kind == "PullRequestReviewComment" else Review
        row = (await db.execute(select(model).where(model.id == db_id))).scalar_one_or_none()
        pr = (
            await db.execute(select(PullRequest).where(PullRequest.id == row.pull_request_id))
        ).scalar_one_or_none() if row else None
        issue_id = pr.issue_id if pr else None
    else:
        return None
    if row is None or issue_id is None:
        return None
    issue = (await db.execute(select(Issue).where(Issue.id == issue_id))).scalar_one_or_none()
    repo = (
        await db.execute(select(Repository).where(Repository.id == issue.repo_id))
    ).scalar_one_or_none() if issue else None
    if repo is None:
        return None
    if kind == "IssueComment":
        # A pull request's conversation comment is an issue comment: either
        # App permission covers it, as on GitHub.
        is_pull = (
            await db.execute(select(PullRequest.id).where(PullRequest.issue_id == issue.id))
        ).scalar_one_or_none() is not None
        scopes = ("issues", "pull_requests") if is_pull else ("issues",)
    else:
        scopes = ("pull_requests",)
    return Subject(kind=kind, row=row, repo=repo, scopes=scopes)


async def can_minimize(info: Info, subject: Subject) -> bool:
    """Hiding a comment takes write access to its repository, as on GitHub,
    under the credential's own grant (see ``app.graphql.authz``)."""
    from app.graphql.authz import may_write

    return await may_write(info, info.context["db"], subject.repo, subject.scopes)


@strawberry.interface
class Minimizable:
    """An entity that can be minimized (hidden)."""

    _MINIMIZE_KIND: ClassVar[str] = ""

    async def _can_minimize(self, info: Info) -> bool:
        subject = await load_subject(info.context["db"], self._MINIMIZE_KIND, self.database_id)
        return subject is not None and await can_minimize(info, subject)

    @strawberry.field
    def is_minimized(self) -> bool:
        return getattr(self, "_minimized_reason", None) is not None

    @strawberry.field
    def minimized_reason(self) -> Optional[str]:
        return getattr(self, "_minimized_reason", None)

    @strawberry.field
    async def viewer_can_minimize(self, info: Info) -> bool:
        return await self._can_minimize(info)

    @strawberry.field
    async def viewer_can_unminimize(self, info: Info) -> bool:
        return getattr(self, "_minimized_reason", None) is not None and await self._can_minimize(info)

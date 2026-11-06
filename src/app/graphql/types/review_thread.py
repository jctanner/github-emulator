"""GraphQL pull request review threads and review comments.

GitHub exposes review threads, and whether each is resolved, only through
GraphQL: ``PullRequest.reviewThreads`` is a connection of
``PullRequestReviewThread``, each a top-level review comment plus its replies.
A review bot reads them, resolves the threads it opened that are now fixed,
and hides its outdated summaries with ``minimizeComment`` (Fullsend's review
agent does all three; github-emulator#20).

The emulator builds threads from ``PRReviewComment`` rows: a comment with no
``in_reply_to_id`` is a thread's root, and every reply is followed back to its
root. The resolved state is stored on the root comment.

Whether a comment is outdated needs git, so it is worked out only for the
threads and comments a query actually returns, once per comment per request.
"""

import difflib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import strawberry
from sqlalchemy import select
from strawberry.types import Info

from app.graphql.connections import Connection, build_connection
from app.graphql.types.minimizable import Minimizable
from app.graphql.types.user import GitHubUser, _node_id, user_from_model

# ``minimizeComment`` takes ``ReportedContentClassifiers``; the comment then
# reports ``minimizedReason`` in a different case and spelling, as on GitHub.
MINIMIZED_REASONS = {
    "ABUSE": "abuse",
    "OFF_TOPIC": "off-topic",
    "OUTDATED": "outdated",
    "RESOLVED": "resolved",
    "DUPLICATE": "duplicate",
    "SPAM": "spam",
}

# The App permission a pull request's review conversation is written under.
PULL_REQUEST_SCOPES = ("pull_requests",)


@strawberry.enum
class DiffSide(Enum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"


def map_line(before: bytes, after: bytes, line: int) -> Optional[int]:
    """Where line ``line`` of ``before`` is in ``after``, or None if it changed.

    The line is followed through the change the way a diff does: it keeps its
    place in an unchanged run of lines, so lines added or removed above it
    move it rather than outdating it.
    """
    old, new = before.splitlines(), after.splitlines()
    index = line - 1
    if index < 0 or index >= len(old):
        return None
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, _j2 in matcher.get_opcodes():
        if tag == "equal" and i1 <= index < i2:
            return j1 + (index - i1) + 1
    return None


@dataclass
class ThreadContext:
    """Per-request state shared by a pull request's threads and comments."""

    repo_full_name: str
    pr_number: int
    head_sha: str
    disk_path: Optional[str]
    viewer_id: Optional[int]
    viewer_can_moderate: bool
    viewer_is_pr_author: bool
    viewer_is_integration: bool
    _blobs: dict = field(default_factory=dict)
    _locations: dict = field(default_factory=dict)

    async def _blob(self, ref: str, path: str) -> Optional[bytes]:
        from app.git.bare_repo import get_file_content

        key = (ref, path)
        if key not in self._blobs:
            self._blobs[key] = await get_file_content(self.disk_path, ref, path) if self.disk_path else None
        return self._blobs[key]

    async def location(self, comment) -> tuple[bool, Optional[int]]:
        """``(outdated, current line)`` for a review comment at the head.

        A comment on the head commit is current. Otherwise a new-side (RIGHT)
        line comment is followed to the head with :func:`map_line`, and is
        outdated when its line changed. A file comment, or an old-side (LEFT)
        comment, is outdated when its file differs at the head; GitHub would
        follow a LEFT comment through the base, which the emulator does not.
        """
        if comment.id in self._locations:
            return self._locations[comment.id]
        commit = comment.commit_id or ""
        if not commit or commit == self.head_sha:
            result = (False, comment.line)
        elif not self.head_sha or not self.disk_path:
            result = (True, None)
        else:
            before = await self._blob(commit, comment.path)
            after = await self._blob(self.head_sha, comment.path)
            if before is None or after is None:
                result = (before is not after, None if before is not after else comment.line)
            elif before == after:
                result = (False, comment.line)
            elif (comment.side or "RIGHT") == "RIGHT" and comment.line and comment.subject_type != "file":
                mapped = map_line(before, after, comment.line)
                result = (mapped is None, mapped)
            else:
                result = (True, None)
        self._locations[comment.id] = result
        return result


async def thread_context(info: Info, pr_id: int) -> Optional[ThreadContext]:
    """The context for a pull request's threads, or None when the caller may
    not see the pull request's repository."""
    from app.graphql.authz import is_integration, may_read, may_write
    from app.models.issue import Issue
    from app.models.pull_request import PullRequest
    from app.models.repository import Repository

    db = info.context["db"]
    viewer = info.context.get("user")
    pr = (await db.execute(select(PullRequest).where(PullRequest.id == pr_id))).scalar_one_or_none()
    issue = (await db.execute(select(Issue).where(Issue.id == pr.issue_id))).scalar_one_or_none() if pr else None
    repo = (
        await db.execute(select(Repository).where(Repository.id == issue.repo_id))
    ).scalar_one_or_none() if issue else None
    if repo is None or not await may_read(info, db, repo, PULL_REQUEST_SCOPES):
        return None
    return ThreadContext(
        repo_full_name=repo.full_name,
        pr_number=issue.number,
        head_sha=pr.head_sha or "",
        disk_path=repo.disk_path,
        viewer_id=viewer.id if viewer else None,
        viewer_can_moderate=await may_write(info, db, repo, PULL_REQUEST_SCOPES),
        viewer_is_pr_author=bool(viewer and issue.user_id == viewer.id),
        viewer_is_integration=is_integration(info),
    )


async def _user(info: Info, user_id: Optional[int]) -> Optional[GitHubUser]:
    if user_id is None:
        return None
    from app.models.user import User

    user = (await info.context["db"].execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    return user_from_model(user) if user else None


@strawberry.type
class PullRequestReviewComment(Minimizable):
    """A review comment on a line or file of a pull request."""

    _MINIMIZE_KIND = "PullRequestReviewComment"

    database_id: int
    body: str
    path: str
    created_at: datetime = strawberry.UNSET
    updated_at: datetime = strawberry.UNSET
    original_line: Optional[int] = None
    diff_hunk: str = ""
    _row: strawberry.Private[object] = None
    _user_id: strawberry.Private[int] = 0
    _minimized_reason: strawberry.Private[Optional[str]] = None
    _ctx: strawberry.Private[Optional[ThreadContext]] = None

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("PullRequestReviewComment", self.database_id)

    @strawberry.field
    async def outdated(self) -> bool:
        return self._ctx is not None and (await self._ctx.location(self._row))[0]

    @strawberry.field
    async def line(self) -> Optional[int]:
        if self._ctx is None:
            return self.original_line
        return (await self._ctx.location(self._row))[1]

    @strawberry.field
    async def author(self, info: Info) -> Optional[GitHubUser]:
        return await _user(info, self._user_id)

    @strawberry.field
    def url(self) -> str:
        from app.config import settings

        if self._ctx is None:
            return ""
        base = settings.BASE_URL.rstrip("/")
        return f"{base}/{self._ctx.repo_full_name}/pull/{self._ctx.pr_number}#discussion_r{self.database_id}"

    @strawberry.field
    def viewer_did_author(self) -> bool:
        return self._ctx is not None and self._ctx.viewer_id == self._user_id

    async def _can_minimize(self, info: Info) -> bool:
        # Every comment in a thread shares the pull request's answer.
        if self._ctx is not None:
            return self._ctx.viewer_can_moderate
        return await Minimizable._can_minimize(self, info)


def review_comment_from_model(comment, ctx: Optional[ThreadContext] = None) -> PullRequestReviewComment:
    return PullRequestReviewComment(
        database_id=comment.id,
        body=comment.body,
        path=comment.path,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
        original_line=comment.line,
        diff_hunk=comment.diff_hunk or "",
        _row=comment,
        _user_id=comment.user_id,
        _minimized_reason=comment.minimized_reason,
        _ctx=ctx,
    )


@strawberry.type
class PullRequestReviewThread:
    """A review comment and its replies, resolvable as one conversation."""

    path: str
    is_resolved: bool
    diff_side: DiffSide
    original_line: Optional[int] = None
    # The emulator stores single-line comments only, as GitHub reports a
    # thread that does not span lines: the start fields are null.
    start_line: Optional[int] = None
    original_start_line: Optional[int] = None
    start_diff_side: Optional[DiffSide] = None
    is_collapsed: bool = False
    _root: strawberry.Private[object] = None
    _resolved_by_id: strawberry.Private[Optional[int]] = None
    # PRReviewComment rows, oldest first; the first is the root.
    _comments: strawberry.Private[Optional[list]] = None
    _ctx: strawberry.Private[Optional[ThreadContext]] = None

    @strawberry.field
    def id(self) -> strawberry.ID:
        return _node_id("PullRequestReviewThread", self._root.id)

    @strawberry.field
    async def is_outdated(self) -> bool:
        return (await self._ctx.location(self._root))[0]

    @strawberry.field
    async def line(self) -> Optional[int]:
        return (await self._ctx.location(self._root))[1]

    @strawberry.field
    async def resolved_by(self, info: Info) -> Optional[GitHubUser]:
        return await _user(info, self._resolved_by_id)

    def _viewer_may_resolve(self) -> bool:
        ctx = self._ctx
        if ctx is None or ctx.viewer_id is None:
            return False
        # GitHub lets the pull request's author resolve too; an integration
        # token acts only through its own grant.
        return ctx.viewer_can_moderate or (ctx.viewer_is_pr_author and not ctx.viewer_is_integration)

    @strawberry.field
    def viewer_can_resolve(self) -> bool:
        return not self.is_resolved and self._viewer_may_resolve()

    @strawberry.field
    def viewer_can_unresolve(self) -> bool:
        return self.is_resolved and self._viewer_may_resolve()

    @strawberry.field
    def viewer_can_reply(self) -> bool:
        return self._ctx is not None and self._ctx.viewer_id is not None

    @strawberry.field
    def comments(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        last: Optional[int] = None,
        before: Optional[str] = None,
        skip: Optional[int] = None,
    ) -> Connection[PullRequestReviewComment]:
        items = self._comments or []
        items = items[skip:] if skip else items
        return build_connection(
            items,
            lambda row: review_comment_from_model(row, self._ctx),
            len(items),
            first=first, after=after, last=last, before=before,
        )


def thread_root_id(comment, by_id: dict) -> int:
    """Follow ``in_reply_to_id`` back to the thread's top-level comment."""
    seen = set()
    while comment.in_reply_to_id and comment.in_reply_to_id in by_id and comment.id not in seen:
        seen.add(comment.id)
        comment = by_id[comment.in_reply_to_id]
    return comment.id


async def build_threads(info: Info, pr_id: int) -> list[PullRequestReviewThread]:
    """Every review thread on a pull request, oldest first.

    Empty when the caller may not see the repository, or (for an
    installation token) may not read its pull requests.
    """
    from app.models.comment import PRReviewComment

    ctx = await thread_context(info, pr_id)
    if ctx is None:
        return []
    rows = (
        await info.context["db"].execute(
            select(PRReviewComment)
            .where(PRReviewComment.pull_request_id == pr_id)
            .order_by(PRReviewComment.created_at.asc(), PRReviewComment.id.asc())
        )
    ).scalars().all()
    by_id = {row.id: row for row in rows}
    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(thread_root_id(row, by_id), []).append(row)
    threads = []
    for root_id, members in grouped.items():
        root = by_id[root_id]
        threads.append(PullRequestReviewThread(
            path=root.path,
            is_resolved=root.thread_resolved_at is not None,
            diff_side=DiffSide.LEFT if (root.side or "RIGHT") == "LEFT" else DiffSide.RIGHT,
            original_line=root.line,
            _root=root,
            _resolved_by_id=root.thread_resolved_by_id,
            _comments=members,
            _ctx=ctx,
        ))
    return threads

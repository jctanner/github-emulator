"""GraphQL mutation resolvers."""

import asyncio
import base64
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Optional, Union

import strawberry
from strawberry.types import Info
from sqlalchemy import select

from app.graphql.types.user import GitHubUser, user_from_model
from app.graphql.types.repository import Repository, repository_from_model
from app.graphql.types.issue import Issue, issue_from_model, IssueComment, comment_from_model
from app.graphql.types.pull_request import Commit, PullRequest, pull_request_from_model
from app.graphql.types.minimizable import Minimizable
from app.graphql.types.review_thread import MINIMIZED_REASONS, PullRequestReviewThread
from graphql import GraphQLError


def _decode_node_id(node_id: str) -> int:
    """Decode a GitHub-style base64 node ID (e.g. 'UmVwb3NpdG9yeTox') to a
    database integer ID.  Falls back to ``int(node_id)`` for plain numeric IDs."""
    try:
        return int(node_id)
    except (ValueError, TypeError):
        pass
    try:
        decoded = base64.b64decode(node_id).decode("utf-8")
        # Format is "TypeName:id"
        _, _, id_str = decoded.partition(":")
        return int(id_str)
    except Exception:
        raise ValueError(f"Invalid node ID: {node_id}")


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------

@strawberry.input
class CreateIssueInput:
    repository_id: strawberry.ID
    title: str
    body: Optional[str] = None
    assignee_ids: Optional[list[strawberry.ID]] = None
    label_ids: Optional[list[strawberry.ID]] = None
    milestone_id: Optional[strawberry.ID] = None
    client_mutation_id: Optional[str] = None


@strawberry.input
class UpdateIssueInput:
    id: strawberry.ID
    title: Optional[str] = None
    body: Optional[str] = None
    state: Optional[str] = None
    milestone_id: Optional[strawberry.ID] = None
    label_ids: Optional[list[strawberry.ID]] = None
    assignee_ids: Optional[list[strawberry.ID]] = None
    client_mutation_id: Optional[str] = None


@strawberry.input
class CloseIssueInput:
    issue_id: strawberry.ID
    state_reason: Optional[str] = None
    client_mutation_id: Optional[str] = None


@strawberry.input
class ReopenIssueInput:
    issue_id: strawberry.ID
    client_mutation_id: Optional[str] = None


@strawberry.input
class AddCommentInput:
    subject_id: strawberry.ID
    body: str
    client_mutation_id: Optional[str] = None


@strawberry.input
class CreatePullRequestInput:
    repository_id: strawberry.ID
    title: str
    body: Optional[str] = None
    head_ref_name: str = ""
    base_ref_name: str = ""
    draft: bool = False
    maintainer_can_modify: bool = True
    client_mutation_id: Optional[str] = None


@strawberry.input
class MergePullRequestInput:
    pull_request_id: strawberry.ID
    commit_headline: Optional[str] = None
    commit_body: Optional[str] = None
    merge_method: Optional[str] = None  # MERGE, SQUASH, REBASE
    client_mutation_id: Optional[str] = None


@strawberry.input
class EnablePullRequestAutoMergeInput:
    pull_request_id: strawberry.ID
    merge_method: Optional[str] = None
    commit_headline: Optional[str] = None
    commit_body: Optional[str] = None
    client_mutation_id: Optional[str] = None


@strawberry.input
class DisablePullRequestAutoMergeInput:
    pull_request_id: strawberry.ID
    client_mutation_id: Optional[str] = None


@strawberry.input
class AddReactionInput:
    subject_id: strawberry.ID
    content: str  # "+1", "-1", "laugh", "confused", "heart", "hooray", "rocket", "eyes"
    client_mutation_id: Optional[str] = None


@strawberry.input
class CreateRepositoryInput:
    name: str
    owner_id: Optional[strawberry.ID] = None
    description: Optional[str] = None
    visibility: str = "public"
    has_issues_enabled: bool = True
    has_wiki_enabled: bool = True
    client_mutation_id: Optional[str] = None


@strawberry.input
class ClosePullRequestInput:
    pull_request_id: strawberry.ID
    client_mutation_id: Optional[str] = None


@strawberry.input
class ReopenPullRequestInput:
    pull_request_id: strawberry.ID
    client_mutation_id: Optional[str] = None


@strawberry.input
class UpdatePullRequestInput:
    pull_request_id: strawberry.ID
    title: Optional[str] = None
    body: Optional[str] = None
    base_ref_name: Optional[str] = None
    # `gh pr edit --add-assignee` sets assignees through this mutation rather
    # than the REST endpoint. Without the field the whole mutation is rejected
    # by the schema, so the assignment fails before it is attempted.
    assignee_ids: Optional[list[strawberry.ID]] = None
    client_mutation_id: Optional[str] = None


@strawberry.input
class AddLabelsToLabelableInput:
    labelable_id: strawberry.ID
    label_ids: list[strawberry.ID]
    client_mutation_id: Optional[str] = None


@strawberry.input
class CommittableBranch:
    branch_name: Optional[str] = None
    repository_name_with_owner: Optional[str] = None
    id: Optional[strawberry.ID] = None


@strawberry.input
class CommitMessage:
    headline: str
    body: Optional[str] = None


@strawberry.input
class FileAddition:
    path: str
    contents: str  # Base64String


@strawberry.input
class FileDeletion:
    path: str


@strawberry.input
class FileChanges:
    additions: Optional[list[FileAddition]] = None
    deletions: Optional[list[FileDeletion]] = None


@strawberry.input
class CreateCommitOnBranchInput:
    branch: CommittableBranch
    expected_head_oid: str  # GitObjectID
    message: CommitMessage
    file_changes: Optional[FileChanges] = None
    client_mutation_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Payload types
# ---------------------------------------------------------------------------

@strawberry.type
class CreateIssuePayload:
    issue: Optional[Issue] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class UpdateIssuePayload:
    issue: Optional[Issue] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class CloseIssuePayload:
    issue: Optional[Issue] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class ReopenIssuePayload:
    issue: Optional[Issue] = None
    client_mutation_id: Optional[str] = None


@strawberry.type(name="CommentEdge")
class AddCommentEdge:
    """An edge wrapping an IssueComment node."""
    node: Optional[IssueComment] = None
    cursor: Optional[str] = None


@strawberry.type
class AddCommentPayload:
    comment_edge: Optional[AddCommentEdge] = None
    subject: Optional[Issue] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class CreatePullRequestPayload:
    pull_request: Optional[PullRequest] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class MergePullRequestPayload:
    pull_request: Optional[PullRequest] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class EnablePullRequestAutoMergePayload:
    pull_request: Optional[PullRequest] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class DisablePullRequestAutoMergePayload:
    pull_request: Optional[PullRequest] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class ReactionType:
    """A reaction to a subject."""
    database_id: int
    content: str
    user: Optional[GitHubUser] = None
    created_at: Optional[datetime] = None


@strawberry.type
class AddReactionPayload:
    reaction: Optional[ReactionType] = None
    subject_id: Optional[strawberry.ID] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class CreateRepositoryPayload:
    repository: Optional[Repository] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class ClosePullRequestPayload:
    pull_request: Optional[PullRequest] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class ReopenPullRequestPayload:
    pull_request: Optional[PullRequest] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class UpdatePullRequestPayload:
    pull_request: Optional[PullRequest] = None
    client_mutation_id: Optional[str] = None


Labelable = Annotated[
    Union[Issue, PullRequest],
    strawberry.union("Labelable"),
]


@strawberry.type
class AddLabelsToLabelablePayload:
    labelable: Optional[Labelable] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class CreateCommitOnBranchPayload:
    commit: Optional[Commit] = None
    client_mutation_id: Optional[str] = None


# --- Review threads and minimized comments (github-emulator#20) ---

@strawberry.enum
class ReportedContentClassifiers(Enum):
    SPAM = "SPAM"
    ABUSE = "ABUSE"
    OFF_TOPIC = "OFF_TOPIC"
    OUTDATED = "OUTDATED"
    DUPLICATE = "DUPLICATE"
    RESOLVED = "RESOLVED"


@strawberry.input
class ResolveReviewThreadInput:
    thread_id: strawberry.ID
    client_mutation_id: Optional[str] = None


@strawberry.input
class UnresolveReviewThreadInput:
    thread_id: strawberry.ID
    client_mutation_id: Optional[str] = None


@strawberry.type
class ResolveReviewThreadPayload:
    thread: Optional[PullRequestReviewThread] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class UnresolveReviewThreadPayload:
    thread: Optional[PullRequestReviewThread] = None
    client_mutation_id: Optional[str] = None


@strawberry.input
class MinimizeCommentInput:
    subject_id: strawberry.ID
    classifier: ReportedContentClassifiers
    client_mutation_id: Optional[str] = None


@strawberry.input
class UnminimizeCommentInput:
    subject_id: strawberry.ID
    client_mutation_id: Optional[str] = None


@strawberry.type
class MinimizeCommentPayload:
    minimized_comment: Optional[Minimizable] = None
    client_mutation_id: Optional[str] = None


@strawberry.type
class UnminimizeCommentPayload:
    unminimized_comment: Optional[Minimizable] = None
    client_mutation_id: Optional[str] = None


def _decode_typed_node_id(node_id: str) -> tuple[str, int] | None:
    """Split a ``base64("Type:id")`` node id; ``None`` when it is not one."""
    try:
        kind, _, raw = base64.b64decode(str(node_id), validate=True).decode("utf-8").partition(":")
        return kind, int(raw)
    except Exception:
        return None


async def _review_thread_root(info: Info, thread_id: str, mutation: str):
    """The root comment of a thread the caller may resolve."""
    from app.graphql.authz import is_integration, may_read, may_write
    from app.graphql.types.review_thread import PULL_REQUEST_SCOPES
    from app.models.comment import PRReviewComment
    from app.models.issue import Issue
    from app.models.pull_request import PullRequest as PRModel
    from app.models.repository import Repository as RepoModel

    viewer = _require_auth(info)
    db = info.context["db"]
    decoded = _decode_typed_node_id(thread_id)
    root = None
    if decoded and decoded[0] == "PullRequestReviewThread":
        root = (
            await db.execute(select(PRReviewComment).where(PRReviewComment.id == decoded[1]))
        ).scalar_one_or_none()
    pr = (await db.execute(select(PRModel).where(PRModel.id == root.pull_request_id))).scalar_one_or_none() if root else None
    issue = (await db.execute(select(Issue).where(Issue.id == pr.issue_id))).scalar_one_or_none() if pr else None
    repo = (await db.execute(select(RepoModel).where(RepoModel.id == issue.repo_id))).scalar_one_or_none() if issue else None
    # A thread's id is its root comment's; a reply's id names no thread.
    # NOT_FOUND when the caller cannot see the repository at all; a grant that
    # does not cover review conversations is FORBIDDEN below, as on GitHub.
    if root is None or root.in_reply_to_id or repo is None or not await may_read(info, db, repo):
        raise _typed_error("NOT_FOUND", f"Could not resolve to a node with the global id of '{thread_id}'")
    # GitHub lets the pull request's author, and anyone who may write its
    # pull requests, resolve or unresolve a conversation. An installation or
    # job token acts only through its own grant.
    author = issue.user_id == viewer.id and not is_integration(info)
    if not author and not await may_write(info, db, repo, PULL_REQUEST_SCOPES):
        raise _typed_error(
            "FORBIDDEN",
            f"{viewer.login} does not have the correct permissions to execute `{mutation}`",
        )
    return root


async def _thread_payload(info: Info, root):
    """The thread of ``root``, as the thread query reports it."""
    from app.graphql.types.review_thread import DiffSide, PullRequestReviewThread, thread_context
    from app.models.comment import PRReviewComment

    ctx = await thread_context(info, root.pull_request_id)
    if ctx is None:
        return None
    replies = (
        await info.context["db"].execute(
            select(PRReviewComment).where(PRReviewComment.pull_request_id == root.pull_request_id)
        )
    ).scalars().all()
    from app.graphql.types.review_thread import thread_root_id

    by_id = {row.id: row for row in replies}
    members = sorted(
        (row for row in replies if thread_root_id(row, by_id) == root.id),
        key=lambda row: (row.created_at, row.id),
    )
    return PullRequestReviewThread(
        path=root.path,
        is_resolved=root.thread_resolved_at is not None,
        diff_side=DiffSide.LEFT if (root.side or "RIGHT") == "LEFT" else DiffSide.RIGHT,
        original_line=root.line,
        _root=root,
        _resolved_by_id=root.thread_resolved_by_id,
        _comments=members,
        _ctx=ctx,
    )


async def _minimize_subject(info: Info, subject_id: str, mutation: str):
    """The item behind a minimizable node id, after the permission check."""
    from app.graphql.authz import may_read
    from app.graphql.types.minimizable import can_minimize, load_subject

    _require_auth(info)
    viewer = info.context["user"]
    db = info.context["db"]
    decoded = _decode_typed_node_id(subject_id)
    subject = await load_subject(db, decoded[0], decoded[1]) if decoded else None
    if subject is None or not await may_read(info, db, subject.repo):
        raise _typed_error("NOT_FOUND", f"Could not resolve to a node with the global id of '{subject_id}'")
    if not await can_minimize(info, subject):
        raise _typed_error(
            "FORBIDDEN",
            f"{viewer.login} does not have the correct permissions to execute `{mutation}`",
        )
    return subject


async def _minimizable_from_row(info: Info, kind: str, row):
    """The minimized item, with the same fields its connections report."""
    from app.graphql.types.pull_request import review_from_model
    from app.graphql.types.review_thread import review_comment_from_model, thread_context

    if kind == "IssueComment":
        return comment_from_model(row)
    if kind == "PullRequestReview":
        return review_from_model(row)
    return review_comment_from_model(row, await thread_context(info, row.pull_request_id))


async def _visible_or_none(info: Info, row, repo_id):
    """``row`` if the caller may see the repository ``repo_id``, else None.

    A mutation's target in a repository the caller cannot see is treated as
    one that does not exist: the mutation fails with the same not-found error,
    writes nothing, and returns nothing from that repository.
    """
    from app.graphql.authz import visible
    from app.models.repository import Repository as RepoModel

    if row is None or repo_id is None:
        return None
    db = info.context["db"]
    repo = (await db.execute(select(RepoModel).where(RepoModel.id == repo_id))).scalar_one_or_none()
    return row if await visible(info, repo) else None


_ISSUES = ("issues",)
_PULL_REQUESTS = ("pull_requests",)


def _issue_scopes(issue) -> tuple[str, ...]:
    """The App permissions that let an integration write ``issue``: a pull
    request's backing issue (labels, comments, state) takes either."""
    if issue is not None and issue.pull_request is not None:
        return ("issues", "pull_requests")
    return _ISSUES


async def _writable_or_none(info: Info, row, repo_id, scopes: tuple[str, ...]):
    """``row`` for a mutation that writes it under ``scopes``.

    None when it does not exist or its repository is hidden from the caller
    (see ``_visible_or_none``). An installation or job token whose own grant
    does not cover ``scopes`` is refused with FORBIDDEN, as REST refuses it
    with 403 "Resource not accessible by integration".
    """
    from app.graphql.authz import integration_may_write
    from app.models.repository import Repository as RepoModel

    row = await _visible_or_none(info, row, repo_id)
    if row is None:
        return None
    db = info.context["db"]
    repo = (await db.execute(select(RepoModel).where(RepoModel.id == repo_id))).scalar_one()
    if not await integration_may_write(info, db, repo, scopes):
        raise _typed_error("FORBIDDEN", "Resource not accessible by integration")
    return row


async def _same_repository_ids(db, model, ids, repo_id: int, what: str) -> list[int]:
    """Decode ``ids`` and require each to name a ``model`` row in ``repo_id``.

    Labels and milestones are per repository; accepting another repository's
    would attach (and then reveal) its name and description here.
    """
    decoded = [_decode_node_id(item) for item in ids]
    if not decoded:
        return []
    found = set(
        (await db.execute(select(model.id).where(model.id.in_(decoded), model.repo_id == repo_id))).scalars().all()
    )
    missing = [str(item) for item in decoded if item not in found]
    if missing:
        raise ValueError(f"{what} with id {', '.join(missing)} not found")
    return decoded


def _typed_error(error_type: str, message: str) -> GraphQLError:
    """A GraphQL error carrying GitHub's top-level ``type`` (lifted from
    ``extensions`` by the router in ``app.main``)."""
    return GraphQLError(message, extensions={"type": error_type})


# ---------------------------------------------------------------------------
# Helper to resolve the authenticated user or raise
# ---------------------------------------------------------------------------

def _require_auth(info: Info):
    """Return the authenticated user from context or raise PermissionError."""
    user = info.context.get("user")
    if user is None:
        raise PermissionError("Authentication required")
    return user


# ---------------------------------------------------------------------------
# Mutation class
# ---------------------------------------------------------------------------

@strawberry.type
class Mutation:
    """Root mutation type for the GitHub GraphQL API emulator."""

    @strawberry.mutation
    async def add_labels_to_labelable(
        self, info: Info, input: AddLabelsToLabelableInput
    ) -> AddLabelsToLabelablePayload:
        """Add labels using GitHub's labelable mutation used by ``gh pr edit``."""
        from app.models.issue import Issue as IssueModel, IssueLabel
        from app.models.label import Label as LabelModel
        from app.models.pull_request import PullRequest as PRModel

        _require_auth(info)
        db = info.context["db"]

        raw_id = base64.b64decode(str(input.labelable_id)).decode("utf-8")
        type_name, _, id_str = raw_id.partition(":")
        labelable_id = int(id_str)

        issue = None
        pull_request = None
        if type_name.lower() == "pullrequest":
            result = await db.execute(
                select(PRModel).where(PRModel.id == labelable_id)
            )
            pull_request = result.scalar_one_or_none()
            if pull_request:
                issue_result = await db.execute(
                    select(IssueModel).where(IssueModel.id == pull_request.issue_id)
                )
                issue = issue_result.scalar_one_or_none()
        else:
            result = await db.execute(
                select(IssueModel).where(IssueModel.id == labelable_id)
            )
            issue = result.scalar_one_or_none()

        issue = await _writable_or_none(info, issue, issue.repo_id if issue else None, _issue_scopes(issue))
        if not issue:
            raise ValueError(f"Labelable with id {labelable_id} not found")

        for label_id in input.label_ids:
            decoded_label_id = _decode_node_id(label_id)
            label_result = await db.execute(
                select(LabelModel).where(
                    LabelModel.id == decoded_label_id,
                    LabelModel.repo_id == issue.repo_id,
                )
            )
            label = label_result.scalar_one_or_none()
            if not label:
                raise ValueError(f"Label with id {decoded_label_id} not found")
            if not any(existing.id == label.id for existing in issue.labels):
                issue.labels.append(label)

        await db.commit()
        await db.refresh(issue)

        if pull_request:
            from app.services.auto_merge_service import process_auto_merge
            await process_auto_merge(db, pull_request, info.context.get("user"))
            await db.refresh(pull_request)

        labelable = pull_request_from_model(pull_request) if pull_request else issue_from_model(issue)
        return AddLabelsToLabelablePayload(
            labelable=labelable,
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def create_issue(self, info: Info, input: CreateIssueInput) -> CreateIssuePayload:
        """Create a new issue on a repository."""
        from app.models.issue import Issue as IssueModel, IssueLabel, IssueAssignee
        from app.models.label import Label as LabelModel
        from app.models.milestone import Milestone as MilestoneModel
        from app.models.repository import Repository as RepoModel

        current_user = _require_auth(info)
        db = info.context["db"]

        repo_id = _decode_node_id(input.repository_id)
        result = await db.execute(
            select(RepoModel).where(RepoModel.id == repo_id)
        )
        repo = result.scalar_one_or_none()
        repo = await _writable_or_none(info, repo, repo.id if repo else None, _ISSUES)
        if not repo:
            raise ValueError(f"Repository with id {repo_id} not found")

        # Assign next issue number
        issue_number = repo.next_issue_number
        repo.next_issue_number += 1

        new_issue = IssueModel(
            repo_id=repo.id,
            number=issue_number,
            user_id=current_user.id,
            title=input.title,
            body=input.body,
            state="open",
            milestone_id=(
                (await _same_repository_ids(db, MilestoneModel, [input.milestone_id], repo.id, "Milestone"))[0]
                if input.milestone_id else None
            ),
        )
        label_ids = await _same_repository_ids(db, LabelModel, input.label_ids or [], repo.id, "Label")
        db.add(new_issue)
        await db.flush()

        # Add labels
        for label_id in label_ids:
            db.add(IssueLabel(issue_id=new_issue.id, label_id=label_id))

        # Add assignees
        if input.assignee_ids:
            for assignee_id in input.assignee_ids:
                db.add(IssueAssignee(issue_id=new_issue.id, user_id=_decode_node_id(assignee_id)))

        # Increment open issues count
        repo.open_issues_count += 1

        await db.commit()
        await db.refresh(new_issue)

        return CreateIssuePayload(
            issue=issue_from_model(new_issue),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def update_issue(self, info: Info, input: UpdateIssueInput) -> UpdateIssuePayload:
        """Update an existing issue."""
        from app.models.issue import Issue as IssueModel, IssueLabel, IssueAssignee

        _require_auth(info)
        db = info.context["db"]

        issue_id = _decode_node_id(input.id)
        result = await db.execute(
            select(IssueModel).where(IssueModel.id == issue_id)
        )
        issue = result.scalar_one_or_none()
        issue = await _writable_or_none(info, issue, issue.repo_id if issue else None, _issue_scopes(issue))
        if not issue:
            raise ValueError(f"Issue with id {issue_id} not found")

        if input.title is not None:
            issue.title = input.title
        if input.body is not None:
            issue.body = input.body
        if input.state is not None:
            new_state = input.state.lower()
            if new_state in ("open", "closed"):
                old_state = issue.state
                issue.state = new_state
                if new_state == "closed" and old_state == "open":
                    issue.closed_at = datetime.now(timezone.utc)
                elif new_state == "open" and old_state == "closed":
                    issue.closed_at = None
        if input.milestone_id is not None:
            from app.models.milestone import Milestone as MilestoneModel

            issue.milestone_id = (
                (await _same_repository_ids(db, MilestoneModel, [input.milestone_id], issue.repo_id, "Milestone"))[0]
                if input.milestone_id != "0" else None
            )

        # Update labels if provided
        if input.label_ids is not None:
            from app.models.label import Label as LabelModel

            label_ids = await _same_repository_ids(db, LabelModel, input.label_ids, issue.repo_id, "Label")
            # Remove existing labels
            from sqlalchemy import delete
            await db.execute(
                delete(IssueLabel).where(IssueLabel.issue_id == issue.id)
            )
            for label_id in label_ids:
                db.add(IssueLabel(issue_id=issue.id, label_id=label_id))

        # Update assignees if provided
        if input.assignee_ids is not None:
            from sqlalchemy import delete
            await db.execute(
                delete(IssueAssignee).where(IssueAssignee.issue_id == issue.id)
            )
            for assignee_id in input.assignee_ids:
                db.add(IssueAssignee(issue_id=issue.id, user_id=_decode_node_id(assignee_id)))

        await db.commit()
        await db.refresh(issue)

        return UpdateIssuePayload(
            issue=issue_from_model(issue),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def close_issue(self, info: Info, input: CloseIssueInput) -> CloseIssuePayload:
        """Close an issue."""
        from app.models.issue import Issue as IssueModel
        from app.models.repository import Repository as RepoModel

        current_user = _require_auth(info)
        db = info.context["db"]

        issue_id = _decode_node_id(input.issue_id)
        result = await db.execute(
            select(IssueModel).where(IssueModel.id == issue_id)
        )
        issue = result.scalar_one_or_none()
        issue = await _writable_or_none(info, issue, issue.repo_id if issue else None, _issue_scopes(issue))
        if not issue:
            raise ValueError(f"Issue with id {issue_id} not found")

        if issue.state == "open":
            issue.state = "closed"
            issue.closed_at = datetime.now(timezone.utc)
            issue.closed_by_id = current_user.id
            if input.state_reason:
                issue.state_reason = input.state_reason

            # Decrement open issues count
            repo_result = await db.execute(
                select(RepoModel).where(RepoModel.id == issue.repo_id)
            )
            repo = repo_result.scalar_one_or_none()
            if repo and repo.open_issues_count > 0:
                repo.open_issues_count -= 1

            await db.commit()
            await db.refresh(issue)

        return CloseIssuePayload(
            issue=issue_from_model(issue),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def reopen_issue(self, info: Info, input: ReopenIssueInput) -> ReopenIssuePayload:
        """Reopen a closed issue."""
        from app.models.issue import Issue as IssueModel
        from app.models.repository import Repository as RepoModel

        _require_auth(info)
        db = info.context["db"]

        issue_id = _decode_node_id(input.issue_id)
        result = await db.execute(
            select(IssueModel).where(IssueModel.id == issue_id)
        )
        issue = result.scalar_one_or_none()
        issue = await _writable_or_none(info, issue, issue.repo_id if issue else None, _issue_scopes(issue))
        if not issue:
            raise ValueError(f"Issue with id {issue_id} not found")

        if issue.state == "closed":
            issue.state = "open"
            issue.closed_at = None
            issue.closed_by_id = None
            issue.state_reason = None

            # Increment open issues count
            repo_result = await db.execute(
                select(RepoModel).where(RepoModel.id == issue.repo_id)
            )
            repo = repo_result.scalar_one_or_none()
            if repo:
                repo.open_issues_count += 1

            await db.commit()
            await db.refresh(issue)

        return ReopenIssuePayload(
            issue=issue_from_model(issue),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def add_comment(self, info: Info, input: AddCommentInput) -> AddCommentPayload:
        """Add a comment to an issue or pull request."""
        from app.models.comment import IssueComment as IssueCommentModel
        from app.models.issue import Issue as IssueModel

        current_user = _require_auth(info)
        db = info.context["db"]

        subject_id = _decode_node_id(input.subject_id)

        # Verify the issue exists
        result = await db.execute(
            select(IssueModel).where(IssueModel.id == subject_id)
        )
        issue = result.scalar_one_or_none()
        issue = await _writable_or_none(info, issue, issue.repo_id if issue else None, _issue_scopes(issue))
        if not issue:
            raise ValueError(f"Subject with id {subject_id} not found")

        new_comment = IssueCommentModel(
            issue_id=issue.id,
            user_id=current_user.id,
            body=input.body,
        )
        db.add(new_comment)
        await db.commit()
        await db.refresh(new_comment)

        return AddCommentPayload(
            comment_edge=AddCommentEdge(node=comment_from_model(new_comment)),
            subject=issue_from_model(issue),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def create_pull_request(
        self, info: Info, input: CreatePullRequestInput
    ) -> CreatePullRequestPayload:
        """Create a new pull request."""
        from app.models.issue import Issue as IssueModel
        from app.models.pull_request import PullRequest as PRModel
        from app.models.repository import Repository as RepoModel
        from app.models.branch import Branch

        current_user = _require_auth(info)
        db = info.context["db"]

        repo_id = _decode_node_id(input.repository_id)
        result = await db.execute(
            select(RepoModel).where(RepoModel.id == repo_id)
        )
        repo = result.scalar_one_or_none()
        repo = await _writable_or_none(info, repo, repo.id if repo else None, _PULL_REQUESTS)
        if not repo:
            raise ValueError(f"Repository with id {repo_id} not found")

        if not input.head_ref_name:
            raise ValueError("head_ref_name is required")
        if not input.base_ref_name:
            raise ValueError("base_ref_name is required")

        # Look up head branch SHA
        head_result = await db.execute(
            select(Branch).where(
                Branch.repo_id == repo.id,
                Branch.name == input.head_ref_name,
            )
        )
        head_branch = head_result.scalar_one_or_none()
        head_sha = head_branch.sha if head_branch else "0" * 40

        # Look up base branch SHA
        base_result = await db.execute(
            select(Branch).where(
                Branch.repo_id == repo.id,
                Branch.name == input.base_ref_name,
            )
        )
        base_branch = base_result.scalar_one_or_none()
        base_sha = base_branch.sha if base_branch else "0" * 40

        # Allocate issue number
        issue_number = repo.next_issue_number
        repo.next_issue_number += 1

        # Create the backing issue
        new_issue = IssueModel(
            repo_id=repo.id,
            number=issue_number,
            user_id=current_user.id,
            title=input.title,
            body=input.body,
            state="open",
        )
        db.add(new_issue)
        await db.flush()

        # Create the pull request
        new_pr = PRModel(
            issue_id=new_issue.id,
            repo_id=repo.id,
            head_ref=input.head_ref_name,
            head_sha=head_sha,
            head_repo_id=repo.id,
            base_ref=input.base_ref_name,
            base_sha=base_sha,
            draft=input.draft,
            mergeable=True,
            last_push_by_id=current_user.id,
        )
        db.add(new_pr)

        repo.open_issues_count += 1

        await db.commit()
        await db.refresh(new_pr)
        await db.refresh(new_issue)

        # GitHub emits the same pull_request activity regardless of whether a
        # pull request is created through REST or GraphQL (gh pr create uses
        # this mutation). Keep Actions behavior identical across both paths.
        from app.services.workflow_service import build_activity_payload, dispatch_event

        await dispatch_event(
            db,
            repo,
            current_user,
            "pull_request_target",
            "opened",
            build_activity_payload(
                repo,
                current_user,
                "opened",
                issue=new_issue,
                pull_request=new_pr,
                ref=f"refs/heads/{new_pr.base_ref}",
                sha=new_pr.base_sha,
            ),
            ref=new_pr.base_ref,
            sha=new_pr.base_sha,
        )

        return CreatePullRequestPayload(
            pull_request=pull_request_from_model(new_pr),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def merge_pull_request(
        self, info: Info, input: MergePullRequestInput
    ) -> MergePullRequestPayload:
        """Merge a pull request."""
        from app.models.pull_request import PullRequest as PRModel
        from app.models.issue import Issue as IssueModel
        from app.models.repository import Repository as RepoModel

        current_user = _require_auth(info)
        db = info.context["db"]

        pr_id = _decode_node_id(input.pull_request_id)
        result = await db.execute(
            select(PRModel).where(PRModel.id == pr_id)
        )
        pr = result.scalar_one_or_none()
        pr = await _writable_or_none(info, pr, pr.repo_id if pr else None, _PULL_REQUESTS)
        if not pr:
            raise ValueError(f"Pull request with id {pr_id} not found")

        if pr.merged:
            raise ValueError("Pull request is already merged")

        from app.services.merge_readiness_service import evaluate_merge_readiness

        readiness = await evaluate_merge_readiness(
            db,
            pr,
            actor=current_user,
            merge_method=(input.merge_method or "MERGE").lower(),
        )
        if not readiness.ready:
            raise ValueError(
                "; ".join(readiness.reasons) or "Pull request is not mergeable"
            )

        repo_result = await db.execute(
            select(RepoModel).where(RepoModel.id == pr.repo_id)
        )
        repo = repo_result.scalar_one_or_none()
        from app.services.closing_issue_service import resolve_closing_issues

        linked_issues = (
            await resolve_closing_issues(
                db,
                pr,
                repo,
                include_commit_messages=True,
            )
            if repo is not None
            else []
        )

        # Mark as merged
        now = datetime.now(timezone.utc)
        pr.merged = True
        pr.merged_at = now
        pr.merged_by_id = current_user.id
        pr.merge_commit_sha = f"merge_{pr.head_sha[:8]}_{pr.base_sha[:8]}"

        # Close the backing issue
        issue_result = await db.execute(
            select(IssueModel).where(IssueModel.id == pr.issue_id)
        )
        issue = issue_result.scalar_one_or_none()
        if issue and issue.state == "open":
            issue.state = "closed"
            issue.closed_at = now
            issue.state_reason = "completed"
            issue.closed_by_id = current_user.id
            if repo and repo.open_issues_count > 0:
                repo.open_issues_count -= 1

        from app.services.closing_issue_service import close_linked_issues

        closed_linked_issues = close_linked_issues(
            linked_issues,
            current_user,
            now,
        )

        await db.commit()
        await db.refresh(pr)

        from app.services.closing_issue_service import dispatch_linked_issue_closed_events

        await dispatch_linked_issue_closed_events(
            db,
            closed_linked_issues,
            current_user,
        )

        return MergePullRequestPayload(
            pull_request=pull_request_from_model(pr),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def enable_pull_request_auto_merge(
        self, info: Info, input: EnablePullRequestAutoMergeInput
    ) -> EnablePullRequestAutoMergePayload:
        """Queue a PR for automatic merge when it becomes ready."""
        from app.models.auto_merge import PullRequestAutoMerge
        from app.models.pull_request import PullRequest as PRModel

        current_user = _require_auth(info)
        db = info.context["db"]
        pr_id = _decode_node_id(input.pull_request_id)
        result = await db.execute(select(PRModel).where(PRModel.id == pr_id))
        pr = result.scalar_one_or_none()
        pr = await _writable_or_none(info, pr, pr.repo_id if pr else None, _PULL_REQUESTS)
        if pr is None:
            raise ValueError(f"Pull request with id {pr_id} not found")
        if pr.merged:
            raise ValueError("Pull request is already merged")
        if pr.issue is not None and pr.issue.state == "closed":
            raise ValueError("Pull request is closed")

        method = (input.merge_method or "MERGE").upper()
        if method not in {"MERGE", "SQUASH", "REBASE"}:
            raise ValueError("merge_method must be MERGE, SQUASH, or REBASE")

        request_result = await db.execute(
            select(PullRequestAutoMerge).where(
                PullRequestAutoMerge.pull_request_id == pr.id
            )
        )
        request = request_result.scalar_one_or_none()
        if request is None:
            request = PullRequestAutoMerge(
                pull_request_id=pr.id,
                enabled_at=datetime.now(timezone.utc),
                enabled_by_id=current_user.id,
            )
            db.add(request)
        request.enabled_at = datetime.now(timezone.utc)
        request.enabled_by_id = current_user.id
        request.merge_method = method
        request.commit_headline = input.commit_headline
        request.commit_body = input.commit_body
        await db.commit()
        await db.refresh(pr)

        from app.services.auto_merge_service import process_auto_merge
        await process_auto_merge(db, pr, current_user)
        await db.refresh(pr)
        return EnablePullRequestAutoMergePayload(
            pull_request=pull_request_from_model(pr),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def disable_pull_request_auto_merge(
        self, info: Info, input: DisablePullRequestAutoMergeInput
    ) -> DisablePullRequestAutoMergePayload:
        """Cancel a queued auto-merge request."""
        from app.models.auto_merge import PullRequestAutoMerge
        from app.models.pull_request import PullRequest as PRModel

        _require_auth(info)
        db = info.context["db"]
        pr_id = _decode_node_id(input.pull_request_id)
        result = await db.execute(select(PRModel).where(PRModel.id == pr_id))
        pr = result.scalar_one_or_none()
        pr = await _writable_or_none(info, pr, pr.repo_id if pr else None, _PULL_REQUESTS)
        if pr is None:
            raise ValueError(f"Pull request with id {pr_id} not found")
        request_result = await db.execute(
            select(PullRequestAutoMerge).where(
                PullRequestAutoMerge.pull_request_id == pr.id
            )
        )
        request = request_result.scalar_one_or_none()
        if request is not None:
            await db.delete(request)
            await db.commit()
            await db.refresh(pr)
        return DisablePullRequestAutoMergePayload(
            pull_request=pull_request_from_model(pr),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def add_reaction(self, info: Info, input: AddReactionInput) -> AddReactionPayload:
        """Add a reaction to a subject (issue, comment, etc.)."""
        from app.models.reaction import Reaction

        current_user = _require_auth(info)
        db = info.context["db"]

        subject_id = _decode_node_id(input.subject_id)
        from app.models.issue import Issue as IssueModel

        # Reactions here are on issues. A missing subject and one in a hidden
        # repository fail alike, so neither reveals which hidden issues exist.
        subject = (await db.execute(select(IssueModel).where(IssueModel.id == subject_id))).scalar_one_or_none()
        if await _writable_or_none(info, subject, subject.repo_id if subject else None, _issue_scopes(subject)) is None:
            raise ValueError(f"Subject with id {subject_id} not found")

        # Determine reactable type by trying to look up the subject
        # Default to "issue" as the most common case
        reactable_type = "issue"

        # Check if a reaction already exists
        existing = await db.execute(
            select(Reaction).where(
                Reaction.user_id == current_user.id,
                Reaction.content == input.content,
                Reaction.reactable_type == reactable_type,
                Reaction.reactable_id == subject_id,
            )
        )
        existing_reaction = existing.scalar_one_or_none()
        if existing_reaction:
            return AddReactionPayload(
                reaction=ReactionType(
                    database_id=existing_reaction.id,
                    content=existing_reaction.content,
                    user=user_from_model(current_user),
                    created_at=existing_reaction.created_at,
                ),
                subject_id=input.subject_id,
                client_mutation_id=input.client_mutation_id,
            )

        new_reaction = Reaction(
            user_id=current_user.id,
            content=input.content,
            reactable_type=reactable_type,
            reactable_id=subject_id,
        )
        db.add(new_reaction)
        await db.commit()
        await db.refresh(new_reaction)

        return AddReactionPayload(
            reaction=ReactionType(
                database_id=new_reaction.id,
                content=new_reaction.content,
                user=user_from_model(current_user),
                created_at=new_reaction.created_at,
            ),
            subject_id=input.subject_id,
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def create_repository(
        self, info: Info, input: CreateRepositoryInput
    ) -> CreateRepositoryPayload:
        """Create a new repository."""
        from app.models.repository import Repository as RepoModel

        current_user = _require_auth(info)
        db = info.context["db"]

        owner_id = _decode_node_id(input.owner_id) if input.owner_id else current_user.id

        full_name = f"{current_user.login}/{input.name}"

        # Check if the owner is not the current user (could be an org).
        # Only a site admin may create a repository for another account; the
        # refusal comes before the name-conflict check, so the conflict error
        # cannot be used to probe another owner's private repository names.
        if owner_id != current_user.id and not current_user.site_admin:
            raise _typed_error(
                "FORBIDDEN",
                f"{current_user.login} cannot create a repository for that owner",
            )
        if owner_id != current_user.id:
            from app.models.user import User
            owner_result = await db.execute(
                select(User).where(User.id == owner_id)
            )
            owner = owner_result.scalar_one_or_none()
            if owner:
                full_name = f"{owner.login}/{input.name}"

        # Check for name conflicts
        existing = await db.execute(
            select(RepoModel).where(RepoModel.full_name == full_name)
        )
        if existing.scalar_one_or_none():
            raise ValueError(f"Repository {full_name} already exists")

        is_private = input.visibility.lower() == "private"

        import os
        from app.config import settings

        owner_login = current_user.login
        if owner_id != current_user.id:
            owner_login = full_name.split("/")[0]

        disk_path = os.path.join(
            settings.DATA_DIR, "repos", owner_login, f"{input.name}.git"
        )

        new_repo = RepoModel(
            owner_id=owner_id,
            name=input.name,
            full_name=full_name,
            description=input.description,
            private=is_private,
            visibility=input.visibility.lower(),
            has_issues=input.has_issues_enabled,
            has_wiki=input.has_wiki_enabled,
            disk_path=disk_path,
        )
        db.add(new_repo)
        await db.commit()
        await db.refresh(new_repo)

        # Initialize bare git repository on disk
        os.makedirs(disk_path, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "git", "init", "--bare", "--initial-branch", "main", disk_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        return CreateRepositoryPayload(
            repository=repository_from_model(new_repo),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def close_pull_request(
        self, info: Info, input: ClosePullRequestInput
    ) -> ClosePullRequestPayload:
        """Close a pull request."""
        from app.models.pull_request import PullRequest as PRModel
        from app.models.issue import Issue as IssueModel
        from app.models.repository import Repository as RepoModel

        current_user = _require_auth(info)
        db = info.context["db"]

        pr_id = _decode_node_id(input.pull_request_id)
        result = await db.execute(
            select(PRModel).where(PRModel.id == pr_id)
        )
        pr = result.scalar_one_or_none()
        pr = await _writable_or_none(info, pr, pr.repo_id if pr else None, _PULL_REQUESTS)
        if not pr:
            raise ValueError(f"Pull request with id {pr_id} not found")

        # Close the backing issue
        issue_result = await db.execute(
            select(IssueModel).where(IssueModel.id == pr.issue_id)
        )
        issue = issue_result.scalar_one_or_none()
        if issue and issue.state == "open":
            issue.state = "closed"
            issue.closed_at = datetime.now(timezone.utc)
            issue.closed_by_id = current_user.id

            # Decrement open issues count
            repo_result = await db.execute(
                select(RepoModel).where(RepoModel.id == pr.repo_id)
            )
            repo = repo_result.scalar_one_or_none()
            if repo and repo.open_issues_count > 0:
                repo.open_issues_count -= 1

            await db.commit()
            # The issue first: refreshing it reloads its pull_request
            # relationship, which would expire pr's eager loads again.
            await db.refresh(issue)
            await db.refresh(pr)
        else:
            await db.commit()
            await db.refresh(pr)

        return ClosePullRequestPayload(
            pull_request=pull_request_from_model(pr),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def reopen_pull_request(
        self, info: Info, input: ReopenPullRequestInput
    ) -> ReopenPullRequestPayload:
        """Reopen a closed pull request."""
        from app.models.pull_request import PullRequest as PRModel
        from app.models.issue import Issue as IssueModel
        from app.models.repository import Repository as RepoModel

        _require_auth(info)
        db = info.context["db"]

        pr_id = _decode_node_id(input.pull_request_id)
        result = await db.execute(
            select(PRModel).where(PRModel.id == pr_id)
        )
        pr = result.scalar_one_or_none()
        pr = await _writable_or_none(info, pr, pr.repo_id if pr else None, _PULL_REQUESTS)
        if not pr:
            raise ValueError(f"Pull request with id {pr_id} not found")

        if pr.merged:
            raise ValueError("Cannot reopen a merged pull request")

        # Reopen the backing issue
        issue_result = await db.execute(
            select(IssueModel).where(IssueModel.id == pr.issue_id)
        )
        issue = issue_result.scalar_one_or_none()
        if issue and issue.state == "closed":
            issue.state = "open"
            issue.closed_at = None
            issue.closed_by_id = None
            issue.state_reason = None

            # Increment open issues count
            repo_result = await db.execute(
                select(RepoModel).where(RepoModel.id == pr.repo_id)
            )
            repo = repo_result.scalar_one_or_none()
            if repo:
                repo.open_issues_count += 1

            await db.commit()
            # The issue first: refreshing it reloads its pull_request
            # relationship, which would expire pr's eager loads again.
            await db.refresh(issue)
            await db.refresh(pr)
        else:
            await db.commit()
            await db.refresh(pr)

        return ReopenPullRequestPayload(
            pull_request=pull_request_from_model(pr),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def update_pull_request(
        self, info: Info, input: UpdatePullRequestInput
    ) -> UpdatePullRequestPayload:
        """Update a pull request's title, body, or base branch."""
        from app.models.pull_request import PullRequest as PRModel
        from app.models.issue import Issue as IssueModel

        _require_auth(info)
        db = info.context["db"]

        pr_id = _decode_node_id(input.pull_request_id)
        result = await db.execute(
            select(PRModel).where(PRModel.id == pr_id)
        )
        pr = result.scalar_one_or_none()
        pr = await _writable_or_none(info, pr, pr.repo_id if pr else None, _PULL_REQUESTS)
        if not pr:
            raise ValueError(f"Pull request with id {pr_id} not found")

        # Update backing issue fields
        issue_result = await db.execute(
            select(IssueModel).where(IssueModel.id == pr.issue_id)
        )
        issue = issue_result.scalar_one_or_none()
        if not issue:
            raise ValueError(f"Backing issue for PR {pr_id} not found")

        if input.title is not None:
            issue.title = input.title
        if input.body is not None:
            issue.body = input.body
        if input.base_ref_name is not None:
            pr.base_ref = input.base_ref_name
        if input.assignee_ids is not None:
            # GitHub replaces the assignee set wholesale here; `gh` reads the
            # current assignees first and sends the union, so replacing is
            # what makes --add-assignee add rather than clobber.
            #
            # The relationship has to be loaded before it is replaced: assigning
            # to it lazily loads the current rows, and a lazy load inside the
            # async resolver raises MissingGreenlet rather than doing IO.
            from app.models.user import User as UserModel

            await db.refresh(issue, attribute_names=["assignees"])

            user_ids = [_decode_node_id(node_id) for node_id in input.assignee_ids]
            if user_ids:
                users = (await db.execute(
                    select(UserModel).where(UserModel.id.in_(user_ids))
                )).scalars().all()
                found = {u.id for u in users}
                missing = [str(i) for i in user_ids if i not in found]
                if missing:
                    raise ValueError(
                        "assigneeIds refers to users that do not exist: "
                        + ", ".join(missing)
                    )
                issue.assignees = list(users)
            else:
                issue.assignees = []

        await db.commit()
        # refresh() expires the instance, so the relationships the payload
        # serializer reads have to be named here. Without that, building the
        # payload lazily loads issue.repository inside the async resolver and
        # raises MissingGreenlet — which is why this mutation failed for every
        # field, not just the new one, until it had a test.
        # A title or body change also expires the server-set updated_at, so
        # both rows are refreshed whole, then the issue's relationships.
        await db.refresh(issue)
        await db.refresh(pr)
        await db.refresh(issue, attribute_names=["repository", "assignees"])

        return UpdatePullRequestPayload(
            pull_request=pull_request_from_model(pr),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def create_commit_on_branch(
        self, info: Info, input: CreateCommitOnBranchInput
    ) -> CreateCommitOnBranchPayload:
        """Commit file additions and deletions to an existing branch.

        GitHub's web-UI commit path: the author is the authenticated account
        and the committer is GitHub, files are regular (100644) and sent
        inline, and the commit applies only if the branch still points at
        ``expectedHeadOid``. Errors carry GitHub's ``type`` (NOT_FOUND,
        FORBIDDEN, STALE_DATA, UNPROCESSABLE).
        """
        from app.git.bare_repo import MissingPathError, StaleBranchError, commit_files
        from app.models.repository import Repository as RepoModel
        from app.services.branch_service import record_branch

        current_user = _require_auth(info)
        db = info.context["db"]

        target = input.branch
        if target.id is not None or not target.repository_name_with_owner or not target.branch_name:
            raise _typed_error(
                "UNPROCESSABLE",
                "branch requires repositoryNameWithOwner and branchName (branch id is not supported)",
            )
        name = target.repository_name_with_owner
        branch = target.branch_name.removeprefix("refs/heads/")
        repo = (
            await db.execute(select(RepoModel).where(RepoModel.full_name == name))
        ).scalar_one_or_none()
        from app.graphql.authz import may_write, visible

        if repo is None or not await visible(info, repo):
            raise _typed_error("NOT_FOUND", f"Could not resolve to a Repository with the name '{name}'.")
        if not await may_write(info, db, repo, ("contents",)):
            raise _typed_error(
                "FORBIDDEN",
                f"{current_user.login} does not have the correct permissions to execute `CreateCommitOnBranch`",
            )

        changes = input.file_changes or FileChanges()
        additions: list[tuple[str, bytes]] = []
        for addition in changes.additions or []:
            try:
                additions.append((addition.path, base64.b64decode(addition.contents, validate=True)))
            except Exception:
                raise _typed_error("UNPROCESSABLE", f"Invalid Base64 contents for {addition.path}") from None
        deletions = [deletion.path for deletion in changes.deletions or []]
        message = input.message.headline
        if input.message.body:
            message += "\n\n" + input.message.body

        try:
            oid = await commit_files(
                repo.disk_path,
                branch,
                input.expected_head_oid,
                additions,
                deletions,
                message,
                author=(current_user.name or current_user.login,
                        current_user.email or f"{current_user.login}@users.noreply.github.com"),
                committer=("GitHub", "noreply@github.com"),
            )
        except StaleBranchError as exc:
            if exc.actual is None:
                raise _typed_error(
                    "NOT_FOUND", f"Could not resolve to a Ref with the name 'refs/heads/{branch}'."
                ) from None
            raise _typed_error(
                "STALE_DATA",
                f'Expected branch to point to "{exc.expected}" but it did not. Pull and try again. '
                f"({exc})",
            ) from None
        except MissingPathError as exc:
            raise _typed_error("UNPROCESSABLE", f"A path was requested for deletion which does not exist as of commit oid `{input.expected_head_oid}`: {exc}") from None

        await record_branch(db, repo, branch, oid)
        repo.pushed_at = datetime.now(timezone.utc)
        await db.commit()

        return CreateCommitOnBranchPayload(
            commit=Commit(oid=oid, message=message),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def resolve_review_thread(
        self, info: Info, input: ResolveReviewThreadInput
    ) -> ResolveReviewThreadPayload:
        """Mark a review thread resolved (pull request author or write access)."""
        root = await _review_thread_root(info, input.thread_id, "ResolveReviewThread")
        if root.thread_resolved_at is None:
            root.thread_resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
            root.thread_resolved_by_id = info.context["user"].id
            await info.context["db"].commit()
        return ResolveReviewThreadPayload(
            thread=await _thread_payload(info, root),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def unresolve_review_thread(
        self, info: Info, input: UnresolveReviewThreadInput
    ) -> UnresolveReviewThreadPayload:
        """Mark a resolved review thread unresolved."""
        root = await _review_thread_root(info, input.thread_id, "UnresolveReviewThread")
        if root.thread_resolved_at is not None:
            root.thread_resolved_at = None
            root.thread_resolved_by_id = None
            await info.context["db"].commit()
        return UnresolveReviewThreadPayload(
            thread=await _thread_payload(info, root),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def minimize_comment(
        self, info: Info, input: MinimizeCommentInput
    ) -> MinimizeCommentPayload:
        """Hide an issue comment, review comment, or review (write access)."""
        subject = await _minimize_subject(info, input.subject_id, "MinimizeComment")
        subject.row.minimized_reason = MINIMIZED_REASONS[input.classifier.value]
        db = info.context["db"]
        await db.commit()
        await db.refresh(subject.row)
        return MinimizeCommentPayload(
            minimized_comment=await _minimizable_from_row(info, subject.kind, subject.row),
            client_mutation_id=input.client_mutation_id,
        )

    @strawberry.mutation
    async def unminimize_comment(
        self, info: Info, input: UnminimizeCommentInput
    ) -> UnminimizeCommentPayload:
        """Show a hidden comment or review again."""
        subject = await _minimize_subject(info, input.subject_id, "UnminimizeComment")
        subject.row.minimized_reason = None
        db = info.context["db"]
        await db.commit()
        await db.refresh(subject.row)
        return UnminimizeCommentPayload(
            unminimized_comment=await _minimizable_from_row(info, subject.kind, subject.row),
            client_mutation_id=input.client_mutation_id,
        )

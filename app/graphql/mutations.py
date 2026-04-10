"""GraphQL mutation resolvers."""

import asyncio
import base64
from datetime import datetime, timezone
from typing import Optional

import strawberry
from strawberry.types import Info
from sqlalchemy import select

from app.graphql.types.user import GitHubUser, user_from_model
from app.graphql.types.repository import Repository, repository_from_model
from app.graphql.types.issue import Issue, issue_from_model, IssueComment, comment_from_model
from app.graphql.types.pull_request import PullRequest, pull_request_from_model


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
    client_mutation_id: Optional[str] = None


@strawberry.input
class MergePullRequestInput:
    pull_request_id: strawberry.ID
    commit_headline: Optional[str] = None
    commit_body: Optional[str] = None
    merge_method: Optional[str] = None  # MERGE, SQUASH, REBASE
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
    async def create_issue(self, info: Info, input: CreateIssueInput) -> CreateIssuePayload:
        """Create a new issue on a repository."""
        from app.models.issue import Issue as IssueModel, IssueLabel, IssueAssignee
        from app.models.repository import Repository as RepoModel

        current_user = _require_auth(info)
        db = info.context["db"]

        repo_id = _decode_node_id(input.repository_id)
        result = await db.execute(
            select(RepoModel).where(RepoModel.id == repo_id)
        )
        repo = result.scalar_one_or_none()
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
            milestone_id=_decode_node_id(input.milestone_id) if input.milestone_id else None,
        )
        db.add(new_issue)
        await db.flush()

        # Add labels
        if input.label_ids:
            for label_id in input.label_ids:
                db.add(IssueLabel(issue_id=new_issue.id, label_id=_decode_node_id(label_id)))

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
            issue.milestone_id = _decode_node_id(input.milestone_id) if input.milestone_id != "0" else None

        # Update labels if provided
        if input.label_ids is not None:
            # Remove existing labels
            from sqlalchemy import delete
            await db.execute(
                delete(IssueLabel).where(IssueLabel.issue_id == issue.id)
            )
            for label_id in input.label_ids:
                db.add(IssueLabel(issue_id=issue.id, label_id=_decode_node_id(label_id)))

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
        )
        db.add(new_pr)

        repo.open_issues_count += 1

        await db.commit()
        await db.refresh(new_pr)
        await db.refresh(new_issue)

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
        if not pr:
            raise ValueError(f"Pull request with id {pr_id} not found")

        if pr.merged:
            raise ValueError("Pull request is already merged")

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

            repo_result = await db.execute(
                select(RepoModel).where(RepoModel.id == pr.repo_id)
            )
            repo = repo_result.scalar_one_or_none()
            if repo and repo.open_issues_count > 0:
                repo.open_issues_count -= 1

        await db.commit()
        await db.refresh(pr)

        return MergePullRequestPayload(
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

        # Check if the owner is not the current user (could be an org)
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
            await db.refresh(pr)
            if issue:
                await db.refresh(issue)
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
            await db.refresh(pr)
            if issue:
                await db.refresh(issue)
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

        await db.commit()
        await db.refresh(pr)
        await db.refresh(issue)

        return UpdatePullRequestPayload(
            pull_request=pull_request_from_model(pr),
            client_mutation_id=input.client_mutation_id,
        )

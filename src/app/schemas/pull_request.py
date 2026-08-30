"""Pydantic schemas for GitHub Pull Request API responses."""

from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id


class PRCreate(BaseModel):
    """Schema for creating a pull request."""

    title: str
    body: Optional[str] = None
    head: str  # branch name
    base: str  # branch name
    draft: bool = False


class PRUpdate(BaseModel):
    """Schema for updating a pull request."""

    title: Optional[str] = None
    body: Optional[str] = None
    state: Optional[str] = None
    base: Optional[str] = None


class PRMerge(BaseModel):
    """Schema for merging a pull request."""

    commit_title: Optional[str] = None
    commit_message: Optional[str] = None
    sha: Optional[str] = None
    merge_method: str = "merge"  # "merge", "squash", or "rebase"


class PRBranchRef(BaseModel):
    """Branch reference in a PR (head or base)."""

    label: str
    ref: str
    sha: str

    model_config = ConfigDict(from_attributes=True)


class PRResponse(BaseModel):
    """Full GitHub-compatible pull request JSON response."""

    url: str
    id: int
    node_id: str
    html_url: str
    diff_url: str
    patch_url: str
    issue_url: str
    number: int
    state: str
    locked: bool = False
    title: str
    user: SimpleUser
    body: Optional[str] = None
    created_at: str
    updated_at: str
    closed_at: Optional[str] = None
    merged_at: Optional[str] = None
    merge_commit_sha: Optional[str] = None
    head: PRBranchRef
    base: PRBranchRef
    draft: bool = False
    merged: bool = False
    mergeable: Optional[bool] = None
    comments: int = 0
    review_comments: int = 0
    commits: int = 0
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_db(
        cls,
        pr,
        base_url: str,
        owner_login: str,
        repo_name: str,
        comments_count: int = 0,
        review_comments_count: int = 0,
        commits_count: int = 0,
        additions: int = 0,
        deletions: int = 0,
        changed_files: int = 0,
    ) -> "PRResponse":
        """Construct a PRResponse from a DB pull request object.

        Expects pr to have a loaded `issue` relationship for title, body,
        state, user, timestamps, and number.
        """
        api_base = f"{base_url}/api/v3"
        repo_url = f"{api_base}/repos/{owner_login}/{repo_name}"
        issue = pr.issue

        user_simple = SimpleUser.from_db(issue.user, base_url)

        head_label = f"{owner_login}:{pr.head_ref}"
        base_label = f"{owner_login}:{pr.base_ref}"

        head_ref = PRBranchRef(
            label=head_label,
            ref=pr.head_ref,
            sha=pr.head_sha,
        )
        base_ref = PRBranchRef(
            label=base_label,
            ref=pr.base_ref,
            sha=pr.base_sha,
        )

        return cls(
            url=f"{repo_url}/pulls/{issue.number}",
            id=pr.id,
            node_id=_make_node_id("PullRequest", pr.id),
            html_url=f"{base_url}/{owner_login}/{repo_name}/pull/{issue.number}",
            diff_url=f"{base_url}/{owner_login}/{repo_name}/pull/{issue.number}.diff",
            patch_url=f"{base_url}/{owner_login}/{repo_name}/pull/{issue.number}.patch",
            issue_url=f"{repo_url}/issues/{issue.number}",
            number=issue.number,
            state=issue.state,
            locked=issue.locked,
            title=issue.title,
            user=user_simple,
            body=issue.body,
            created_at=_fmt_dt(issue.created_at),
            updated_at=_fmt_dt(issue.updated_at),
            closed_at=_fmt_dt(issue.closed_at),
            merged_at=_fmt_dt(pr.merged_at),
            merge_commit_sha=pr.merge_commit_sha,
            head=head_ref,
            base=base_ref,
            draft=pr.draft,
            merged=pr.merged,
            mergeable=pr.mergeable,
            comments=comments_count,
            review_comments=review_comments_count,
            commits=commits_count,
            additions=additions,
            deletions=deletions,
            changed_files=changed_files,
        )

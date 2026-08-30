"""Pydantic schemas for GitHub Comment API responses."""

from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id


class CommentCreate(BaseModel):
    """Schema for creating a comment."""

    body: str


class CommentUpdate(BaseModel):
    """Schema for updating a comment."""

    body: str


class IssueCommentResponse(BaseModel):
    """GitHub-compatible issue comment JSON response."""

    id: int
    node_id: str
    url: str
    html_url: str
    body: str
    user: SimpleUser
    created_at: str
    updated_at: str
    issue_url: str

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_db(
        cls,
        comment,
        base_url: str,
        owner_login: str,
        repo_name: str,
        issue_number: int,
    ) -> "IssueCommentResponse":
        """Construct an IssueCommentResponse from a DB comment object."""
        api_base = f"{base_url}/api/v3"
        repo_url = f"{api_base}/repos/{owner_login}/{repo_name}"

        return cls(
            id=comment.id,
            node_id=_make_node_id("IssueComment", comment.id),
            url=f"{repo_url}/issues/comments/{comment.id}",
            html_url=f"{base_url}/{owner_login}/{repo_name}/issues/{issue_number}#issuecomment-{comment.id}",
            body=comment.body,
            user=SimpleUser.from_db(comment.user, base_url),
            created_at=_fmt_dt(comment.created_at),
            updated_at=_fmt_dt(comment.updated_at),
            issue_url=f"{repo_url}/issues/{issue_number}",
        )

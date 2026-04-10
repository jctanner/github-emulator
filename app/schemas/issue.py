"""Pydantic schemas for GitHub Issue API responses."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.label import LabelResponse
from app.schemas.milestone import MilestoneResponse
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id


class IssueCreate(BaseModel):
    """Schema for creating an issue."""

    title: str
    body: Optional[str] = None
    assignees: Optional[list[str]] = None
    labels: Optional[list[str]] = None
    milestone: Optional[int] = None


class IssueUpdate(BaseModel):
    """Schema for updating an issue."""

    title: Optional[str] = None
    body: Optional[str] = None
    state: Optional[str] = None  # "open" or "closed"
    state_reason: Optional[str] = None
    assignees: Optional[list[str]] = None
    labels: Optional[list[str]] = None
    milestone: Optional[int] = None


class IssueResponse(BaseModel):
    """Full GitHub-compatible issue JSON response."""

    url: str
    repository_url: str
    labels_url: str
    comments_url: str
    events_url: str
    html_url: str
    id: int
    node_id: str
    number: int
    title: str
    user: SimpleUser
    labels: list[LabelResponse] = []
    state: str = "open"
    locked: bool = False
    assignee: Optional[SimpleUser] = None
    assignees: list[SimpleUser] = []
    milestone: Optional[MilestoneResponse] = None
    comments: int = 0
    created_at: str
    updated_at: str
    closed_at: Optional[str] = None
    body: Optional[str] = None
    state_reason: Optional[str] = None
    closed_by: Optional[SimpleUser] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_db(
        cls,
        issue,
        base_url: str,
        owner_login: str,
        repo_name: str,
        comments_count: int = 0,
        milestone_response: Optional[MilestoneResponse] = None,
    ) -> "IssueResponse":
        """Construct an IssueResponse from a DB issue object."""
        api_base = f"{base_url}/api/v3"
        repo_url = f"{api_base}/repos/{owner_login}/{repo_name}"

        user_simple = SimpleUser.from_db(issue.user, base_url)

        # Build label responses
        label_responses = []
        if hasattr(issue, "labels") and issue.labels:
            label_responses = [
                LabelResponse.from_db(lbl, base_url, owner_login, repo_name)
                for lbl in issue.labels
            ]

        # Build assignees
        assignee_list = []
        if hasattr(issue, "assignees") and issue.assignees:
            assignee_list = [
                SimpleUser.from_db(a, base_url) for a in issue.assignees
            ]

        assignee = assignee_list[0] if assignee_list else None

        # Closed by
        closed_by = None
        if hasattr(issue, "closed_by") and issue.closed_by is not None:
            closed_by = SimpleUser.from_db(issue.closed_by, base_url)

        return cls(
            url=f"{repo_url}/issues/{issue.number}",
            repository_url=repo_url,
            labels_url=f"{repo_url}/issues/{issue.number}/labels{{/name}}",
            comments_url=f"{repo_url}/issues/{issue.number}/comments",
            events_url=f"{repo_url}/issues/{issue.number}/events",
            html_url=f"{base_url}/{owner_login}/{repo_name}/issues/{issue.number}",
            id=issue.id,
            node_id=_make_node_id("Issue", issue.id),
            number=issue.number,
            title=issue.title,
            user=user_simple,
            labels=label_responses,
            state=issue.state,
            locked=issue.locked,
            assignee=assignee,
            assignees=assignee_list,
            milestone=milestone_response,
            comments=comments_count,
            created_at=_fmt_dt(issue.created_at),
            updated_at=_fmt_dt(issue.updated_at),
            closed_at=_fmt_dt(issue.closed_at),
            body=issue.body,
            state_reason=issue.state_reason,
            closed_by=closed_by,
        )

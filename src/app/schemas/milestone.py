"""Pydantic schemas for GitHub Milestone API responses."""

import base64
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id


class MilestoneCreate(BaseModel):
    """Schema for creating a milestone."""

    title: str
    state: str = "open"
    description: Optional[str] = None
    due_on: Optional[str] = None


class MilestoneUpdate(BaseModel):
    """Schema for updating a milestone."""

    title: Optional[str] = None
    state: Optional[str] = None
    description: Optional[str] = None
    due_on: Optional[str] = None


class MilestoneResponse(BaseModel):
    """GitHub-compatible milestone JSON response."""

    url: str
    html_url: str
    labels_url: str
    id: int
    node_id: str
    number: int
    title: str
    description: Optional[str] = None
    creator: Optional[SimpleUser] = None
    open_issues: int = 0
    closed_issues: int = 0
    state: str = "open"
    created_at: str
    updated_at: str
    due_on: Optional[str] = None
    closed_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_db(
        cls,
        milestone,
        base_url: str,
        owner_login: str,
        repo_name: str,
        creator=None,
        open_issues: int = 0,
        closed_issues: int = 0,
    ) -> "MilestoneResponse":
        """Construct a MilestoneResponse from a DB milestone object."""
        api_base = f"{base_url}/api/v3"
        repo_url = f"{api_base}/repos/{owner_login}/{repo_name}"

        creator_simple = None
        if creator is not None:
            creator_simple = SimpleUser.from_db(creator, base_url)

        return cls(
            url=f"{repo_url}/milestones/{milestone.number}",
            html_url=f"{base_url}/{owner_login}/{repo_name}/milestone/{milestone.number}",
            labels_url=f"{repo_url}/milestones/{milestone.number}/labels",
            id=milestone.id,
            node_id=_make_node_id("Milestone", milestone.id),
            number=milestone.number,
            title=milestone.title,
            description=milestone.description,
            creator=creator_simple,
            open_issues=open_issues,
            closed_issues=closed_issues,
            state=milestone.state,
            created_at=_fmt_dt(milestone.created_at),
            updated_at=_fmt_dt(milestone.updated_at),
            due_on=_fmt_dt(milestone.due_on),
            closed_at=_fmt_dt(milestone.closed_at),
        )

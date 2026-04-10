"""Pydantic schemas for the GitHub Emulator API."""

from app.schemas.user import (
    UserBase,
    UserCreate,
    UserUpdate,
    UserResponse,
    SimpleUser,
)
from app.schemas.repository import (
    RepoCreate,
    RepoUpdate,
    RepoPermissions,
    RepoResponse,
)
from app.schemas.issue import (
    IssueCreate,
    IssueUpdate,
    IssueResponse,
)
from app.schemas.pull_request import (
    PRCreate,
    PRUpdate,
    PRMerge,
    PRBranchRef,
    PRResponse,
)
from app.schemas.comment import (
    CommentCreate,
    CommentUpdate,
    IssueCommentResponse,
)
from app.schemas.label import (
    LabelCreate,
    LabelUpdate,
    LabelResponse,
)
from app.schemas.milestone import (
    MilestoneCreate,
    MilestoneUpdate,
    MilestoneResponse,
)
from app.schemas.webhook import (
    WebhookConfig,
    WebhookConfigResponse,
    WebhookCreate,
    WebhookUpdate,
    WebhookResponse,
)
from app.schemas.event import (
    EventRepo,
    EventResponse,
)

__all__ = [
    # User
    "UserBase",
    "UserCreate",
    "UserUpdate",
    "UserResponse",
    "SimpleUser",
    # Repository
    "RepoCreate",
    "RepoUpdate",
    "RepoPermissions",
    "RepoResponse",
    # Issue
    "IssueCreate",
    "IssueUpdate",
    "IssueResponse",
    # Pull Request
    "PRCreate",
    "PRUpdate",
    "PRMerge",
    "PRBranchRef",
    "PRResponse",
    # Comment
    "CommentCreate",
    "CommentUpdate",
    "IssueCommentResponse",
    # Label
    "LabelCreate",
    "LabelUpdate",
    "LabelResponse",
    # Milestone
    "MilestoneCreate",
    "MilestoneUpdate",
    "MilestoneResponse",
    # Webhook
    "WebhookConfig",
    "WebhookConfigResponse",
    "WebhookCreate",
    "WebhookUpdate",
    "WebhookResponse",
    # Event
    "EventRepo",
    "EventResponse",
]

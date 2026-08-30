"""Typed contracts for repository settings clients."""

from typing import Any

from pydantic import BaseModel

from app.schemas.user import SimpleUser


class CollaboratorResponse(SimpleUser):
    permissions: dict[str, bool]
    role_name: str


class InstallationAccountResponse(BaseModel):
    login: str
    type: str


class InstallationResponse(BaseModel):
    id: int
    app_id: str
    app_slug: str
    target_type: str
    account: InstallationAccountResponse
    repository_selection: str
    access_tokens_url: str
    html_url: str
    created_at: str
    updated_at: str
    repositories: list[str]
    permissions: dict[str, str]
    events: list[str]


class BranchProtectionResponse(BaseModel):
    url: str
    required_status_checks: dict[str, Any] | None = None
    required_pull_request_reviews: dict[str, Any] | None = None
    enforce_admins: dict[str, Any]
    restrictions: dict[str, Any] | None = None
    required_linear_history: dict[str, bool]
    allow_force_pushes: dict[str, bool]
    allow_deletions: dict[str, bool]
    block_creations: dict[str, bool]
    required_conversation_resolution: dict[str, bool]
    lock_branch: dict[str, bool]
    allow_fork_syncing: dict[str, bool]
    required_signatures: dict[str, bool]

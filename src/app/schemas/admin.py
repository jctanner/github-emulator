"""Typed JSON contracts for the site-administration frontend."""

from typing import Any

from pydantic import BaseModel


class AdminSummaryResponse(BaseModel):
    users: int
    organizations: int
    repositories: int
    issues: int
    workflow_runs: int
    runners: int
    imports: int


class AdminUserResponse(BaseModel):
    id: int
    login: str
    name: str | None = None
    email: str | None = None
    site_admin: bool
    type: str
    created_at: str | None = None


class AdminOrganizationResponse(BaseModel):
    id: int
    login: str
    name: str | None = None
    description: str | None = None
    email: str | None = None
    created_at: str | None = None


class AdminRepositoryResponse(BaseModel):
    id: int
    full_name: str
    name: str
    private: bool
    owner_type: str
    default_branch: str
    created_at: str | None = None


class AdminTokenResponse(BaseModel):
    id: int
    user_id: int
    owner: str
    name: str
    token_prefix: str | None = None
    scopes: list[str]
    created_at: str | None = None
    last_used_at: str | None = None


class AdminTokenCreatedResponse(AdminTokenResponse):
    token: str


class AdminRunnerResponse(BaseModel):
    id: int
    name: str
    os: str
    status: str
    busy: bool
    labels: list[str]
    scope: str
    last_heartbeat: str | None = None


class AdminImportResponse(BaseModel):
    id: int
    job_type: str
    status: str
    source_url: str | None = None
    repo_name: str | None = None
    owner: str
    error_message: str | None = None
    repo_count: int | None = None
    completed_count: int
    created_at: str | None = None
    completed_at: str | None = None


class AdminIssueResponse(BaseModel):
    id: int
    repository: str
    number: int
    title: str
    state: str
    is_pull_request: bool
    created_at: str | None = None


class AdminInstallationResponse(BaseModel):
    id: int
    app_id: str
    owner: str
    repo: str | None = None
    repositories: list[str]
    created_at: str | None = None


class AdminAppResponse(BaseModel):
    app_id: str
    name: str
    slug: str
    owner: str
    client_id: str
    installations_count: int
    created_at: str | None = None
    installations: list[AdminInstallationResponse] | None = None
    has_private_key: bool | None = None
    private_key: str | None = None

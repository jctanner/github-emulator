"""Typed contracts for browser-visible GitHub Actions resources."""

from typing import Any

from pydantic import BaseModel

from app.schemas.user import SimpleUser


class WorkflowResponse(BaseModel):
    id: int
    node_id: str
    name: str
    path: str
    state: str
    created_at: str
    updated_at: str
    url: str
    html_url: str
    badge_url: str


class WorkflowListResponse(BaseModel):
    total_count: int
    workflows: list[WorkflowResponse]


class WorkflowRunResponse(BaseModel):
    id: int
    name: str
    head_branch: str
    head_sha: str
    run_number: int
    run_attempt: int
    event: str
    status: str
    conclusion: str | None = None
    workflow_id: int
    url: str
    html_url: str
    created_at: str
    updated_at: str
    actor: SimpleUser | None = None


class WorkflowRunListResponse(BaseModel):
    total_count: int
    workflow_runs: list[WorkflowRunResponse]


class WorkflowJobResponse(BaseModel):
    id: int
    run_id: int
    name: str
    workflow_name: str | None = None
    status: str
    conclusion: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    steps: list[dict[str, Any]] = []
    runner_name: str | None = None
    runner_id: int | None = None
    labels: list[str] = []
    run_attempt: int = 1
    needs: list[str] = []
    permissions: dict[str, Any] = {}
    url: str
    html_url: str
    logs_url: str


class WorkflowJobListResponse(BaseModel):
    total_count: int
    jobs: list[WorkflowJobResponse]


class RunnerLabelResponse(BaseModel):
    id: int
    name: str
    type: str


class RunnerResponse(BaseModel):
    id: int
    name: str
    os: str
    status: str
    busy: bool
    labels: list[RunnerLabelResponse] = []


class RunnerListResponse(BaseModel):
    total_count: int
    runners: list[RunnerResponse]

"""Pydantic schemas for GitHub Organization API responses."""

from typing import Optional

from pydantic import BaseModel


class OrganizationResponse(BaseModel):
    login: str
    id: int
    node_id: str
    url: str
    repos_url: str
    events_url: str
    hooks_url: str
    issues_url: str
    members_url: str
    public_members_url: str
    avatar_url: str
    description: Optional[str] = None
    name: Optional[str] = None
    company: Optional[str] = None
    blog: str = ""
    location: Optional[str] = None
    email: Optional[str] = None
    is_verified: bool = False
    has_organization_projects: bool = True
    has_repository_projects: bool = True
    public_repos: int = 0
    public_gists: int = 0
    followers: int = 0
    following: int = 0
    html_url: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    type: str = "Organization"

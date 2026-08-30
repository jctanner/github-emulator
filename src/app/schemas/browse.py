"""Typed response contracts used by repository browsing clients."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.repository import RepoResponse


class BranchCommit(BaseModel):
    sha: str
    url: str


class BranchResponse(BaseModel):
    name: str
    commit: BranchCommit
    protected: bool
    protection: dict[str, Any]
    protection_url: str


class GitIdentity(BaseModel):
    name: str
    email: str
    date: str


class CommitDetails(BaseModel):
    author: GitIdentity
    committer: GitIdentity
    message: str
    tree: dict[str, str]
    url: str
    comment_count: int = 0
    verification: dict[str, Any]


class CommitResponse(BaseModel):
    sha: str
    node_id: str
    commit: CommitDetails
    url: str
    html_url: str
    comments_url: str
    author: dict[str, Any] | None = None
    committer: dict[str, Any] | None = None
    parents: list[dict[str, str]] = []


class ContentLinks(BaseModel):
    self: str
    git: str
    html: str


class ContentResponse(BaseModel):
    type: Literal["file", "dir"]
    size: int
    name: str
    path: str
    sha: str
    url: str
    git_url: str
    html_url: str
    download_url: str | None = None
    encoding: str | None = None
    content: str | None = None
    links: ContentLinks | None = Field(default=None, alias="_links")

    model_config = ConfigDict(populate_by_name=True)


class TagResponse(BaseModel):
    name: str
    commit: BranchCommit
    zipball_url: str
    tarball_url: str
    node_id: str


class RepositorySearchResponse(BaseModel):
    total_count: int
    incomplete_results: bool
    items: list[RepoResponse]


class GenericSearchResponse(BaseModel):
    total_count: int
    incomplete_results: bool
    items: list[dict[str, Any]]

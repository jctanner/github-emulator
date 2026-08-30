"""Pydantic schemas for GitHub Repository API responses."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id


class RepoCreate(BaseModel):
    """Schema for creating a repository."""

    name: str
    description: Optional[str] = None
    private: bool = False
    auto_init: bool = False
    default_branch: str = "main"
    has_issues: bool = True
    has_wiki: bool = True
    has_projects: bool = True
    homepage: Optional[str] = None
    is_template: bool = False


class RepoUpdate(BaseModel):
    """Schema for updating a repository."""

    name: Optional[str] = None
    description: Optional[str] = None
    private: Optional[bool] = None
    default_branch: Optional[str] = None
    has_issues: Optional[bool] = None
    has_wiki: Optional[bool] = None
    has_projects: Optional[bool] = None
    homepage: Optional[str] = None
    archived: Optional[bool] = None
    visibility: Optional[str] = None
    allow_forking: Optional[bool] = None
    web_commit_signoff_required: Optional[bool] = None


class RepoPermissions(BaseModel):
    """Repository permissions object."""

    admin: bool = False
    maintain: bool = False
    push: bool = False
    triage: bool = False
    pull: bool = True


class RepoResponse(BaseModel):
    """Full GitHub-compatible repository JSON response."""

    # Core fields
    id: int
    node_id: str
    name: str
    full_name: str
    private: bool
    owner: SimpleUser
    html_url: str
    description: Optional[str] = None
    fork: bool = False
    url: str

    # URL template fields
    forks_url: str
    keys_url: str
    collaborators_url: str
    teams_url: str
    hooks_url: str
    issue_events_url: str
    events_url: str
    assignees_url: str
    branches_url: str
    tags_url: str
    blobs_url: str
    git_tags_url: str
    git_refs_url: str
    trees_url: str
    statuses_url: str
    languages_url: str
    stargazers_url: str
    contributors_url: str
    subscribers_url: str
    subscription_url: str
    commits_url: str
    git_commits_url: str
    comments_url: str
    issue_comment_url: str
    contents_url: str
    compare_url: str
    merges_url: str
    archive_url: str
    downloads_url: str
    issues_url: str
    pulls_url: str
    milestones_url: str
    notifications_url: str
    labels_url: str
    releases_url: str
    deployments_url: str

    # Git URLs
    git_url: str
    ssh_url: str
    clone_url: str
    svn_url: str
    mirror_url: Optional[str] = None

    # Metadata
    homepage: Optional[str] = None
    language: Optional[str] = None
    forks_count: int = 0
    stargazers_count: int = 0
    watchers_count: int = 0
    size: int = 0
    default_branch: str = "main"
    open_issues_count: int = 0
    topics: list[str] = []
    has_issues: bool = True
    has_projects: bool = True
    has_wiki: bool = True
    has_pages: bool = False
    has_downloads: bool = True
    has_discussions: bool = False
    archived: bool = False
    disabled: bool = False
    visibility: str = "public"
    allow_forking: bool = True
    is_template: bool = False
    web_commit_signoff_required: bool = False
    license: Optional[dict] = None

    # Timestamps (ISO 8601 strings)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    pushed_at: Optional[str] = None

    # Permissions
    permissions: Optional[RepoPermissions] = None

    # Aliases (GitHub returns these as duplicates)
    forks: int = 0
    open_issues: int = 0
    watchers: int = 0

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_db(
        cls,
        repo,
        owner_user,
        base_url: str,
        permissions: Optional[dict] = None,
    ) -> "RepoResponse":
        """Construct a full RepoResponse from a DB repo object and its owner."""
        api_base = f"{base_url}/api/v3"
        repo_url = f"{api_base}/repos/{repo.full_name}"
        html_url = f"{base_url}/{repo.full_name}"

        owner_simple = SimpleUser.from_db(owner_user, base_url)

        perm = None
        if permissions is not None:
            perm = RepoPermissions(**permissions)

        topics = repo.topics if repo.topics is not None else []

        return cls(
            id=repo.id,
            node_id=_make_node_id("Repository", repo.id),
            name=repo.name,
            full_name=repo.full_name,
            private=repo.private,
            owner=owner_simple,
            html_url=html_url,
            description=repo.description,
            fork=repo.fork,
            url=repo_url,
            # URL template fields
            forks_url=f"{repo_url}/forks",
            keys_url=f"{repo_url}/keys{{/key_id}}",
            collaborators_url=f"{repo_url}/collaborators{{/collaborator}}",
            teams_url=f"{repo_url}/teams",
            hooks_url=f"{repo_url}/hooks",
            issue_events_url=f"{repo_url}/issues/events{{/number}}",
            events_url=f"{repo_url}/events",
            assignees_url=f"{repo_url}/assignees{{/user}}",
            branches_url=f"{repo_url}/branches{{/branch}}",
            tags_url=f"{repo_url}/tags",
            blobs_url=f"{repo_url}/git/blobs{{/sha}}",
            git_tags_url=f"{repo_url}/git/tags{{/sha}}",
            git_refs_url=f"{repo_url}/git/refs{{/sha}}",
            trees_url=f"{repo_url}/git/trees{{/sha}}",
            statuses_url=f"{repo_url}/statuses/{{sha}}",
            languages_url=f"{repo_url}/languages",
            stargazers_url=f"{repo_url}/stargazers",
            contributors_url=f"{repo_url}/contributors",
            subscribers_url=f"{repo_url}/subscribers",
            subscription_url=f"{repo_url}/subscription",
            commits_url=f"{repo_url}/commits{{/sha}}",
            git_commits_url=f"{repo_url}/git/commits{{/sha}}",
            comments_url=f"{repo_url}/comments{{/number}}",
            issue_comment_url=f"{repo_url}/issues/comments{{/number}}",
            contents_url=f"{repo_url}/contents/{{+path}}",
            compare_url=f"{repo_url}/compare/{{base}}...{{head}}",
            merges_url=f"{repo_url}/merges",
            archive_url=f"{repo_url}/{{archive_format}}{{/ref}}",
            downloads_url=f"{repo_url}/downloads",
            issues_url=f"{repo_url}/issues{{/number}}",
            pulls_url=f"{repo_url}/pulls{{/number}}",
            milestones_url=f"{repo_url}/milestones{{/number}}",
            notifications_url=f"{repo_url}/notifications{{?since,all,participating}}",
            labels_url=f"{repo_url}/labels{{/name}}",
            releases_url=f"{repo_url}/releases{{/id}}",
            deployments_url=f"{repo_url}/deployments",
            # Git URLs
            git_url=f"git://{base_url.split('://', 1)[-1]}/{repo.full_name}.git",
            ssh_url=f"git@{base_url.split('://', 1)[-1]}:{repo.full_name}.git",
            clone_url=f"{base_url}/{repo.full_name}.git",
            svn_url=f"{base_url}/{repo.full_name}",
            mirror_url=None,
            # Metadata
            homepage=repo.homepage,
            language=repo.language,
            forks_count=repo.forks_count,
            stargazers_count=repo.stargazers_count,
            watchers_count=repo.watchers_count,
            size=repo.size,
            default_branch=repo.default_branch,
            open_issues_count=repo.open_issues_count,
            topics=topics,
            has_issues=repo.has_issues,
            has_projects=repo.has_projects,
            has_wiki=repo.has_wiki,
            has_pages=repo.has_pages,
            has_downloads=repo.has_downloads,
            has_discussions=repo.has_discussions,
            archived=repo.archived,
            disabled=repo.disabled,
            visibility=repo.visibility,
            allow_forking=repo.allow_forking,
            is_template=repo.is_template,
            web_commit_signoff_required=repo.web_commit_signoff_required,
            license=None,
            # Timestamps
            created_at=_fmt_dt(repo.created_at),
            updated_at=_fmt_dt(repo.updated_at),
            pushed_at=_fmt_dt(repo.pushed_at),
            # Permissions
            permissions=perm,
            # Aliases
            forks=repo.forks_count,
            open_issues=repo.open_issues_count,
            watchers=repo.watchers_count,
        )

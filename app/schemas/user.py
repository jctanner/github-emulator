"""Pydantic schemas for GitHub User API responses."""

import base64
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


def _fmt_dt(dt: Optional[datetime]) -> Optional[str]:
    """Format a datetime as ISO 8601 string with trailing Z."""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_node_id(type_name: str, id_value: int) -> str:
    """Generate a node_id as base64('Type:id')."""
    raw = f"{type_name}:{id_value}"
    return base64.b64encode(raw.encode()).decode()


class UserBase(BaseModel):
    """Base user fields."""

    login: str
    name: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    blog: Optional[str] = None
    twitter_username: Optional[str] = None


class UserCreate(BaseModel):
    """Schema for creating a new user."""

    login: str
    password: str
    name: Optional[str] = None
    email: Optional[str] = None
    site_admin: bool = False


class UserUpdate(BaseModel):
    """Schema for updating a user."""

    name: Optional[str] = None
    email: Optional[str] = None
    bio: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    blog: Optional[str] = None
    twitter_username: Optional[str] = None


class SimpleUser(BaseModel):
    """Simplified user object for embedding in other responses."""

    login: str
    id: int
    node_id: str
    avatar_url: str
    gravatar_id: str = ""
    url: str
    html_url: str
    type: str = "User"
    site_admin: bool = False

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_db(cls, user, base_url: str) -> "SimpleUser":
        """Construct a SimpleUser from a DB user object."""
        avatar = user.avatar_url or f"{base_url}/avatars/{user.login}"
        return cls(
            login=user.login,
            id=user.id,
            node_id=_make_node_id("User", user.id),
            avatar_url=avatar,
            gravatar_id="",
            url=f"{base_url}/api/v3/users/{user.login}",
            html_url=f"{base_url}/{user.login}",
            type=getattr(user, "type", "User") or "User",
            site_admin=getattr(user, "site_admin", False),
        )


class UserResponse(BaseModel):
    """Full GitHub-compatible user JSON response."""

    login: str
    id: int
    node_id: str
    avatar_url: str
    gravatar_id: str = ""
    url: str
    html_url: str
    followers_url: str
    following_url: str
    gists_url: str
    starred_url: str
    subscriptions_url: str
    organizations_url: str
    repos_url: str
    events_url: str
    received_events_url: str
    type: str = "User"
    site_admin: bool = False

    name: Optional[str] = None
    company: Optional[str] = None
    blog: Optional[str] = None
    location: Optional[str] = None
    email: Optional[str] = None
    hireable: Optional[bool] = None
    bio: Optional[str] = None
    twitter_username: Optional[str] = None

    public_repos: int = 0
    public_gists: int = 0
    followers: int = 0
    following: int = 0

    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_db(cls, user, base_url: str) -> "UserResponse":
        """Construct a full UserResponse from a DB user object."""
        avatar = user.avatar_url or f"{base_url}/avatars/{user.login}"
        api_base = f"{base_url}/api/v3"
        user_url = f"{api_base}/users/{user.login}"

        # Count public repos if the relationship is loaded
        public_repos = 0
        if hasattr(user, "repositories") and user.repositories is not None:
            public_repos = sum(
                1 for r in user.repositories if not r.private
            )

        return cls(
            login=user.login,
            id=user.id,
            node_id=_make_node_id("User", user.id),
            avatar_url=avatar,
            gravatar_id="",
            url=user_url,
            html_url=f"{base_url}/{user.login}",
            followers_url=f"{user_url}/followers",
            following_url=f"{user_url}/following{{/other_user}}",
            gists_url=f"{user_url}/gists{{/gist_id}}",
            starred_url=f"{user_url}/starred{{/owner}}{{/repo}}",
            subscriptions_url=f"{user_url}/subscriptions",
            organizations_url=f"{user_url}/orgs",
            repos_url=f"{user_url}/repos",
            events_url=f"{user_url}/events{{/privacy}}",
            received_events_url=f"{user_url}/received_events",
            type=getattr(user, "type", "User") or "User",
            site_admin=getattr(user, "site_admin", False),
            name=user.name,
            company=user.company,
            blog=user.blog or "",
            location=user.location,
            email=user.email,
            hireable=None,
            bio=user.bio,
            twitter_username=user.twitter_username,
            public_repos=public_repos,
            public_gists=0,
            followers=0,
            following=0,
            created_at=_fmt_dt(user.created_at),
            updated_at=_fmt_dt(user.updated_at),
        )

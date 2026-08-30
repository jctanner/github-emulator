"""Pydantic schemas for GitHub Event API responses."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id


class EventRepo(BaseModel):
    """Repo object embedded in event responses."""

    id: int
    name: str
    url: str


class EventResponse(BaseModel):
    """GitHub-compatible event JSON response."""

    id: str
    type: str
    actor: SimpleUser
    repo: EventRepo
    payload: dict[str, Any] = {}
    public: bool = True
    created_at: str
    org: Optional[SimpleUser] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_db(
        cls,
        event,
        base_url: str,
        actor_user=None,
        repo=None,
        org_user=None,
    ) -> "EventResponse":
        """Construct an EventResponse from a DB event object.

        Args:
            event: The DB Event object.
            base_url: The server base URL.
            actor_user: The User object for the actor (if not passed,
                        event.actor must be loaded).
            repo: The Repository object (if not passed, the repo fields
                  will be derived from event attributes).
            org_user: Optional organization user for the org field.
        """
        api_base = f"{base_url}/api/v3"

        actor = actor_user if actor_user is not None else event.actor
        actor_simple = SimpleUser.from_db(actor, base_url)

        # Build repo info
        if repo is not None:
            event_repo = EventRepo(
                id=repo.id,
                name=repo.full_name,
                url=f"{api_base}/repos/{repo.full_name}",
            )
        else:
            event_repo = EventRepo(
                id=event.repo_id or 0,
                name="unknown/unknown",
                url=f"{api_base}/repos/unknown/unknown",
            )

        org_simple = None
        if org_user is not None:
            org_simple = SimpleUser.from_db(org_user, base_url)

        payload = event.payload if event.payload is not None else {}

        return cls(
            id=str(event.id),
            type=event.type,
            actor=actor_simple,
            repo=event_repo,
            payload=payload,
            public=event.public,
            created_at=_fmt_dt(event.created_at),
            org=org_simple,
        )

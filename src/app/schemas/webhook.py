"""Pydantic schemas for GitHub Webhook API responses."""

from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.user import _fmt_dt, _make_node_id


class WebhookConfig(BaseModel):
    """Webhook configuration object."""

    url: str
    content_type: str = "json"
    secret: Optional[str] = None
    insecure_ssl: str = "0"


class WebhookConfigResponse(BaseModel):
    """Webhook config in response (no secret exposed)."""

    url: str
    content_type: str = "json"
    insecure_ssl: str = "0"


class WebhookCreate(BaseModel):
    """Schema for creating a webhook."""

    config: WebhookConfig
    events: list[str] = ["push"]
    active: bool = True


class WebhookUpdate(BaseModel):
    """Schema for updating a webhook."""

    config: Optional[WebhookConfig] = None
    events: Optional[list[str]] = None
    active: Optional[bool] = None
    add_events: Optional[list[str]] = None
    remove_events: Optional[list[str]] = None


class WebhookResponse(BaseModel):
    """GitHub-compatible webhook JSON response."""

    id: int
    url: str
    test_url: str
    ping_url: str
    name: str = "web"
    events: list[str] = ["push"]
    active: bool = True
    config: WebhookConfigResponse
    updated_at: str
    created_at: str
    type: str = "Repository"

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_db(
        cls,
        webhook,
        base_url: str,
        owner_login: str,
        repo_name: str,
    ) -> "WebhookResponse":
        """Construct a WebhookResponse from a DB webhook object."""
        api_base = f"{base_url}/api/v3"
        repo_url = f"{api_base}/repos/{owner_login}/{repo_name}"
        hook_url = f"{repo_url}/hooks/{webhook.id}"

        insecure_ssl_val = "1" if webhook.insecure_ssl else "0"

        config_resp = WebhookConfigResponse(
            url=webhook.url,
            content_type=webhook.content_type,
            insecure_ssl=insecure_ssl_val,
        )

        events = webhook.events if webhook.events is not None else ["push"]

        return cls(
            id=webhook.id,
            url=hook_url,
            test_url=f"{hook_url}/tests",
            ping_url=f"{hook_url}/pings",
            name="web",
            events=events,
            active=webhook.active,
            config=config_resp,
            updated_at=_fmt_dt(webhook.updated_at),
            created_at=_fmt_dt(webhook.created_at),
            type="Repository",
        )

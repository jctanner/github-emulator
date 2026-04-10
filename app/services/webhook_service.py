"""Webhook management and delivery service."""

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Webhook, WebhookDelivery


async def create_webhook(
    db: AsyncSession,
    repo_id: int,
    url: str,
    events: list[str],
    secret: Optional[str] = None,
    content_type: str = "json",
    active: bool = True,
) -> Webhook:
    """Create a new webhook for a repository.

    Args:
        db: Async database session.
        repo_id: The repository ID.
        url: The webhook payload URL.
        events: List of event types to subscribe to.
        secret: Optional shared secret for HMAC signing.
        content_type: Content type ("json" or "form").
        active: Whether the webhook is active.

    Returns:
        The newly created Webhook.
    """
    webhook = Webhook(
        repo_id=repo_id,
        url=url,
        secret=secret,
        content_type=content_type,
        events=events,
        active=active,
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    return webhook


async def deliver_webhook(
    db: AsyncSession,
    webhook: Webhook,
    event: str,
    payload: dict,
) -> WebhookDelivery:
    """Deliver a webhook payload to the configured URL.

    Sends an HTTP POST with GitHub-compatible headers including
    X-GitHub-Event and X-Hub-Signature-256 (if a secret is configured).

    Args:
        db: Async database session.
        webhook: The webhook to deliver.
        event: The event type (e.g. "push", "issues").
        payload: The event payload dict.

    Returns:
        The WebhookDelivery record.
    """
    delivery_id = str(uuid.uuid4())
    body_str = json.dumps(payload, separators=(",", ":"), default=str)

    # Build headers
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "GitHub-Emulator-Hookshot",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Hook-ID": str(webhook.id),
    }

    # Compute HMAC signature if secret is set
    if webhook.secret:
        signature = hmac.new(
            webhook.secret.encode("utf-8"),
            body_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={signature}"

    # Attempt delivery
    status_code = None
    response_headers = None
    response_body = None
    success = False
    start_time = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                webhook.url,
                content=body_str,
                headers=headers,
            )
            status_code = response.status_code
            response_headers = dict(response.headers)
            response_body = response.text
            success = 200 <= status_code < 300
    except Exception:
        status_code = 0
        response_body = "Connection failed"
        success = False

    duration = time.monotonic() - start_time

    # Extract action from payload if present
    action = payload.get("action") if isinstance(payload, dict) else None

    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=event,
        action=action,
        status_code=status_code,
        request_headers=headers,
        request_body=body_str,
        response_headers=response_headers,
        response_body=response_body,
        delivered_at=datetime.utcnow(),
        duration=duration,
        success=success,
    )
    db.add(delivery)
    await db.commit()
    await db.refresh(delivery)
    return delivery


async def trigger_webhooks(
    db: AsyncSession,
    repo_id: int,
    event: str,
    payload: dict,
) -> None:
    """Find all matching webhooks for a repo and event, and deliver to each.

    Args:
        db: Async database session.
        repo_id: The repository ID.
        event: The event type (e.g. "push", "issues").
        payload: The event payload dict.
    """
    result = await db.execute(
        select(Webhook).where(
            Webhook.repo_id == repo_id,
            Webhook.active == True,  # noqa: E712
        )
    )
    webhooks = result.scalars().all()

    for webhook in webhooks:
        # Check if the webhook is subscribed to this event or to "*"
        if "*" in webhook.events or event in webhook.events:
            await deliver_webhook(db, webhook, event, payload)

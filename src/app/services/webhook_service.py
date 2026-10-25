"""Webhook management and delivery service.

Repository activity reaches hooks through :func:`schedule_webhooks`, which
``workflow_service.dispatch_event`` calls for every event it receives. A
delivery runs as a background task after the triggering request, as on
GitHub, so a slow or unreachable receiver never delays the API.
"""

import asyncio
import copy
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Webhook, WebhookDelivery

logger = logging.getLogger(__name__)

# Actions events whose webhook counterpart has another name. GitHub sends a
# pull_request webhook for the activity that runs pull_request_target
# workflows; there is no pull_request_target webhook.
WEBHOOK_EVENT_NAMES = {"pull_request_target": "pull_request"}
# Actions-only triggers with no webhook.
NON_WEBHOOK_EVENTS = {"schedule", "workflow_call"}

DELIVERY_TIMEOUT_SECONDS = 10.0
RECORD_ATTEMPTS = 5
# Tests replace this with an httpx.MockTransport.
transport: httpx.AsyncBaseTransport | None = None
# Deliveries in flight; the set keeps their tasks from being collected.
_pending: set[asyncio.Task] = set()


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


@dataclass(frozen=True)
class HookTarget:
    """What a delivery needs from a hook, detached from any session."""

    id: int
    repo_id: int | None
    org_id: int | None
    url: str
    secret: str | None
    content_type: str
    insecure_ssl: bool

    @classmethod
    def of(cls, hook: Webhook) -> "HookTarget":
        return cls(
            id=hook.id,
            repo_id=hook.repo_id,
            org_id=hook.org_id,
            url=hook.url,
            secret=hook.secret,
            content_type=hook.content_type or "json",
            insecure_ssl=bool(hook.insecure_ssl),
        )


async def _post(target: HookTarget, event: str, payload: dict) -> WebhookDelivery:
    """POST one delivery and describe it as an unsaved WebhookDelivery.

    Sends GitHub-compatible headers, including X-GitHub-Event and, when the
    hook has a secret, X-Hub-Signature-256 over the exact body sent.
    """
    delivery_id = str(uuid.uuid4())
    payload_json = json.dumps(payload, separators=(",", ":"), default=str)
    if target.content_type == "form":
        # GitHub's "form" content type: the JSON in a ``payload`` field.
        content_type = "application/x-www-form-urlencoded"
        body_str = urlencode({"payload": payload_json})
    else:
        content_type = "application/json"
        body_str = payload_json

    headers = {
        "Content-Type": content_type,
        "User-Agent": "GitHub-Emulator-Hookshot",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Hook-ID": str(target.id),
        "X-GitHub-Hook-Installation-Target-Type": "organization" if target.org_id else "repository",
        "X-GitHub-Hook-Installation-Target-ID": str(target.org_id or target.repo_id),
    }
    if target.secret:
        signature = hmac.new(
            target.secret.encode("utf-8"),
            body_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={signature}"

    status_code = None
    response_headers = None
    response_body = None
    success = False
    start_time = time.monotonic()
    try:
        async with httpx.AsyncClient(
            timeout=DELIVERY_TIMEOUT_SECONDS,
            transport=transport,
            verify=not target.insecure_ssl,
        ) as client:
            response = await client.post(target.url, content=body_str, headers=headers)
            status_code = response.status_code
            response_headers = dict(response.headers)
            response_body = response.text
            success = 200 <= status_code < 300
    except Exception as exc:
        status_code = 0
        response_body = f"Connection failed: {type(exc).__name__}"

    return WebhookDelivery(
        webhook_id=target.id,
        event=event,
        action=payload.get("action") if isinstance(payload, dict) else None,
        status_code=status_code,
        request_headers=headers,
        request_body=body_str,
        response_headers=response_headers,
        response_body=response_body,
        delivered_at=datetime.utcnow(),
        duration=time.monotonic() - start_time,
        success=success,
    )


async def deliver_webhook(
    db: AsyncSession,
    webhook: Webhook,
    event: str,
    payload: dict,
) -> WebhookDelivery:
    """Deliver a payload to one hook now, and record the delivery."""
    delivery = await _post(HookTarget.of(webhook), event, payload)
    db.add(delivery)
    await db.commit()
    await db.refresh(delivery)
    return delivery


def subscribed(webhook: Webhook, event: str) -> bool:
    """Whether an active hook receives ``event``; ``ping`` reaches every hook."""
    events = webhook.events or ["push"]
    return bool(webhook.active) and (event == "ping" or "*" in events or event in events)


def webhook_event_name(event: str) -> str | None:
    """The webhook event for an Actions event, or None if it has none."""
    if event in NON_WEBHOOK_EVENTS:
        return None
    return WEBHOOK_EVENT_NAMES.get(event, event)


async def schedule_webhooks(
    db: AsyncSession,
    repo_id: int,
    org_id: int | None,
    event: str,
    payload: dict,
) -> None:
    """Deliver ``event`` to the repository's hooks, and its organization's,
    in the background.

    The subscribed hooks are found now, on the caller's session, so an
    activity on a repository without hooks starts nothing. The payload is
    copied now too, so later changes by the caller (Actions adds ``ref`` and
    ``after``) do not reach the hooks.
    """
    name = webhook_event_name(event)
    if name is None:
        return
    owners = [Webhook.repo_id == repo_id]
    if org_id is not None:
        owners.append(Webhook.org_id == org_id)
    hooks = (await db.execute(select(Webhook).where(or_(*owners)))).scalars().all()
    targets = [HookTarget.of(hook) for hook in hooks if subscribed(hook, name)]
    if targets:
        _start(db, targets, name, copy.deepcopy(payload))


def schedule_hook_delivery(db: AsyncSession, hook: Webhook, event: str, payload: dict) -> None:
    """Deliver ``event`` to one hook in the background, as ping and test do."""
    _start(db, [HookTarget.of(hook)], event, copy.deepcopy(payload))


def _start(db: AsyncSession, targets: list[HookTarget], event: str, payload: dict) -> None:
    sessions = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    task = asyncio.get_running_loop().create_task(
        _deliver(sessions, targets, event, payload),
        name=f"webhook-{event}",
    )
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _deliver(sessions, targets: list[HookTarget], event: str, payload: dict) -> None:
    """Send to every target with no database connection open, then record
    the deliveries in one short transaction."""
    try:
        deliveries = []
        for target in targets:
            delivery = await _post(target, event, payload)
            if not delivery.success:
                logger.warning(
                    "Webhook %s delivery of %s to %s failed: %s %s",
                    target.id, event, target.url, delivery.status_code, delivery.response_body,
                )
            deliveries.append(delivery)
        for attempt in range(RECORD_ATTEMPTS):
            try:
                async with sessions() as db:
                    # A hook deleted meanwhile keeps no delivery history.
                    live = set((await db.execute(
                        select(Webhook.id).where(Webhook.id.in_([t.id for t in targets]))
                    )).scalars().all())
                    db.add_all([d for d in deliveries if d.webhook_id in live])
                    await db.commit()
                return
            except OperationalError:
                # SQLite reported a lock; the request that caused the event
                # may still be writing.
                if attempt == RECORD_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(0.05 * (attempt + 1))
    except Exception:
        logger.exception("Webhook delivery of %s failed", event)


async def wait_for_deliveries() -> None:
    """Wait until every scheduled delivery has finished."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)

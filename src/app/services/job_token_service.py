"""Issue and validate scoped tokens carried by real Actions job messages."""

import base64
import hmac
import json
import time

from sqlalchemy import select

from app.config import settings
from app.models.actions import WorkflowJob, WorkflowRun


def _base64url_json(value: dict) -> str:
    encoded = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("ascii")


def _decode_json(value: str) -> dict:
    padded = value + "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))


def issue_job_token(job: WorkflowJob) -> str:
    """Issue the JWT-shaped OAuth token required by the upstream runner."""
    now = int(time.time())
    header = _base64url_json({"typ": "JWT", "alg": "HS256"})
    payload = _base64url_json({
        "iss": "github-emulator",
        "aud": "github-emulator",
        "sub": f"job:{job.id}",
        "nbf": now - 60,
        "iat": now,
        "exp": now + 3600,
        "orch_id": str(job.run_id),
    })
    signing_input = f"{header}.{payload}"
    digest = hmac.digest(
        settings.SECRET_KEY.encode(), signing_input.encode(), "sha256"
    )
    signature = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"{signing_input}.{signature}"


async def validate_job_token(db, token: str):
    try:
        header_value, payload_value, signature = token.split(".")
        header = _decode_json(header_value)
        payload = _decode_json(payload_value)
        if header.get("alg") != "HS256" or payload.get("iss") != "github-emulator":
            return None
        signing_input = f"{header_value}.{payload_value}"
        digest = hmac.digest(
            settings.SECRET_KEY.encode(), signing_input.encode(), "sha256"
        )
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        if not hmac.compare_digest(signature, expected):
            return None
        subject = str(payload.get("sub", ""))
        if not subject.startswith("job:"):
            return None
        job_id = int(subject.split(":", 1)[1])
        now = int(time.time())
        if int(payload["exp"]) < now or int(payload.get("nbf", 0)) > now:
            return None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None

    result = await db.execute(
        select(WorkflowJob, WorkflowRun)
        .join(WorkflowRun, WorkflowJob.run_id == WorkflowRun.id)
        .where(WorkflowJob.id == job_id)
    )
    row = result.first()
    if row is None:
        return None
    job, run = row
    return job, run

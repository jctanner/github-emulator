"""Actions runner management endpoints -- registration tokens, runner CRUD."""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import AuthUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.actions import EnterpriseRunnerRegistrationToken, Runner, RegistrationToken, RunnerSession, WorkflowJob
from app.schemas.user import _fmt_dt
from app.schemas.actions import RunnerListResponse, RunnerResponse

router = APIRouter(tags=["actions-runners"])

BASE = settings.BASE_URL


def _require_enterprise(enterprise: str, user: AuthUser) -> None:
    if enterprise != settings.ENTERPRISE_SLUG:
        raise HTTPException(status_code=404, detail="Not Found")
    if not user.site_admin:
        raise HTTPException(status_code=403, detail="Site administrator required")


def normalise_runner_os(value: str | None) -> str:
    """Reduce whatever a runner reports to the token GitHub's API returns.

    GitHub reports a runner's `os` as linux, macos or windows. The upstream
    Actions runner registers with .NET's RuntimeInformation.OSDescription,
    which on Linux is the full uname string — kernel version, distribution
    build and build date. Stored verbatim, that put the *host's* kernel into
    an API response and onto the admin page, since containers share it.

    The description itself is not kept: nothing reads it, and it describes the
    machine underneath the cluster rather than the runner.
    """
    text = (value or "").strip()
    if not text:
        return "linux"
    lowered = text.lower()
    if "windows" in lowered or "microsoft" in lowered:
        return "windows"
    if "darwin" in lowered or "mac os" in lowered or "macos" in lowered:
        return "macos"
    if "linux" in lowered:
        return "linux"
    # Something unrecognised: keep the first token rather than guessing a
    # platform, so an unexpected runner is visible instead of mislabelled.
    return lowered.split()[0]


def _effective_status(runner: Runner) -> str:
    """Report a runner that has stopped heartbeating as offline.

    The stored `status` is only ever written back to "offline" by
    `_requeue_stale_jobs`, which reaches a runner solely when it is holding an
    in-progress job. A runner that goes away while idle keeps whatever it was
    last set to, so a stack that has restarted its runners a few times serves a
    list of runners all claiming to be online, some with heartbeats weeks old.
    That is not what GitHub does: a runner that stops polling goes offline.

    Derived on read rather than reconciled on write, so no background sweep is
    needed and a runner cannot be left misreporting because nothing happened to
    touch it. The threshold is the one `_requeue_stale_jobs` already uses, so
    the two agree by construction rather than by coincidence.
    """
    if runner.status == "offline" or runner.last_heartbeat is None:
        return "offline"
    heartbeat = runner.last_heartbeat
    if heartbeat.tzinfo is None:
        # SQLite hands back naive datetimes; they are stored as UTC.
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.RUNNER_STALE_THRESHOLD_SECONDS
    )
    return "offline" if heartbeat < cutoff else runner.status


def _runner_payload(runner: Runner) -> dict:
    status = _effective_status(runner)
    return {
        "id": runner.id,
        "name": runner.name,
        "os": runner.os,
        "status": status,
        # A runner that is not online cannot be busy. Leaving busy=True on a
        # dead runner is how a phantom appears to be occupied forever.
        "busy": runner.busy and status == "online",
        "labels": [
            {"id": index, "name": label, "type": "custom"}
            for index, label in enumerate(runner.labels or [])
        ],
    }


@router.post("/enterprises/{enterprise}/actions/runners/registration-token")
async def create_enterprise_registration_token(
    enterprise: str, db: DbSession, user: AuthUser,
):
    """Create a registration token for an enterprise-scoped runner."""
    _require_enterprise(enterprise, user)
    token = f"AAAAAA{secrets.token_urlsafe(27)}"
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    db.add(EnterpriseRunnerRegistrationToken(
        token=token,
        enterprise_slug=enterprise,
        expires_at=expires,
    ))
    await db.commit()
    return {"token": token, "expires_at": _fmt_dt(expires)}


@router.post("/enterprises/{enterprise}/actions/runners/remove-token")
async def create_enterprise_remove_token(
    enterprise: str, db: DbSession, user: AuthUser,
):
    """Create the short-lived token consumed by config.sh remove."""
    _require_enterprise(enterprise, user)
    token = f"AAAAAA{secrets.token_urlsafe(27)}"
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    db.add(EnterpriseRunnerRegistrationToken(
        token=token,
        enterprise_slug=enterprise,
        expires_at=expires,
    ))
    await db.commit()
    return {"token": token, "expires_at": _fmt_dt(expires)}


@router.get("/enterprises/{enterprise}/actions/runners")
async def list_enterprise_runners(
    enterprise: str, db: DbSession, user: AuthUser,
):
    _require_enterprise(enterprise, user)
    result = await db.execute(
        select(Runner).where(Runner.enterprise_slug == enterprise)
    )
    runners = result.scalars().all()
    return {
        "total_count": len(runners),
        "runners": [_runner_payload(runner) for runner in runners],
    }


@router.get("/enterprises/{enterprise}/actions/runners/{runner_id}")
async def get_enterprise_runner(
    enterprise: str, runner_id: int, db: DbSession, user: AuthUser,
):
    _require_enterprise(enterprise, user)
    result = await db.execute(select(Runner).where(
        Runner.id == runner_id,
        Runner.enterprise_slug == enterprise,
    ))
    runner = result.scalar_one_or_none()
    if runner is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _runner_payload(runner)


async def _delete_runner_and_detach_history(db, runner: Runner) -> None:
    """Delete a runner, detaching the jobs that record having used it.

    `workflow_jobs.runner_id` is a foreign key to `runners.id`, and SQLite does
    not enforce it, so deleting a runner that any job referenced silently left
    rows pointing at nothing. The practical effect was that the dead
    registrations a restarted stack accumulates could not be removed safely:
    the only ones it was safe to delete were those no job had ever used.

    Jobs keep `runner_name`, which is denormalised for exactly this reason, so
    detaching loses nothing a reader of the history needs — it still says which
    runner ran the job. What goes is a pointer to a row that is being removed.

    A runner still executing something is refused rather than detached. Nulling
    `runner_id` on an in-progress job would strand it: `_requeue_stale_jobs`
    finds work to requeue by joining jobs to their runner, so a detached live
    job would never be recovered by anything.
    """
    live = (await db.execute(
        select(WorkflowJob).where(
            WorkflowJob.runner_id == runner.id,
            WorkflowJob.status == "in_progress",
        )
    )).scalars().first()
    if live is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"runner {runner.name!r} is running job {live.id}; "
                "wait for it to finish or let it go stale first"
            ),
        )

    referencing = (await db.execute(
        select(WorkflowJob).where(WorkflowJob.runner_id == runner.id)
    )).scalars().all()
    for job in referencing:
        job.runner_id = None

    # runner_sessions is the other table with a foreign key to runners, and its
    # runner_id is NOT NULL — so it cannot be detached the way jobs can. The
    # first version of this function handled only workflow_jobs and returned a
    # 500 for any runner that had ever opened a session: SQLAlchemy tried to
    # null a non-nullable column on cascade. A session is live connection
    # state, not history, so removing it alongside the runner is correct rather
    # than a compromise.
    sessions = (await db.execute(
        select(RunnerSession).where(RunnerSession.runner_id == runner.id)
    )).scalars().all()
    for session in sessions:
        await db.delete(session)

    await db.delete(runner)
    await db.commit()


@router.delete("/enterprises/{enterprise}/actions/runners/{runner_id}", status_code=204)
async def delete_enterprise_runner(
    enterprise: str, runner_id: int, db: DbSession, user: AuthUser,
):
    _require_enterprise(enterprise, user)
    result = await db.execute(select(Runner).where(
        Runner.id == runner_id,
        Runner.enterprise_slug == enterprise,
    ))
    runner = result.scalar_one_or_none()
    if runner is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await _delete_runner_and_detach_history(db, runner)


@router.post("/repos/{owner}/{repo}/actions/runners/registration-token")
async def create_registration_token(
    owner: str, repo: str, db: DbSession, user: AuthUser,
):
    """Create a registration token for a self-hosted runner."""
    repository = await get_repo_or_404(owner, repo, db)

    token = f"AAAAAA{secrets.token_urlsafe(27)}"
    expires = datetime.now(timezone.utc) + timedelta(hours=1)

    reg = RegistrationToken(
        token=token,
        repo_id=repository.id,
        expires_at=expires,
    )
    db.add(reg)
    await db.commit()

    return {"token": token, "expires_at": _fmt_dt(expires)}


@router.post("/repos/{owner}/{repo}/actions/runners/remove-token")
async def create_remove_token(
    owner: str, repo: str, db: DbSession, user: AuthUser,
):
    """Create a remove token for a self-hosted runner."""
    token = f"AAAAAA{secrets.token_urlsafe(27)}"
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    return {"token": token, "expires_at": _fmt_dt(expires)}


@router.get(
    "/repos/{owner}/{repo}/actions/runners", response_model=RunnerListResponse
)
async def list_runners(
    owner: str, repo: str, db: DbSession, user: AuthUser,
):
    """List self-hosted runners for a repository."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Runner).where(Runner.repo_id == repository.id)
    )
    runners = result.scalars().all()
    api = f"{BASE}/api/v3"
    items = [_runner_payload(r) for r in runners]
    return {"total_count": len(items), "runners": items}


@router.get("/repos/{owner}/{repo}/actions/runners/downloads")
async def list_runner_downloads(
    owner: str, repo: str, db: DbSession, user: AuthUser,
):
    """List runner application downloads."""
    return [
        {
            "os": "linux",
            "architecture": "x64",
            "download_url": "https://github.com/actions/runner/releases/latest",
            "filename": "actions-runner-linux-x64.tar.gz",
        },
    ]


@router.get(
    "/repos/{owner}/{repo}/actions/runners/{runner_id}",
    response_model=RunnerResponse,
)
async def get_runner(
    owner: str, repo: str, runner_id: int, db: DbSession, user: AuthUser,
):
    """Get a self-hosted runner."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Runner).where(Runner.id == runner_id, Runner.repo_id == repository.id)
    )
    r = result.scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _runner_payload(r)


@router.delete("/repos/{owner}/{repo}/actions/runners/{runner_id}", status_code=204)
async def delete_runner(
    owner: str, repo: str, runner_id: int, db: DbSession, user: AuthUser,
):
    """Delete a self-hosted runner."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Runner).where(Runner.id == runner_id, Runner.repo_id == repository.id)
    )
    r = result.scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await _delete_runner_and_detach_history(db, r)

"""Workflow job promotion and run lifecycle scheduling."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.actions import WorkflowJob, WorkflowRun

async def dispatch_ready_jobs(db: AsyncSession, run_id: int) -> list[WorkflowJob]:
    """Find jobs whose dependencies are met and set them to queued."""
    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run_id)
    )
    all_jobs = result.scalars().all()

    completed_keys = set()
    unsuccessful_keys = set()
    job_by_name = {}
    for job in all_jobs:
        job_by_name[job.name] = job
        if job.status == "completed" and job.conclusion == "success":
            completed_keys.add(job.name)
        elif job.status == "completed" and job.conclusion in ("failure", "cancelled", "skipped"):
            unsuccessful_keys.add(job.name)

    promoted = []
    for job in all_jobs:
        if job.status != "waiting":
            continue
        needs = job.needs or []
        if any(n in unsuccessful_keys for n in needs):
            job.status = "completed"
            job.conclusion = "skipped"
            job.completed_at = datetime.now(timezone.utc)
            job.steps = [
                {**step, "status": "completed", "conclusion": "skipped"}
                for step in (job.steps or [])
            ]
        elif all(n in completed_keys for n in needs):
            job.status = "queued"
            promoted.append(job)

    if promoted:
        await db.flush()

    return promoted


async def check_run_completion(db: AsyncSession, run_id: int) -> WorkflowRun | None:
    """Check if all jobs in a run are done; if so, finalize the run."""
    run_result = await db.execute(
        select(WorkflowRun).where(WorkflowRun.id == run_id)
    )
    run = run_result.scalar_one_or_none()
    if not run:
        return None

    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run_id)
    )
    jobs = result.scalars().all()

    if not jobs:
        return run

    if any(j.status in ("queued", "in_progress", "waiting") for j in jobs):
        failed_keys = {j.name for j in jobs if j.status == "completed" and j.conclusion == "failure"}
        if failed_keys:
            for j in jobs:
                if j.status == "waiting" and j.needs:
                    if any(n in failed_keys for n in j.needs):
                        j.status = "completed"
                        j.conclusion = "skipped"
                        j.completed_at = datetime.now(timezone.utc)

            still_active = any(
                j.status in ("queued", "in_progress", "waiting")
                for j in jobs
                if not (j.status == "waiting" and j.needs and any(n in failed_keys for n in j.needs))
            )
            if still_active:
                run.status = "in_progress"
                await db.flush()
                return run

        else:
            run.status = "in_progress"
            await db.flush()
            return run

    conclusions = [j.conclusion for j in jobs if j.conclusion]
    if "failure" in conclusions:
        run.conclusion = "failure"
    elif "cancelled" in conclusions:
        run.conclusion = "cancelled"
    elif all(c in ("success", "skipped") for c in conclusions):
        run.conclusion = "success"
    else:
        run.conclusion = "failure"

    run.status = "completed"
    await db.flush()
    return run


async def cancel_workflow_run(db: AsyncSession, run_id: int) -> WorkflowRun | None:
    """Cancel a workflow run and its pending jobs."""
    run_result = await db.execute(
        select(WorkflowRun).where(WorkflowRun.id == run_id)
    )
    run = run_result.scalar_one_or_none()
    if not run:
        return None

    if run.status == "completed" and run.conclusion != "cancelled":
        return run

    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run_id)
    )
    for job in result.scalars().all():
        if job.status != "completed":
            job.status = "completed"
            job.conclusion = "cancelled"
            job.completed_at = now

    run.status = "completed"
    run.conclusion = "cancelled"
    await db.flush()
    return run

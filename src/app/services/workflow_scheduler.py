"""Workflow job promotion and run lifecycle scheduling."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.actions import WorkflowJob, WorkflowRun

async def dispatch_ready_jobs(db: AsyncSession, run_id: int) -> list[WorkflowJob]:
    """Find jobs whose dependencies are met and set them to queued.

    A promoted job's condition and steps are rendered here rather than at run
    creation when it declares dependencies, because they may read
    ``needs.<job>.outputs.*``, which only exist once those jobs have finished.
    """
    from app.services.workflow_service import (
        build_needs_context,
        build_run_expression_context,
        build_steps_data,
    )
    from app.services.workflow_expressions import evaluate_job_if, render_expressions

    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run_id)
    )
    all_jobs = result.scalars().all()

    completed_keys = set()
    unsuccessful_keys = set()
    for job in all_jobs:
        # ``needs:`` entries reference YAML job keys, not display names.
        key = job.job_key or job.name
        if job.status == "completed" and job.conclusion == "success":
            completed_keys.add(key)
        elif job.status == "completed" and job.conclusion in ("failure", "cancelled", "skipped"):
            unsuccessful_keys.add(key)

    promoted = []
    ready = []
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
            ready.append(job)

    if ready:
        base_context = None
        needs_context = None
        for job in ready:
            pending = job.pending_render
            if pending:
                if base_context is None:
                    run = (await db.execute(
                        select(WorkflowRun).where(WorkflowRun.id == run_id)
                    )).scalar_one_or_none()
                    base_context = await build_run_expression_context(db, run) if run else {}
                    needs_context = build_needs_context(
                        [j for j in all_jobs if j.status == "completed"]
                    )
                github_ctx = base_context.get("github") or {}
                context = {
                    **base_context,
                    "needs": needs_context,
                    "matrix": pending.get("matrix") or {},
                    "job": {
                        "workflow_repository": pending.get("_workflow_repository")
                        or github_ctx.get("repository", ""),
                        "workflow_sha": pending.get("_workflow_sha")
                        or github_ctx.get("sha", ""),
                    },
                }
                call_inputs = pending.get("_call_inputs") or {}
                call_secrets = pending.get("_call_secrets") or {}
                if call_inputs:
                    context["inputs"] = {
                        **(base_context.get("inputs") or {}),
                        **render_expressions(call_inputs, context),
                    }
                if call_secrets:
                    context["secrets"] = {
                        **(base_context.get("secrets") or {}),
                        **render_expressions(call_secrets, context),
                    }
                if not evaluate_job_if(pending.get("if"), context):
                    job.status = "completed"
                    job.conclusion = "skipped"
                    job.completed_at = datetime.now(timezone.utc)
                    job.steps = [
                        {**step, "status": "completed", "conclusion": "skipped"}
                        for step in (job.steps or [])
                    ]
                    job.pending_render = None
                    continue
                job.steps = build_steps_data(
                    pending.get("steps") or [],
                    pending.get("env") or {},
                    context,
                )
                job.pending_render = None
            job.status = "queued"
            promoted.append(job)

    if promoted or ready:
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

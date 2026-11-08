"""Workflow job promotion and run lifecycle scheduling."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.actions import WorkflowJob, WorkflowRun

def _runs_after_unsuccessful_needs(job: WorkflowJob) -> bool:
    """Whether a job is still evaluated when a job it needs failed, was
    skipped or was cancelled: on GitHub, when its condition names a status
    function. Without one the condition is implicitly ``success() && ...``
    and the job is skipped."""
    from app.services.workflow_expressions import uses_status_function

    pending = job.pending_render or {}
    return uses_status_function(pending.get("if"))


def _ancestors(job: WorkflowJob, all_jobs) -> list[str]:
    """Every job ``job`` depends on, directly or through others: the jobs
    ``success()`` and ``failure()`` answer for, as on GitHub, so a cleanup
    job behind a skipped one still sees the failure that skipped it."""
    needs_by_key = {(j.job_key or j.name): list(j.needs or []) for j in all_jobs}
    seen: list[str] = []
    pending = list(job.needs or [])
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.append(key)
        pending.extend(needs_by_key.get(key, []))
    return seen


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
        render_job_labels,
        strategy_context,
    )
    from app.services.workflow_expressions import (
        JOB_NEEDS_KEY,
        RUN_CANCELLED_KEY,
        evaluate_job_if,
        render_expressions,
    )

    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run_id)
    )
    all_jobs = result.scalars().all()

    # ``needs:`` entries reference YAML job keys, not display names. The
    # instances of a matrix job share its key, and a job that needs it waits
    # for all of them: it is finished only when every instance is, and
    # successful only when every instance succeeded.
    rows_by_key: dict[str, list[WorkflowJob]] = {}
    for job in all_jobs:
        rows_by_key.setdefault(job.job_key or job.name, []).append(job)
    completed_keys = set()
    unsuccessful_keys = set()
    for key, rows in rows_by_key.items():
        if not all(row.status == "completed" for row in rows):
            continue
        if all(row.conclusion == "success" for row in rows):
            completed_keys.add(key)
        else:
            unsuccessful_keys.add(key)

    promoted = []
    ready = []
    # Repeat until nothing more is skipped: a job skipped here is itself an
    # unsuccessful need, so the skip cascades down a chain in one pass, and a
    # job further down that names a status function becomes ready now rather
    # than waiting for a dispatch that would never come.
    changed = True
    while changed:
        changed = False
        for job in all_jobs:
            if job.status != "waiting" or job in ready:
                continue
            needs = job.needs or []
            if any(n in unsuccessful_keys for n in needs) and not _runs_after_unsuccessful_needs(job):
                job.status = "completed"
                job.conclusion = "skipped"
                job.completed_at = datetime.now(timezone.utc)
                job.steps = [
                    {**step, "status": "completed", "conclusion": "skipped"}
                    for step in (job.steps or [])
                ]
                unsuccessful_keys.add(job.job_key or job.name)
                changed = True
            elif all(n in completed_keys or n in unsuccessful_keys for n in needs):
                # Every need has finished. Either all succeeded, or the job's
                # condition names a status function and decides for itself.
                ready.append(job)

    expansion_failed = False
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
                    JOB_NEEDS_KEY: _ancestors(job, all_jobs),
                    RUN_CANCELLED_KEY: bool(base_context.get(RUN_CANCELLED_KEY)),
                    "matrix": pending.get("matrix") or {},
                    "strategy": pending.get("strategy") or strategy_context(None),
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
                instances = [(job, context)]
                if pending.get("dynamic_matrix") is not None:
                    instances = await _expand_dynamic_matrix(db, job, pending, context)
                    if not instances:
                        expansion_failed = True
                        continue
                for instance, instance_context in instances:
                    instance.labels = render_job_labels(instance.labels, instance_context)
                    instance.steps = build_steps_data(
                        pending.get("steps") or [],
                        pending.get("env") or {},
                        instance_context,
                    )
                    instance.pending_render = None
                    if instance is not job:
                        ready.append(instance)
                job.pending_render = None
            # Concurrency is applied here, where the job becomes eligible to
            # run, not when it was created: a job still waiting on `needs:`
            # has an unevaluated `if:` and may be about to skip. Only a job
            # that is really going to run may supersede another.
            if job.concurrency_group:
                await _supersede_for_concurrency(db, job)
            job.status = "queued"
            promoted.append(job)

    if promoted or ready:
        await db.flush()

    if expansion_failed:
        # A job that failed to expand is a finished, unsuccessful need: the
        # jobs behind it (a cleanup with always(), or ones to skip) are
        # decided now, since no job will complete to trigger another pass.
        promoted.extend(await dispatch_ready_jobs(db, run_id))

    return promoted


async def _expand_dynamic_matrix(
    db: AsyncSession, job: WorkflowJob, pending: dict, context: dict,
) -> list[tuple[WorkflowJob, dict]]:
    """Expand a matrix written as an expression into its job instances.

    ``matrix: ${{ fromJSON(needs.x.outputs.matrix) }}`` can be evaluated only
    once ``x`` has finished, so the job waits as one row. Here the first
    instance takes that row and each further one gets a row of its own. Each
    instance's name and concurrency group are rendered with its own matrix
    values: rendered without them, every Fullsend harness job shared one
    group, ``fullsend-harness--<repo>--``, and superseded the others.

    A matrix that is not a mapping or has no instances fails the job, as on
    GitHub ("Matrix vector 'include' does not contain any values"). Returns
    the instances with their expression contexts, or [] when the job failed.
    """
    from app.services.workflow_expressions import evaluate_expression_value
    from app.services.workflow_service import (
        _job_concurrency,
        matrix_combinations,
        matrix_job_name,
        strategy_context,
    )
    from app.services.workflow_expressions import render_expressions

    combos = matrix_combinations(
        evaluate_expression_value(pending["dynamic_matrix"], context)
    )
    if not combos:
        job.status = "completed"
        job.conclusion = "failure"
        job.completed_at = datetime.now(timezone.utc)
        job.steps = [
            {**step, "status": "completed", "conclusion": "skipped"}
            for step in (job.steps or [])
        ]
        job.pending_render = None
        return []

    instances = []
    for index, combo in enumerate(combos):
        row = job if index == 0 else WorkflowJob(
            run_id=job.run_id,
            workflow_name=job.workflow_name,
            workflow_ref=job.workflow_ref,
            status="waiting",
            labels=list(job.labels or []),
            run_attempt=job.run_attempt,
            needs=list(job.needs or []),
            permissions=dict(job.permissions or {}),
            job_key=job.job_key,
            outputs_config=dict(job.outputs_config or {}),
            outputs={},
            name=job.name,
        )
        instance_context = {
            **context,
            "matrix": combo,
            "strategy": strategy_context(
                pending.get("strategy_options"), index, len(combos), context,
            ),
        }
        row.name = str(render_expressions(
            matrix_job_name(pending.get("name") or job.name, combo), instance_context,
        ))
        group, cancel = _job_concurrency(
            {"concurrency": pending.get("concurrency")}, instance_context,
        )
        row.concurrency_group = group if cancel else None
        if row is not job:
            db.add(row)
        instances.append((row, instance_context))
    await db.flush()
    return instances


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
        failed_keys = {
            j.job_key or j.name for j in jobs
            if j.status == "completed" and j.conclusion == "failure"
        }
        if failed_keys:
            def stranded(j) -> bool:
                return (
                    j.status == "waiting" and bool(j.needs)
                    and any(n in failed_keys for n in j.needs)
                    and not _runs_after_unsuccessful_needs(j)
                )

            for j in jobs:
                if stranded(j):
                    j.status = "completed"
                    j.conclusion = "skipped"
                    j.completed_at = datetime.now(timezone.utc)

            still_active = any(
                j.status in ("queued", "in_progress", "waiting")
                for j in jobs
                if not stranded(j)
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


async def _supersede_for_concurrency(db: AsyncSession, job: WorkflowJob) -> None:
    """Cancel earlier jobs sharing this job's concurrency group.

    GitHub's `cancel-in-progress: true` means a newly eligible job supersedes
    an in-flight one in the same group. The group is rendered from the event,
    so for Fullsend's stages it is per repository and per issue: a new comment
    on an issue supersedes a triage still running on that issue, and leaves
    every other issue alone.

    Only jobs from *other* runs are candidates. Superseding within the same
    run would mean a matrix sharing a group cancelled its own siblings.

    A group is recorded on the job only when `cancel-in-progress` is true, so
    reaching here means superseding is wanted. GitHub's other half — a group
    with `cancel-in-progress: false`, which queues a job behind its
    predecessor rather than cancelling it — is not implemented: this emulator
    has no queue-behind state, and pretending otherwise would run both
    concurrently while reporting that they were serialised.
    """
    superseded = (await db.execute(
        select(WorkflowJob).where(
            WorkflowJob.concurrency_group == job.concurrency_group,
            WorkflowJob.run_id != job.run_id,
            WorkflowJob.id != job.id,
            WorkflowJob.status.in_(("queued", "in_progress")),
        )
    )).scalars().all()
    now = datetime.now(timezone.utc)
    for previous in superseded:
        previous.status = "completed"
        previous.conclusion = "cancelled"
        previous.completed_at = now

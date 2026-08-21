"""Workflow detection, trigger evaluation, and run lifecycle management."""

import asyncio
import fnmatch
import itertools
import logging
import re
from datetime import datetime, timezone

import yaml
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.actions import Workflow, WorkflowRun, WorkflowJob, Secret, Variable
from app.models.repository import Repository
from app.models.user import User

logger = logging.getLogger("github_emulator.workflows")

_EXPRESSION_RE = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")


def _lookup_context(context: dict, expression: str) -> str:
    value: object = context
    for part in expression.strip().split("."):
        if isinstance(value, dict):
            value = value.get(part, "")
        else:
            return ""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def render_expressions(value: object, context: dict) -> object:
    """Render the small expression subset needed by the M2 runner contract."""
    if isinstance(value, str):
        def replace(match):
            expression = match.group(1).strip()
            # Step outputs only exist after a prior step has run. Preserve the
            # expression for the runner's runtime renderer.
            if expression.startswith("steps."):
                return match.group(0)
            return _lookup_context(context, expression)
        return _EXPRESSION_RE.sub(replace, value)
    if isinstance(value, dict):
        return {key: render_expressions(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render_expressions(item, context) for item in value]
    return value


async def detect_workflows(repo_disk_path: str) -> list[dict]:
    """Read .github/workflows/*.yml from a bare repo's HEAD and return parsed YAML dicts."""
    proc = await asyncio.create_subprocess_exec(
        "git", "ls-tree", "--name-only", "HEAD", ".github/workflows/",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"GIT_DIR": repo_disk_path},
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return []

    workflows = []
    for line in stdout.decode().strip().splitlines():
        path = line.strip()
        if not path.endswith((".yml", ".yaml")):
            continue

        cat_proc = await asyncio.create_subprocess_exec(
            "git", "show", f"HEAD:{path}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"GIT_DIR": repo_disk_path},
        )
        cat_out, _ = await cat_proc.communicate()
        if cat_proc.returncode != 0:
            continue

        try:
            parsed = yaml.safe_load(cat_out.decode())
            if isinstance(parsed, dict):
                parsed["_path"] = path
                workflows.append(parsed)
        except yaml.YAMLError:
            logger.warning("Failed to parse workflow %s", path)

    return workflows


def evaluate_trigger(workflow_yaml: dict, event: str, payload: dict) -> bool:
    """Check if a workflow's `on:` configuration matches the given event and payload."""
    on_config = workflow_yaml.get("on") or workflow_yaml.get(True)
    if on_config is None:
        return False

    if isinstance(on_config, str):
        return on_config == event

    if isinstance(on_config, list):
        return event in on_config

    if isinstance(on_config, dict):
        if event not in on_config:
            return False

        event_config = on_config[event]
        if event_config is None:
            return True

        if event == "push":
            return _match_push(event_config, payload)
        if event == "pull_request":
            return _match_pull_request(event_config, payload)
        if event == "workflow_dispatch":
            return True

        return True

    return False


def _match_push(config: dict, payload: dict) -> bool:
    ref = payload.get("ref", "")
    branch = ref.removeprefix("refs/heads/")
    tag = ref.removeprefix("refs/tags/")

    if "branches" in config:
        patterns = config["branches"]
        if not any(fnmatch.fnmatch(branch, p) for p in patterns):
            return False

    if "branches-ignore" in config:
        patterns = config["branches-ignore"]
        if any(fnmatch.fnmatch(branch, p) for p in patterns):
            return False

    if "tags" in config:
        patterns = config["tags"]
        if not ref.startswith("refs/tags/"):
            return False
        if not any(fnmatch.fnmatch(tag, p) for p in patterns):
            return False

    if "tags-ignore" in config:
        patterns = config["tags-ignore"]
        if ref.startswith("refs/tags/") and any(fnmatch.fnmatch(tag, p) for p in patterns):
            return False

    if "paths" in config:
        changed = _get_changed_files(payload)
        if not any(fnmatch.fnmatch(f, p) for f in changed for p in config["paths"]):
            return False

    if "paths-ignore" in config:
        changed = _get_changed_files(payload)
        if all(
            any(fnmatch.fnmatch(f, p) for p in config["paths-ignore"])
            for f in changed
        ):
            return False

    return True


def _match_pull_request(config: dict, payload: dict) -> bool:
    if "types" in config:
        action = payload.get("action", "opened")
        if action not in config["types"]:
            return False

    if "branches" in config:
        base_branch = payload.get("pull_request", {}).get("base", {}).get("ref", "")
        if not any(fnmatch.fnmatch(base_branch, p) for p in config["branches"]):
            return False

    if "branches-ignore" in config:
        base_branch = payload.get("pull_request", {}).get("base", {}).get("ref", "")
        if any(fnmatch.fnmatch(base_branch, p) for p in config["branches-ignore"]):
            return False

    return True


def _get_changed_files(payload: dict) -> list[str]:
    files = []
    for commit in payload.get("commits", []):
        files.extend(commit.get("added", []))
        files.extend(commit.get("modified", []))
        files.extend(commit.get("removed", []))
    return files


def expand_matrix(job_config: dict) -> list[dict]:
    """Expand strategy.matrix into individual job configurations."""
    strategy = job_config.get("strategy", {})
    matrix = dict(strategy.get("matrix", {}) or {})
    if not matrix:
        return [job_config]

    include = matrix.pop("include", [])
    exclude = matrix.pop("exclude", [])

    keys = list(matrix.keys())
    values = [matrix[k] if isinstance(matrix[k], list) else [matrix[k]] for k in keys]

    combos = []
    for combo in itertools.product(*values):
        entry = dict(zip(keys, combo))

        excluded = False
        for exc in exclude:
            if all(entry.get(k) == v for k, v in exc.items()):
                excluded = True
                break
        if not excluded:
            combos.append(entry)

    for inc in include:
        combos.append(inc)

    if not combos:
        return [job_config]

    expanded = []
    for combo in combos:
        job_copy = {k: v for k, v in job_config.items() if k != "strategy"}
        job_copy["_matrix"] = combo
        name_suffix = ", ".join(f"{v}" for v in combo.values())
        job_copy["_display_name"] = f"{job_config.get('name', job_config.get('_key', ''))} ({name_suffix})"
        expanded.append(job_copy)

    return expanded


def build_job_graph(workflow_yaml: dict) -> list[dict]:
    """Parse jobs section into a dependency-ordered list."""
    jobs_config = workflow_yaml.get("jobs", {})
    if not jobs_config:
        return []

    jobs = []
    for key, config in jobs_config.items():
        if not isinstance(config, dict):
            continue
        runs_on = config.get("runs-on", "ubuntu-latest")
        if isinstance(runs_on, str):
            labels = [runs_on]
        else:
            labels = list(runs_on)

        needs = config.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]

        steps = config.get("steps", [])

        jobs.append({
            "key": key,
            "name": config.get("name", key),
            "runs_on": labels,
            "needs": needs,
            "steps": steps,
            "env": config.get("env", {}),
            "strategy": config.get("strategy", {}),
            "permissions": config.get("permissions", {}),
            "if": config.get("if"),
            "timeout_minutes": config.get("timeout-minutes", 360),
        })

    return _topo_sort(jobs)


def _topo_sort(jobs: list[dict]) -> list[dict]:
    """Topological sort by 'needs' dependencies."""
    by_key = {j["key"]: j for j in jobs}
    visited = set()
    result = []

    def visit(key):
        if key in visited:
            return
        visited.add(key)
        job = by_key.get(key)
        if job:
            for dep in job["needs"]:
                visit(dep)
            result.append(job)

    for j in jobs:
        visit(j["key"])

    return result


async def sync_workflows_to_db(
    db: AsyncSession, repository: Repository
) -> list[Workflow]:
    """Upsert Workflow rows from on-disk workflow files."""
    detected = await detect_workflows(repository.disk_path)

    result = await db.execute(
        select(Workflow).where(Workflow.repo_id == repository.id)
    )
    existing = {w.path: w for w in result.scalars().all()}

    workflows = []
    seen_paths = set()

    for wf_yaml in detected:
        path = wf_yaml.get("_path", "")
        name = wf_yaml.get("name", path)
        seen_paths.add(path)

        if path in existing:
            w = existing[path]
            w.name = name
            w.state = "active"
        else:
            w = Workflow(repo_id=repository.id, name=name, path=path)
            db.add(w)

        workflows.append((w, wf_yaml))

    for path, w in existing.items():
        if path not in seen_paths:
            w.state = "disabled_manually"

    await db.flush()
    return workflows


async def create_workflow_run(
    db: AsyncSession,
    workflow: Workflow,
    workflow_yaml: dict,
    event: str,
    payload: dict,
    actor: User,
    head_sha: str,
    head_branch: str,
) -> WorkflowRun:
    """Create a WorkflowRun and its child WorkflowJob records."""
    count = (await db.execute(
        select(func.count(WorkflowRun.id)).where(
            WorkflowRun.workflow_id == workflow.id
        )
    )).scalar() or 0

    run = WorkflowRun(
        workflow_id=workflow.id,
        repo_id=workflow.repo_id,
        head_sha=head_sha,
        head_branch=head_branch,
        event=event,
        status="queued",
        run_number=count + 1,
        run_attempt=1,
        actor_id=actor.id,
        trigger_payload=payload,
    )
    db.add(run)
    await db.flush()

    variables = {
        item.name: item.value
        for item in (await db.execute(
            select(Variable).where(Variable.repo_id == workflow.repo_id)
        )).scalars().all()
    }
    secrets = {
        item.name: item.value or ""
        for item in (await db.execute(
            select(Secret).where(Secret.repo_id == workflow.repo_id)
        )).scalars().all()
    }
    expression_context = {
        "inputs": payload.get("inputs", {}),
        "vars": variables,
        "secrets": secrets,
        "github": {
            "event_name": event,
            "ref": payload.get("ref", f"refs/heads/{head_branch}"),
            "repository": payload.get("repository", {}).get("full_name", ""),
            "repository_owner": payload.get("repository", {}).get("full_name", "").split("/", 1)[0],
            "run_id": run.id,
            "run_number": run.run_number,
            "sha": head_sha,
            "server_url": settings.BASE_URL,
        },
    }

    concurrency = workflow_yaml.get("concurrency")
    cancel_in_progress = True
    if isinstance(concurrency, dict):
        cancel_in_progress = concurrency.get("cancel-in-progress", True) is not False
        concurrency = concurrency.get("group")
    if concurrency:
        group = str(render_expressions(concurrency, expression_context))
        run.concurrency_group = group
        if cancel_in_progress:
            active = (await db.execute(select(WorkflowRun).where(
                WorkflowRun.workflow_id == workflow.id,
                WorkflowRun.concurrency_group == group,
                WorkflowRun.id != run.id,
                WorkflowRun.status != "completed",
            ))).scalars().all()
            for previous in active:
                previous.status = "completed"
                previous.conclusion = "cancelled"
                previous_jobs = (await db.execute(select(WorkflowJob).where(WorkflowJob.run_id == previous.id))).scalars().all()
                for previous_job in previous_jobs:
                    if previous_job.status in ("queued", "waiting", "in_progress"):
                        previous_job.status = "completed"
                        previous_job.conclusion = "cancelled"
                        previous_job.completed_at = datetime.now(timezone.utc)

    job_list = build_job_graph(workflow_yaml)

    for job_def in job_list:
        expanded = expand_matrix(job_def)
        for job_config in expanded:
            job_expression_context = {**expression_context, "matrix": job_config.get("_matrix", {})}
            display_name = render_expressions(job_config.get("_display_name", job_config.get("name", job_config["key"])), job_expression_context)
            needs = job_config.get("needs", [])
            initial_status = "queued" if not needs else "waiting"

            steps_data = []
            for i, step in enumerate(job_config.get("steps", [])):
                step_env = {
                    **(job_config.get("env") or {}),
                    **(step.get("env") or {}),
                }
                step_data = {
                    "number": i + 1,
                    "name": step.get("name", f"Step {i + 1}"),
                    "status": "queued",
                    "conclusion": None,
                }
                for key in ("run", "shell", "working-directory", "uses", "with"):
                    if key in step:
                        step_data[key] = render_expressions(step[key], job_expression_context)
                if "id" in step:
                    step_data["id"] = step["id"]
                if step_env:
                    step_data["env"] = render_expressions(step_env, job_expression_context)
                steps_data.append(step_data)

            job = WorkflowJob(
                run_id=run.id,
                name=display_name,
                workflow_name=workflow.name,
                status=initial_status,
                steps=steps_data,
                labels=job_config.get("runs_on", ["ubuntu-latest"]),
                run_attempt=1,
                needs=needs,
                permissions=job_config.get("permissions") or {},
            )
            db.add(job)

    await db.flush()
    return run


async def process_push_event(
    db: AsyncSession, repository: Repository, user: User
) -> list[WorkflowRun]:
    """Detect workflows triggered by a push and create runs for them."""
    if not repository.disk_path:
        return []

    workflows = await sync_workflows_to_db(db, repository)

    head_sha = await _get_head_sha(repository.disk_path)
    head_branch = repository.default_branch or "main"

    payload = {
        "ref": f"refs/heads/{head_branch}",
        "after": head_sha,
        "repository": {"id": repository.id, "full_name": repository.full_name},
        "pusher": {"name": user.login, "email": user.email or ""},
        "sender": {"login": user.login, "id": user.id},
    }

    runs = []
    for workflow_obj, workflow_yaml in workflows:
        if evaluate_trigger(workflow_yaml, "push", payload):
            run = await create_workflow_run(
                db, workflow_obj, workflow_yaml, "push", payload,
                user, head_sha, head_branch,
            )
            runs.append(run)
            logger.info(
                "Created workflow run #%d for %s on %s",
                run.run_number, workflow_obj.name, repository.full_name,
            )

    if runs:
        await db.commit()

        try:
            from app.services.webhook_service import trigger_webhooks
            for run in runs:
                await trigger_webhooks(db, repository.id, "workflow_run", {
                    "action": "requested",
                    "workflow_run": {
                        "id": run.id,
                        "name": run.workflow.name if run.workflow else "",
                        "head_sha": run.head_sha,
                        "head_branch": run.head_branch,
                        "status": run.status,
                        "event": run.event,
                    },
                    "repository": {"id": repository.id, "full_name": repository.full_name},
                })
        except Exception:
            pass

    return runs


async def _get_head_sha(repo_disk_path: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"GIT_DIR": repo_disk_path},
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip() if proc.returncode == 0 else ""


async def get_ref_sha(repo_disk_path: str, ref: str) -> str:
    """Resolve a branch/ref in a bare repository to a commit SHA."""
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", f"{ref}^{{commit}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"GIT_DIR": repo_disk_path},
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip() if proc.returncode == 0 else ""


async def dispatch_ready_jobs(db: AsyncSession, run_id: int) -> list[WorkflowJob]:
    """Find jobs whose dependencies are met and set them to queued."""
    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run_id)
    )
    all_jobs = result.scalars().all()

    completed_keys = set()
    job_by_name = {}
    for job in all_jobs:
        job_by_name[job.name] = job
        if job.status == "completed" and job.conclusion == "success":
            completed_keys.add(job.name)

    promoted = []
    for job in all_jobs:
        if job.status != "waiting":
            continue
        needs = job.needs or []
        if all(n in completed_keys for n in needs):
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

    if run.status == "completed":
        return run

    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run_id)
    )
    for job in result.scalars().all():
        if job.status in ("queued", "waiting"):
            job.status = "completed"
            job.conclusion = "cancelled"
            job.completed_at = now

    run.status = "completed"
    run.conclusion = "cancelled"
    await db.flush()
    return run

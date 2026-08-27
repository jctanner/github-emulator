"""Workflow detection, trigger evaluation, and run lifecycle management."""

import asyncio
import copy
import fnmatch
import itertools
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import yaml
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.actions import Workflow, WorkflowRun, WorkflowJob, Secret, Variable
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.user import User

logger = logging.getLogger("github_emulator.workflows")

_EXPRESSION_RE = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")


@dataclass(frozen=True)
class EventEnvelope:
    """Normalized event data used by the Actions dispatcher.

    The envelope is an internal transport object.  Only ``payload`` is stored
    in ``WorkflowRun.trigger_payload`` and exposed to the runner, matching the
    shape of ``github.event`` rather than leaking dispatch metadata into it.
    """

    delivery_id: str
    event_name: str
    action: str
    repository: Repository
    ref: str
    sha: str
    actor: User
    payload: dict
    occurred_at: datetime


def _lookup_context(context: dict, expression: str) -> str:
    value = _lookup_context_value(context, expression)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _lookup_context_value(context: dict, expression: str) -> object:
    value: object = context
    for part in expression.strip().split("."):
        if isinstance(value, dict):
            value = value.get(part, "")
        else:
            return ""
    return value


class _ExpressionError(ValueError):
    pass


def _expression_truthy(value: object) -> bool:
    return bool(value)


class _IfExpressionParser:
    """Small, safe evaluator for the job-level Actions expression subset."""

    _TOKEN_RE = re.compile(
        r"(?P<space>\s+)|(?P<op>\|\||&&|==|!=|[!(),])|"
        r"(?P<string>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")|"
        r"(?P<number>\d+(?:\.\d+)?)|(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)"
    )

    def __init__(self, expression: str, context: dict):
        self.context = context
        self.tokens = self._tokenize(expression)
        self.position = 0

    @classmethod
    def _tokenize(cls, expression: str) -> list[tuple[str, str]]:
        tokens = []
        position = 0
        while position < len(expression):
            match = cls._TOKEN_RE.match(expression, position)
            if not match:
                raise _ExpressionError(f"unsupported character at {position}")
            position = match.end()
            kind = match.lastgroup
            if kind != "space":
                tokens.append((kind, match.group(0)))
        tokens.append(("eof", ""))
        return tokens

    def _peek(self, value: str | None = None) -> tuple[str, str] | bool:
        token = self.tokens[self.position]
        return token[1] == value if value is not None else token

    def _take(self, value: str | None = None) -> tuple[str, str]:
        token = self.tokens[self.position]
        if value is not None and token[1] != value:
            raise _ExpressionError(f"expected {value!r}")
        self.position += 1
        return token

    def parse(self) -> bool:
        result = self._parse_or()
        if self._peek()[0] != "eof":
            raise _ExpressionError("unexpected trailing expression")
        return _expression_truthy(result)

    def _parse_or(self) -> object:
        result = self._parse_and()
        while self._peek("||"):
            self._take("||")
            right = self._parse_and()
            result = result or right
        return result

    def _parse_and(self) -> object:
        result = self._parse_not()
        while self._peek("&&"):
            self._take("&&")
            right = self._parse_not()
            result = result and right
        return result

    def _parse_not(self) -> object:
        if self._peek("!"):
            self._take("!")
            return not _expression_truthy(self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> object:
        left = self._parse_primary()
        if self._peek("==") or self._peek("!="):
            operator = self._take()[1]
            right = self._parse_primary()
            equal = left == right
            return equal if operator == "==" else not equal
        return left

    def _parse_primary(self) -> object:
        if self._peek("("):
            self._take("(")
            value = self._parse_or()
            self._take(")")
            return value

        kind, token = self._take()
        if kind == "string":
            return token[1:-1].replace("\\'", "'").replace('\\"', '"')
        if kind == "number":
            return float(token) if "." in token else int(token)
        if kind != "name":
            raise _ExpressionError("expected value")

        if self._peek("("):
            self._take("(")
            arguments = []
            if not self._peek(")"):
                arguments.append(self._parse_or())
                while self._peek(","):
                    self._take(",")
                    arguments.append(self._parse_or())
            self._take(")")
            return self._call(token, arguments)

        if token == "true":
            return True
        if token == "false":
            return False
        if token == "null":
            return None
        return _lookup_context_value(self.context, token)

    @staticmethod
    def _call(name: str, arguments: list[object]) -> object:
        if name == "startsWith" and len(arguments) == 2:
            return str(arguments[0] or "").startswith(str(arguments[1] or ""))
        if name == "endsWith" and len(arguments) == 2:
            return str(arguments[0] or "").endswith(str(arguments[1] or ""))
        if name == "contains" and len(arguments) == 2:
            haystack, needle = arguments
            return needle in haystack if isinstance(haystack, (list, dict, str)) else False
        if name == "always" and not arguments:
            return True
        raise _ExpressionError(f"unsupported function {name}")


def evaluate_job_if(condition: object, context: dict) -> bool:
    """Evaluate a job-level ``if`` condition using Actions-like semantics."""
    if condition is None:
        return True
    if isinstance(condition, bool):
        return condition
    if not isinstance(condition, str):
        return _expression_truthy(condition)

    expression = condition.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2].strip()
    try:
        return _IfExpressionParser(expression, context).parse()
    except _ExpressionError as exc:
        logger.warning("Unable to evaluate job condition %r: %s", condition, exc)
        return False


def render_expressions(value: object, context: dict) -> object:
    """Render the small expression subset needed by the M2 runner contract."""
    if isinstance(value, str):
        def replace(match):
            expression = match.group(1).strip()
            # Step outputs only exist after a prior step has run. Preserve the
            # expression for the runner's runtime renderer.
            if expression.startswith("steps."):
                return match.group(0)
            # Keep one workflow usable for both an automatic event and an
            # explicit workflow_dispatch. This is the common GitHub Actions
            # fallback form used by the Fullsend fixtures.
            for alternative in expression.split("||"):
                resolved = _lookup_context(context, alternative)
                if resolved:
                    return resolved
            return ""
        return _EXPRESSION_RE.sub(replace, value)
    if isinstance(value, dict):
        return {key: render_expressions(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render_expressions(item, context) for item in value]
    return value


async def detect_workflows(repo_disk_path: str, ref: str = "HEAD") -> list[dict]:
    """Read workflow files from *ref* in a bare repository."""
    proc = await asyncio.create_subprocess_exec(
        "git", "ls-tree", "--name-only", ref, ".github/workflows/",
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
            "git", "show", f"{ref}:{path}",
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
        event_config = on_config.get(event)
        if event_config is None and event not in on_config:
            return False

        # PyYAML may parse an empty mapping as {}, and GitHub treats both an
        # empty mapping and a null value as the event's default configuration.
        if event_config is None or event_config == {}:
            return True

        if event == "push":
            return _match_push(event_config, payload)
        if event in ("pull_request", "pull_request_target"):
            return _match_pull_request(event_config, payload)
        if event in ("issues", "issue_comment", "pull_request_review"):
            return _match_types(event_config, payload)
        if event == "workflow_dispatch":
            return True

        return True

    return False


def _match_push(config: dict, payload: dict) -> bool:
    if not isinstance(config, dict):
        return True
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
    if not isinstance(config, dict):
        return True
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


def _match_types(config: object, payload: dict) -> bool:
    """Apply the common ``types`` allowlist used by activity triggers."""
    if not isinstance(config, dict):
        return True
    types = config.get("types")
    if types is None:
        return True
    if isinstance(types, str):
        types = [types]
    return payload.get("action", "") in types


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
            "uses": config.get("uses"),
            "env": config.get("env", {}),
            "strategy": config.get("strategy", {}),
            "permissions": config.get("permissions", {}),
            "if": config.get("if"),
            "timeout_minutes": config.get("timeout-minutes", 360),
        })

    return _topo_sort(jobs)


_MAX_REUSABLE_WORKFLOW_DEPTH = 8


def _render_reusable_call_context(value: object, inputs: dict, secrets: dict) -> object:
    """Resolve only the contexts supplied to a reusable workflow call.

    ``github.*`` and ``steps.*`` belong to the eventual caller/runner context
    and must remain available for the normal expression pass.  Inputs and
    secrets, however, are lexical values of the called workflow and must be
    substituted before its jobs are flattened into the caller run.
    """
    if isinstance(value, str):
        def replace(match):
            expression = match.group(1).strip()
            if expression.startswith("inputs."):
                return _lookup_context({"inputs": inputs}, expression)
            if expression.startswith("secrets."):
                return _lookup_context({"secrets": secrets}, expression)
            return match.group(0)

        return _EXPRESSION_RE.sub(replace, value)
    if isinstance(value, dict):
        return {
            key: _render_reusable_call_context(item, inputs, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_render_reusable_call_context(item, inputs, secrets) for item in value]
    return value


async def _resolve_reusable_workflow(
    uses: str,
    repo_disk_path: str,
    ref: str,
    db: AsyncSession | None,
) -> tuple[dict | None, str, str, str]:
    """Resolve a local or imported reusable workflow reference."""
    if "@" in uses:
        reference, called_ref = uses.rsplit("@", 1)
    else:
        # GitHub's local reusable-workflow form is
        # ``./.github/workflows/workflow.yml`` and intentionally has no ref.
        # It is resolved from the same repository and the caller's ref.
        if not uses.startswith("./"):
            return None, repo_disk_path, ref, uses
        reference, called_ref = uses, ref
    called_ref = called_ref or ref
    called_repo_path = repo_disk_path
    workflow_path = reference

    if reference.startswith("./"):
        workflow_path = reference[2:]
    elif db is not None:
        # OWNER/REPO/.github/workflows/file.yml@REF. Split only the first two
        # components because Fullsend's repository is named ``.fullsend``.
        parts = reference.split("/", 2)
        if len(parts) != 3:
            return None, repo_disk_path, called_ref, uses
        called_owner, called_repo, workflow_path = parts
        repo_result = await db.execute(
            select(Repository).where(
                Repository.full_name == f"{called_owner}/{called_repo}"
            )
        )
        called_repository = repo_result.scalar_one_or_none()
        if called_repository is None or not called_repository.disk_path:
            return None, repo_disk_path, called_ref, uses
        called_repo_path = called_repository.disk_path

    called_files = await detect_workflows(called_repo_path, called_ref)
    called = next(
        (candidate for candidate in called_files
         if candidate.get("_path") == workflow_path),
        None,
    )
    return called, called_repo_path, called_ref, uses


async def _materialize_reusable_jobs(
    workflow_yaml: dict,
    repo_disk_path: str,
    ref: str,
    db: AsyncSession | None,
    *,
    inputs: dict,
    secrets: dict,
    depth: int,
    ancestry: tuple[str, ...],
) -> dict:
    jobs = workflow_yaml.get("jobs", {})
    if not isinstance(jobs, dict):
        return copy.deepcopy(workflow_yaml)

    result = copy.deepcopy(workflow_yaml)
    expanded: dict[str, dict] = {}
    for key, config in jobs.items():
        if not isinstance(config, dict) or not config.get("uses"):
            expanded[key] = _render_reusable_call_context(config, inputs, secrets)
            continue

        uses = str(config["uses"])
        if depth >= _MAX_REUSABLE_WORKFLOW_DEPTH or uses in ancestry:
            placeholder = copy.deepcopy(config)
            placeholder["steps"] = []
            placeholder["name"] = placeholder.get("name", f"{key} (reusable workflow)")
            expanded[key] = placeholder
            continue

        called, called_repo_path, called_ref, _ = await _resolve_reusable_workflow(
            uses, repo_disk_path, ref, db
        )
        if called is None or not isinstance(called.get("jobs"), dict):
            # Keep unresolved calls inspectable and non-successful rather than
            # silently dropping them from the run graph.
            placeholder = copy.deepcopy(config)
            placeholder["steps"] = []
            placeholder["name"] = placeholder.get("name", f"{key} (reusable workflow)")
            expanded[key] = placeholder
            continue

        call_inputs = _render_reusable_call_context(config.get("with", {}), inputs, secrets)
        call_secrets = _render_reusable_call_context(config.get("secrets", {}), inputs, secrets)
        if not isinstance(call_inputs, dict):
            call_inputs = {}
        if not isinstance(call_secrets, dict):
            call_secrets = {}

        materialized = await _materialize_reusable_jobs(
            called,
            called_repo_path,
            called_ref,
            db,
            inputs=call_inputs,
            secrets=call_secrets,
            depth=depth + 1,
            ancestry=(*ancestry, uses),
        )
        called_jobs = materialized.get("jobs", {})
        called_keys = set(called_jobs)
        prefix = f"{key} / "
        for called_key, called_config in called_jobs.items():
            child = copy.deepcopy(called_config)
            child["name"] = child.get("name", f"{key} / {called_key}")
            caller_condition = config.get("if")
            child_condition = child.get("if")
            if caller_condition is not None:
                child["if"] = (
                    caller_condition
                    if child_condition is None
                    else f"({caller_condition}) && ({child_condition})"
                )
            child["env"] = {
                **(materialized.get("env") or {}),
                **(child.get("env") or {}),
            }
            child_needs = child.get("needs", [])
            if isinstance(child_needs, str):
                child_needs = [child_needs]
            child["needs"] = [
                f"{prefix}{dependency}" if dependency in called_keys else dependency
                for dependency in child_needs
            ]
            if config.get("needs"):
                outer_needs = config["needs"]
                if isinstance(outer_needs, str):
                    outer_needs = [outer_needs]
                child["needs"] = [*outer_needs, *child["needs"]]
            expanded[f"{prefix}{called_key}"] = child

    result["jobs"] = expanded
    return result


async def materialize_reusable_workflows(
    workflow_yaml: dict,
    repo_disk_path: str,
    ref: str = "HEAD",
    db: AsyncSession | None = None,
    *,
    inputs: dict | None = None,
    secrets: dict | None = None,
) -> dict:
    """Recursively inline local and imported job-level reusable workflows.

    GitHub executes a job-level ``uses:`` workflow with the caller's event and
    repository context, while exposing the call's ``with`` and ``secrets`` as
    the called workflow's ``inputs`` and ``secrets`` contexts.  The lightweight
    emulator represents the nested jobs in one inspectable run, but resolves
    those lexical contexts and remaps dependencies so nested calls behave like
    their GitHub counterparts.
    """
    return await _materialize_reusable_jobs(
        workflow_yaml,
        repo_disk_path,
        ref,
        db,
        inputs=inputs or {},
        secrets=secrets or {},
        depth=0,
        ancestry=(),
    )


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
    db: AsyncSession, repository: Repository, ref: str = "HEAD"
) -> list[Workflow]:
    """Upsert Workflow rows from on-disk workflow files."""
    detected = await detect_workflows(repository.disk_path, ref)

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

    default_ref = repository.default_branch or "main"
    for path, w in existing.items():
        if ref in {"HEAD", default_ref} and path not in seen_paths:
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
            # Actions expressions expose the complete webhook-shaped payload
            # as github.event.  Keep this separate from the runner transport
            # metadata so jobs can use expressions such as
            # github.event.issue.number and github.event.pull_request.number.
            "event": payload,
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
    skipped_job_created = False

    for job_def in job_list:
        expanded = expand_matrix(job_def)
        for job_config in expanded:
            job_expression_context = {**expression_context, "matrix": job_config.get("_matrix", {})}
            display_name = render_expressions(job_config.get("_display_name", job_config.get("name", job_config["key"])), job_expression_context)
            needs = job_config.get("needs", [])
            initial_status = "queued" if not needs else "waiting"
            should_run = evaluate_job_if(job_config.get("if"), job_expression_context)

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
                if "if" in step:
                    # Keep step conditions intact: expressions may depend on
                    # outputs produced later by an earlier runner step.
                    step_data["if"] = step["if"]
                for key in ("run", "shell", "working-directory", "uses", "with"):
                    if key in step:
                        step_data[key] = render_expressions(step[key], job_expression_context)
                if "id" in step:
                    step_data["id"] = step["id"]
                if step_env:
                    step_data["env"] = render_expressions(step_env, job_expression_context)
                steps_data.append(step_data)

            conclusion = None
            completed_at = None
            if not should_run:
                initial_status = "completed"
                conclusion = "skipped"
                completed_at = datetime.now(timezone.utc)
                skipped_job_created = True
                steps_data = [
                    {**step, "status": "completed", "conclusion": "skipped"}
                    for step in steps_data
                ]

            job = WorkflowJob(
                run_id=run.id,
                name=display_name,
                workflow_name=workflow.name,
                status=initial_status,
                conclusion=conclusion,
                completed_at=completed_at,
                steps=steps_data,
                labels=job_config.get("runs_on", ["ubuntu-latest"]),
                run_attempt=1,
                needs=needs,
                permissions=job_config.get("permissions") or {},
            )
            db.add(job)

    await db.flush()
    # A run containing only skipped jobs should finish immediately. Also
    # propagate skipped dependencies before the first runner poll.
    if skipped_job_created:
        await dispatch_ready_jobs(db, run.id)
        await check_run_completion(db, run.id)
    return run


def _user_payload(user: User | None) -> dict | None:
    if user is None:
        return None
    return {
        "login": user.login,
        "id": user.id,
        "node_id": f"U_{user.id}",
        "type": getattr(user, "type", "User") or "User",
        "site_admin": bool(user.site_admin),
    }


def _repository_payload(repository: Repository) -> dict:
    owner = _user_payload(repository.owner)
    return {
        "id": repository.id,
        "node_id": f"R_{repository.id}",
        "name": repository.name,
        "full_name": repository.full_name,
        "private": bool(repository.private),
        "default_branch": repository.default_branch,
        "description": repository.description,
        "owner": owner,
        "html_url": f"{settings.BASE_URL}/{repository.full_name}",
        "url": f"{settings.BASE_URL}/api/v3/repos/{repository.full_name}",
    }


def _label_payload(label) -> dict:
    return {
        "id": label.id,
        "node_id": f"L_{label.id}",
        "name": label.name,
        "color": label.color,
        "description": label.description,
    }


def _issue_payload(issue, repository: Repository) -> dict:
    return {
        "id": issue.id,
        "node_id": f"I_{issue.id}",
        "number": issue.number,
        "title": issue.title,
        "body": issue.body,
        "state": issue.state,
        "state_reason": issue.state_reason,
        "user": _user_payload(issue.user),
        "labels": [_label_payload(label) for label in (issue.labels or [])],
        "locked": bool(issue.locked),
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
        "updated_at": issue.updated_at.isoformat() if issue.updated_at else None,
        "closed_at": issue.closed_at.isoformat() if issue.closed_at else None,
        "html_url": f"{settings.BASE_URL}/{repository.full_name}/issues/{issue.number}",
        "repository": _repository_payload(repository),
    }


def _pull_request_payload(pr, issue, repository: Repository) -> dict:
    base = {
        "ref": pr.base_ref,
        "sha": pr.base_sha,
        "label": f"{repository.full_name.split('/', 1)[0]}:{pr.base_ref}",
    }
    head = {
        "ref": pr.head_ref,
        "sha": pr.head_sha,
        "label": f"{repository.full_name.split('/', 1)[0]}:{pr.head_ref}",
    }
    return {
        "id": pr.id,
        "node_id": f"PR_{pr.id}",
        "number": issue.number,
        "title": issue.title,
        "body": issue.body,
        "state": issue.state,
        "user": _user_payload(issue.user),
        "draft": bool(pr.draft),
        "merged": bool(pr.merged),
        "merge_commit_sha": pr.merge_commit_sha,
        "base": base,
        "head": head,
        "labels": [_label_payload(label) for label in (issue.labels or [])],
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
        "updated_at": issue.updated_at.isoformat() if issue.updated_at else None,
        "closed_at": issue.closed_at.isoformat() if issue.closed_at else None,
        "html_url": f"{settings.BASE_URL}/{repository.full_name}/pull/{issue.number}",
        "repo": _repository_payload(repository),
    }


def build_activity_payload(
    repository: Repository,
    actor: User,
    action: str,
    *,
    issue=None,
    pull_request=None,
    comment=None,
    review=None,
    label=None,
    ref: str | None = None,
    sha: str | None = None,
) -> dict:
    """Build the GitHub-like payload shared by REST-triggered activities."""
    payload = {
        "action": action,
        "repository": _repository_payload(repository),
        "sender": _user_payload(actor),
    }
    if issue is not None:
        payload["issue"] = _issue_payload(issue, repository)
    if pull_request is not None and issue is not None:
        payload["pull_request"] = _pull_request_payload(pull_request, issue, repository)
    if comment is not None:
        payload["comment"] = {
            "id": comment.id,
            "node_id": f"IC_{comment.id}",
            "body": comment.body,
            "user": _user_payload(comment.user),
            "created_at": comment.created_at.isoformat() if comment.created_at else None,
            "updated_at": comment.updated_at.isoformat() if comment.updated_at else None,
            "html_url": f"{settings.BASE_URL}/{repository.full_name}/issues/{issue.number}#issuecomment-{comment.id}",
        }
    if review is not None:
        payload["review"] = {
            "id": review.id,
            "node_id": f"REV_{review.id}",
            "body": review.body,
            "state": review.state,
            "user": _user_payload(review.user),
            "commit_id": review.commit_id,
            "submitted_at": review.submitted_at.isoformat() if review.submitted_at else None,
        }
    if label is not None:
        payload["label"] = _label_payload(label)
    if ref:
        payload["ref"] = ref
    if sha:
        payload["after"] = sha
    return payload


async def dispatch_event(
    db: AsyncSession,
    repository: Repository,
    actor: User,
    event: str,
    action: str,
    payload: dict,
    *,
    ref: str | None = None,
    sha: str | None = None,
) -> list[WorkflowRun]:
    """Dispatch one successful repository activity to matching workflows."""
    if not repository.disk_path or not actor:
        return []

    ref = ref or repository.default_branch or "main"
    ref_name = ref.removeprefix("refs/heads/")
    ref_spec = ref if ref.startswith(("refs/", "HEAD")) else ref_name
    sha = sha or payload.get("after") or await get_ref_sha(repository.disk_path, ref_spec)
    payload.setdefault("ref", f"refs/heads/{ref_name}")
    payload.setdefault("after", sha)

    # Reconcile the Actions workflow inventory before matching this event. This
    # keeps deleted workflows from remaining active after a push removes them,
    # while sync_workflows_to_db only reconciles the default branch.
    await sync_workflows_to_db(db, repository, ref_spec)

    envelope = EventEnvelope(
        delivery_id=str(uuid.uuid4()),
        event_name=event,
        action=action,
        repository=repository,
        ref=payload["ref"],
        sha=sha,
        actor=actor,
        payload=payload,
        occurred_at=datetime.now(timezone.utc),
    )

    workflows_yaml = await detect_workflows(repository.disk_path, ref_spec)
    result = await db.execute(select(Workflow).where(Workflow.repo_id == repository.id))
    known = {workflow.path: workflow for workflow in result.scalars().all()}
    runs = []
    for workflow_yaml in workflows_yaml:
        path = workflow_yaml.get("_path", "")
        workflow = known.get(path)
        if workflow is None:
            workflow = Workflow(
                repo_id=repository.id,
                name=workflow_yaml.get("name", path),
                path=path,
                state="active",
            )
            db.add(workflow)
            await db.flush()
            known[path] = workflow
        else:
            workflow.name = workflow_yaml.get("name", path)
            workflow.state = "active"

        if not evaluate_trigger(workflow_yaml, envelope.event_name, envelope.payload):
            continue
        workflow_yaml = await materialize_reusable_workflows(
            workflow_yaml,
            repository.disk_path,
            ref_spec,
            db,
            inputs=payload.get("inputs", {}),
            secrets=payload.get("secrets", {}),
        )
        run = await create_workflow_run(
            db,
            workflow,
            workflow_yaml,
            envelope.event_name,
            envelope.payload,
            envelope.actor,
            envelope.sha,
            ref_name,
        )
        runs.append(run)

    if runs:
        await db.commit()
        logger.info(
            "Dispatched %s/%s to %d workflow(s) for %s",
            event,
            action,
            len(runs),
            repository.full_name,
        )
    return runs


async def process_push_event(
    db: AsyncSession,
    repository: Repository,
    user: User,
    *,
    before_sha: str | None = None,
    ref_name: str | None = None,
    after_sha: str | None = None,
    created: bool = False,
    deleted: bool = False,
    forced: bool = False,
) -> list[WorkflowRun]:
    """Dispatch a push and any matching pull-request synchronizations."""
    if not repository.disk_path:
        return []
    user = user or repository.owner
    if user is None:
        return []

    head_branch = ref_name or repository.default_branch or "main"
    head_sha = after_sha
    if head_sha is None and not deleted:
        head_sha = await get_ref_sha(repository.disk_path, head_branch)
        head_sha = head_sha or await _get_head_sha(repository.disk_path)
    head_sha = head_sha or "0" * 40
    before_sha = before_sha or "0" * 40
    commits = []
    if head_sha:
        changed_files = await _get_changed_files_between(
            repository.disk_path, before_sha, head_sha,
        )
        if changed_files:
            commits.append({
                "id": head_sha,
                "added": [path for path, status in changed_files if status == "A"],
                "modified": [path for path, status in changed_files if status == "M"],
                "removed": [path for path, status in changed_files if status == "D"],
            })

    payload = {
        "ref": f"refs/heads/{head_branch}",
        "after": head_sha,
        "before": before_sha,
        "created": created,
        "deleted": deleted,
        "forced": forced,
        "commits": commits,
        "repository": {"id": repository.id, "full_name": repository.full_name},
        "pusher": {"name": user.login, "email": user.email or ""},
        "sender": {"login": user.login, "id": user.id},
    }
    runs = await dispatch_event(
        db, repository, user, "push", "", payload,
        ref=head_branch, sha=head_sha,
    )

    # A deleted head ref cannot synchronize an open PR; the push event is the
    # only event generated for that ref update.
    if deleted:
        return runs

    # A push to a pull request's head branch is the source of the
    # pull_request_target ``synchronize`` activity.  The base branch is the
    # checkout/ref used for the resulting run.
    result = await db.execute(
        select(PullRequest)
        .join(Issue, PullRequest.issue_id == Issue.id)
        .where(PullRequest.repo_id == repository.id, PullRequest.head_ref == head_branch)
    )
    for pr in result.scalars().all():
        issue = pr.issue
        pr_payload = build_activity_payload(
            repository, user, "synchronize", issue=issue,
            pull_request=pr, ref=f"refs/heads/{pr.base_ref}", sha=pr.base_sha,
        )
        runs.extend(await dispatch_event(
            db, repository, user, "pull_request_target", "synchronize",
            pr_payload, ref=pr.base_ref, sha=pr.base_sha,
        ))
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


async def _get_changed_files_between(
    repo_disk_path: str, before_sha: str, after_sha: str
) -> list[tuple[str, str]]:
    """Return ``(path, status)`` pairs for a push range."""
    if before_sha == "0" * 40:
        before_sha = ""
    args = ["git", "diff-tree", "--no-commit-id", "--name-status", "-r"]
    if before_sha:
        args.append(f"{before_sha}..{after_sha}")
    else:
        args.extend(["--root", after_sha])
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"GIT_DIR": repo_disk_path},
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return []
    changed = []
    for line in stdout.decode().splitlines():
        status, _, path = line.partition("\t")
        if path and status:
            changed.append((path, status[0]))
    return changed


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

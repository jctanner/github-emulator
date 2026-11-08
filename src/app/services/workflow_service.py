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
from app.models.actions import Workflow, WorkflowRun, WorkflowJob, Secret
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.user import User

logger = logging.getLogger("github_emulator.workflows")

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


from app.services.workflow_expressions import (
    _EXPRESSION_RE,
    _ExpressionError,
    _IfExpressionParser,
    _lookup_context,
    bind_server_context,
    evaluate_expression_value,
    evaluate_job_if,
    render_expressions,
)

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


from app.services.webhook_service import schedule_webhooks
from app.services.cross_repo_pulls import same_repository_head, sync_head_ref
from app.services.workflow_triggers import evaluate_trigger

def dynamic_matrix_expression(job_config: dict) -> str | None:
    """The expression of a dynamic matrix (``matrix: ${{ fromJSON(...) }}``),
    or None when the matrix is written out or absent."""
    strategy = job_config.get("strategy") or {}
    if not isinstance(strategy, dict):
        return None
    matrix = strategy.get("matrix")
    return matrix if isinstance(matrix, str) else None


def matrix_combinations(matrix: object) -> list[dict] | None:
    """The job instances a matrix produces, as on GitHub: the product of its
    vectors less ``exclude``, then ``include``. An entry of ``include`` that
    matches no combination becomes one of its own; one that extends a
    combination without overwriting its original values is merged into it.
    An include-only matrix is just its entries.

    Returns None for a matrix that is not a mapping, and an empty list for
    one with no instances, which GitHub fails the job for.
    """
    if not isinstance(matrix, dict):
        return None
    matrix = dict(matrix)
    include = matrix.pop("include", None) or []
    exclude = matrix.pop("exclude", None) or []
    keys = list(matrix.keys())
    combos: list[dict] = []
    if keys:
        values = [matrix[k] if isinstance(matrix[k], list) else [matrix[k]] for k in keys]
        for combo in itertools.product(*values):
            entry = dict(zip(keys, combo))
            if not any(
                isinstance(exc, dict) and all(entry.get(k) == v for k, v in exc.items())
                for exc in exclude
            ):
                combos.append(entry)
    originals = [dict(c) for c in combos]
    for inc in include if isinstance(include, list) else []:
        if not isinstance(inc, dict):
            continue
        merged = False
        for combo, original in zip(combos, originals):
            if all(original[k] == v for k, v in inc.items() if k in original):
                combo.update(inc)
                merged = True
        if not merged:
            combos.append(dict(inc))
    return combos


def matrix_job_name(template: str, combo: dict) -> str:
    """A matrix instance's unrendered name: GitHub appends the instance's
    values only when the name does not already use the matrix context."""
    if not combo or re.search(r"\$\{\{[^}]*\bmatrix\b", template):
        return template
    return f"{template} ({', '.join(str(v) for v in combo.values())})"


def strategy_context(
    strategy: dict | None, index: int = 0, total: int = 1, context: dict | None = None,
) -> dict:
    """The ``strategy`` context of one job instance. Fullsend's harness job
    reads ``strategy.job-index`` to find its own entry in the matrix.

    ``fail-fast`` and ``max-parallel`` may be expressions
    (``fail-fast: ${{ inputs.fast }}``); with a context they are evaluated.
    """
    strategy = strategy if isinstance(strategy, dict) else {}
    fail_fast = strategy.get("fail-fast", True)
    max_parallel = strategy.get("max-parallel", total)
    if context is not None:
        fail_fast = evaluate_expression_value(fail_fast, context)
        max_parallel = evaluate_expression_value(max_parallel, context)
    if isinstance(max_parallel, str):
        try:
            max_parallel = int(max_parallel)
        except ValueError:
            max_parallel = total
    return {
        "fail-fast": fail_fast is not False and str(fail_fast).lower() != "false",
        "job-index": index,
        "job-total": total,
        "max-parallel": max_parallel if isinstance(max_parallel, int) else total,
    }


def render_job_labels(labels: list | None, context: dict) -> list:
    """A job's runner labels with any expressions in them rendered."""
    return [
        str(render_expressions(label, context)) if isinstance(label, str) and "${{" in label else label
        for label in (labels or [])
    ]


def expand_matrix(job_config: dict) -> list[dict]:
    """Expand strategy.matrix into individual job configurations.

    A dynamic matrix is a string here: it may depend on a job that has not
    run yet, so the job stays one row and is expanded when it is promoted
    (see ``dispatch_ready_jobs``).
    """
    strategy = job_config.get("strategy", {}) or {}
    if not isinstance(strategy, dict):
        return [job_config]
    raw_matrix = strategy.get("matrix", {}) or {}
    if isinstance(raw_matrix, str):
        return [job_config]
    combos = matrix_combinations(raw_matrix)
    if not combos:
        return [job_config]

    expanded = []
    for index, combo in enumerate(combos):
        job_copy = {k: v for k, v in job_config.items() if k != "strategy"}
        job_copy["_matrix"] = combo
        job_copy["_strategy"] = {
            "options": {k: v for k, v in strategy.items() if k != "matrix"},
            "index": index,
            "total": len(combos),
        }
        job_copy["_display_name"] = matrix_job_name(
            job_config.get("name") or job_config.get("key", ""), combo,
        )
        expanded.append(job_copy)

    return expanded


def build_job_graph(workflow_yaml: dict) -> list[dict]:
    """Parse jobs section into a dependency-ordered list."""
    jobs_config = workflow_yaml.get("jobs", {})
    if not jobs_config:
        return []

    # A workflow-level ``permissions:`` block applies to every job that does
    # not declare its own; a job's own block replaces it outright rather than
    # merging, which is what GitHub does. Reading only the job level meant a
    # workflow that scoped its token once at the top was recorded as declaring
    # nothing, so an id-token request was refused and every scope check saw an
    # empty grant.
    workflow_permissions = workflow_yaml.get("permissions")

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
            "permissions": (
                config["permissions"]
                if "permissions" in config
                else workflow_permissions
                if workflow_permissions is not None
                else {}
            ),
            "if": config.get("if"),
            # Carried through so job-level concurrency reaches job creation.
            "concurrency": config.get("concurrency"),
            "outputs": config.get("outputs", {}),
            "_call_inputs": config.get("_call_inputs", {}),
            "_call_secrets": config.get("_call_secrets", {}),
            "_workflow_repository": config.get("_workflow_repository", ""),
            "_workflow_sha": config.get("_workflow_sha", ""),
            "_workflow_ref": config.get("_workflow_ref", ""),
            "timeout_minutes": config.get("timeout-minutes", 360),
        })

    return _topo_sort(jobs)


_MAX_REUSABLE_WORKFLOW_DEPTH = 8

# A single context path such as ``inputs.mint_url``: letters, digits, dots,
# dashes and underscores only, with no operators, quotes, or calls.
_BARE_CONTEXT_PATH_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")


def _condition_body(condition: object) -> str:
    """Return a condition's expression text without its ``${{ }}`` wrapper."""
    text = str(condition).strip()
    if text.startswith("${{") and text.endswith("}}"):
        return text[3:-2].strip()
    return text


def _combine_conditions(caller_condition: object, child_condition: object) -> str:
    """AND a caller job's condition into an inlined child job's condition.

    Both sides are unwrapped first. Concatenating them with their wrappers
    intact produced ``(...) && (${{ ... }})``, where the inner marker sits
    mid-expression and cannot be parsed, so the combined condition failed and
    the job was silently skipped.
    """
    caller_text = _condition_body(caller_condition)
    if child_condition is None:
        return caller_text
    child_text = _condition_body(child_condition)
    if not child_text:
        return caller_text
    if not caller_text:
        return child_text
    return f"({caller_text}) && ({child_text})"


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
            # Only a bare path may be substituted lexically. A compound
            # expression such as ``inputs.matrix == ''`` merely starts with
            # "inputs." and must survive for the real evaluator, which reads
            # these values from the job's expression context. Rewriting it as a
            # path lookup yielded an empty string, which made the condition
            # unparseable and silently skipped the job.
            if not _BARE_CONTEXT_PATH_RE.fullmatch(expression):
                return match.group(0)
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
) -> tuple[dict | None, str, str, str, str]:
    """Resolve a local or imported reusable workflow reference."""
    if "@" in uses:
        reference, called_ref = uses.rsplit("@", 1)
    else:
        # GitHub's local reusable-workflow form is
        # ``./.github/workflows/workflow.yml`` and intentionally has no ref.
        # It is resolved from the same repository and the caller's ref.
        if not uses.startswith("./"):
            return None, repo_disk_path, ref, uses, ""
        reference, called_ref = uses, ref
    called_ref = called_ref or ref
    called_repo_path = repo_disk_path
    called_full_name = ""
    workflow_path = reference

    if reference.startswith("./"):
        workflow_path = reference[2:]
        # A local call names no repository, but GitHub still reports one: the
        # calling repository, in job_workflow_ref (owner/repo/path@ref) and as
        # the workflow's source. Left empty, the OIDC claim read
        # "/.github/workflows/x.yml@..." and a mint keyed on the workflow host
        # refused every vendored install. The calling repository is the one
        # whose disk path this call resolves against, also when the local call
        # sits inside a workflow imported from another repository.
        if db is not None:
            called_full_name = (
                await db.execute(
                    select(Repository.full_name).where(
                        Repository.disk_path == repo_disk_path
                    )
                )
            ).scalar_one_or_none() or ""
    elif db is not None:
        # OWNER/REPO/.github/workflows/file.yml@REF. Split only the first two
        # components because Fullsend's repository is named ``.fullsend``.
        parts = reference.split("/", 2)
        if len(parts) != 3:
            return None, repo_disk_path, called_ref, uses, ""
        called_owner, called_repo, workflow_path = parts
        repo_result = await db.execute(
            select(Repository).where(
                Repository.full_name == f"{called_owner}/{called_repo}"
            )
        )
        called_repository = repo_result.scalar_one_or_none()
        if called_repository is None or not called_repository.disk_path:
            return None, repo_disk_path, called_ref, uses, ""
        called_repo_path = called_repository.disk_path
        called_full_name = called_repository.full_name

    called_files = await detect_workflows(called_repo_path, called_ref)
    called = next(
        (candidate for candidate in called_files
         if candidate.get("_path") == workflow_path),
        None,
    )
    return called, called_repo_path, called_ref, uses, called_full_name


async def _resolve_ref_sha(repo_disk_path: str, ref: str) -> str:
    """Resolve a ref to its commit sha inside a bare repository."""
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", ref,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"GIT_DIR": repo_disk_path},
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return ""
    return stdout.decode().strip()


def _declared_call_inputs(called: dict) -> dict:
    """Return the `on.workflow_call.inputs` declaration of a called workflow."""
    on = called.get("on") or called.get(True) or {}
    if not isinstance(on, dict):
        return {}
    call = on.get("workflow_call")
    if not isinstance(call, dict):
        return {}
    declared = call.get("inputs")
    return declared if isinstance(declared, dict) else {}


def _coerce_input(value: object, declared_type: str) -> object:
    """Coerce a supplied input to its declared type.

    Values arrive from YAML or from rendered expressions, so a boolean input
    frequently arrives as the string "true". A condition testing it then
    compares a string to a boolean and silently takes the wrong branch.
    """
    if declared_type == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() == "true"
    if declared_type == "number":
        try:
            text = str(value).strip()
            return int(text) if text.lstrip("-").isdigit() else float(text)
        except (TypeError, ValueError):
            return value
    if declared_type == "string" and not isinstance(value, str):
        return "" if value is None else str(value)
    return value


def apply_workflow_call_inputs(called: dict, supplied: dict) -> tuple[dict, list[str]]:
    """Merge declared defaults into the supplied inputs and coerce types.

    An unsupplied input rendered as empty rather than as its declared default,
    so a guard like `inputs.install_mode == 'per-repo'` was comparing against
    "" and only worked when the caller happened to pass the value. Returns the
    resolved inputs and the names of any `required: true` inputs the caller
    omitted, which the caller logs rather than raising on: GitHub fails the
    run, but failing here would turn a latent workflow bug into an outage in a
    stack whose whole point is to surface such things visibly.
    """
    declared = _declared_call_inputs(called)
    resolved = dict(supplied)
    missing_required: list[str] = []

    for name, spec in declared.items():
        if not isinstance(spec, dict):
            continue
        declared_type = str(spec.get("type", "string"))
        if name in resolved and resolved[name] not in (None, ""):
            resolved[name] = _coerce_input(resolved[name], declared_type)
            continue
        if "default" in spec:
            resolved[name] = _coerce_input(spec["default"], declared_type)
        elif spec.get("required") is True:
            missing_required.append(name)
    return resolved, missing_required


def _called_workflow_ref(uses: str, called_full_name: str, called_ref: str) -> str:
    """`owner/repo/.github/workflows/x.yml@v1` -> `owner/repo/.github/workflows/x.yml@refs/heads/v1`.

    Local (`./`) calls resolve against the calling repository, which the
    caller supplies as ``called_full_name``.
    """
    reference = uses.split("@", 1)[0]
    if reference.startswith("./"):
        path = reference[2:]
        name = called_full_name
    else:
        parts = reference.split("/", 2)
        name = "/".join(parts[:2])
        path = parts[2] if len(parts) == 3 else ""
    ref = called_ref or ""
    if ref and not ref.startswith("refs/") and not _looks_like_sha(ref):
        ref = f"refs/heads/{ref}"
    return f"{name}/{path}@{ref}"


def _looks_like_sha(value: str) -> bool:
    return len(value) == 40 and all(c in "0123456789abcdef" for c in value.lower())


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

        called, called_repo_path, called_ref, _, called_full_name = (
            await _resolve_reusable_workflow(uses, repo_disk_path, ref, db)
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
        if not isinstance(call_inputs, dict):
            call_inputs = {}
        call_inputs, missing_required = apply_workflow_call_inputs(called, call_inputs)
        if missing_required:
            logger.warning(
                "reusable workflow %s called without required input(s): %s",
                uses, ", ".join(sorted(missing_required)),
            )

        # `secrets: inherit` is a string, not a mapping. Parsing it as one and
        # then discarding it handed the called workflow no secrets at all
        # while looking like it had been honoured.
        raw_secrets = config.get("secrets")
        if isinstance(raw_secrets, str) and raw_secrets.strip() == "inherit":
            call_secrets = dict(secrets)
        else:
            call_secrets = _render_reusable_call_context(raw_secrets or {}, inputs, secrets)
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
        called_sha = await _resolve_ref_sha(called_repo_path, called_ref)
        called_jobs = materialized.get("jobs", {})
        called_keys = set(called_jobs)
        prefix = f"{key} / "
        for called_key, called_config in called_jobs.items():
            child = copy.deepcopy(called_config)
            child["name"] = child.get("name", f"{key} / {called_key}")
            caller_condition = config.get("if")
            child_condition = child.get("if")
            if caller_condition is not None:
                child["if"] = _combine_conditions(caller_condition, child_condition)
            child["env"] = {
                **(materialized.get("env") or {}),
                **(child.get("env") or {}),
            }
            # Compound expressions (``inputs.matrix == ''``) are not
            # substituted lexically, so the called workflow's inputs and
            # secrets travel with the job and enter its expression context.
            child["_call_inputs"] = {**call_inputs, **(child.get("_call_inputs") or {})}
            child["_call_secrets"] = {**call_secrets, **(child.get("_call_secrets") or {})}
            # Where this job's workflow actually came from. The stage jobs check
            # out their upstream defaults at exactly this repo and commit, which
            # is how ADR 0062 keeps the dispatch and its defaults in step.
            child.setdefault("_workflow_repository", called_full_name)
            child.setdefault("_workflow_sha", called_sha)
            # The called workflow as GitHub names it in job_workflow_ref:
            # owner/repo/path@ref. The OIDC token reports this for a job an
            # inlined reusable workflow defined; the run's own workflow_ref
            # still names the caller (Fullsend ADR 0082 keys the mint on
            # the called one).
            child.setdefault("_workflow_ref", _called_workflow_ref(uses, called_full_name, called_ref))
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


def _instance_strategy(job_config: dict, context: dict) -> dict:
    """The strategy context of a job as expanded by ``expand_matrix``."""
    instance = job_config.get("_strategy")
    if instance:
        return strategy_context(
            instance["options"], instance["index"], instance["total"], context,
        )
    return strategy_context(job_config.get("strategy"), context=context)


def _job_context(base_context: dict, job_config: dict) -> dict:
    """Build a job's expression context, layering any reusable-call values.

    A reusable call's ``with:`` values are written in the caller's terms
    (``event_action: ${{ github.event.action }}``), so they are rendered
    against the caller's context here. Leaving them unrendered made
    ``inputs.event_action`` the literal expression text.
    """
    github = base_context.get("github") or {}
    context = {
        **base_context,
        "matrix": job_config.get("_matrix", {}),
        # A job executing an inlined reusable workflow reports that workflow's
        # repository and commit; a job defined in this repository reports its
        # own. Stage jobs read these to check out their upstream defaults at
        # exactly the revision the dispatch came from.
        "job": {
            "workflow_repository": job_config.get("_workflow_repository")
            or github.get("repository", ""),
            "workflow_sha": job_config.get("_workflow_sha") or github.get("sha", ""),
        },
    }
    call_inputs = job_config.get("_call_inputs") or {}
    call_secrets = job_config.get("_call_secrets") or {}
    if call_inputs:
        rendered_inputs = render_expressions(call_inputs, context)
        context["inputs"] = {**(base_context.get("inputs") or {}), **rendered_inputs}
    if call_secrets:
        rendered_secrets = render_expressions(call_secrets, context)
        context["secrets"] = {**(base_context.get("secrets") or {}), **rendered_secrets}
    # After the call's inputs, which ``fail-fast: ${{ inputs.fast }}`` reads.
    context["strategy"] = _instance_strategy(job_config, context)
    return context


# Step conditions that depend on runtime state stay with the runner; everything
# else is decided server-side, where the full expression context exists.
# hashFiles reads the job workspace, which only the runner can see, so it
# belongs with the other runtime-dependent forms.
_RUNTIME_CONDITION_RE = re.compile(
    r"\bsteps\.|\bsuccess\s*\(|\bfailure\s*\(|\bcancelled\s*\(|\balways\s*\("
    r"|\bhashFiles\s*\("
)


def _resolve_step_condition(condition: object, context: dict) -> object:
    """Pre-evaluate a step ``if`` unless it depends on runtime state.

    The runner can only resolve ``steps.*``; every other path evaluates to the
    empty string there, so a guard such as ``inputs.event_action == ''`` was
    always true and fired incorrectly. Conditions the server can decide are
    reduced to a literal the runner understands.
    """
    text = _condition_body(condition)
    if not text:
        return condition
    if _RUNTIME_CONDITION_RE.search(text):
        # Mixed conditions are common: a step may gate on both a prior step's
        # output and the event context. Bind what is known here so the runner
        # receives an expression it can finish, rather than one it must fail on.
        return bind_server_context(text, context)
    try:
        decided = _IfExpressionParser(text, context).parse()
    except _ExpressionError:
        # Not decidable here. Hand it to the runner rather than defaulting:
        # resolving an unevaluable condition to false silently drops the step,
        # which is how the upstream-defaults checkout disappeared.
        return condition
    return "true" if decided else "false"


def build_steps_data(steps: list, job_env: dict, context: dict) -> list[dict]:
    """Render a job's steps into the stored step records."""
    steps_data = []
    for i, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        step_env = {**(job_env or {}), **(step.get("env") or {})}
        step_data = {
            "number": i + 1,
            "name": step.get("name", f"Step {i + 1}"),
            "status": "queued",
            "conclusion": None,
        }
        if "if" in step:
            # Conditions that read a prior step's outputs must stay intact for
            # the runner; the rest are decided here, where the full context is
            # available.
            step_data["if"] = _resolve_step_condition(step["if"], context)
        for key in ("run", "shell", "working-directory", "uses", "with"):
            if key in step:
                step_data[key] = render_expressions(step[key], context)
        if "id" in step:
            step_data["id"] = step["id"]
        if step_env:
            step_data["env"] = render_expressions(step_env, context)
        steps_data.append(step_data)
    return steps_data


def resolve_job_outputs(outputs_config: dict, step_outputs: dict) -> dict:
    """Resolve a job's ``outputs:`` mapping against its step outputs."""
    if not isinstance(outputs_config, dict) or not outputs_config:
        return {}
    # The runner reports {step_id: {name: value}}, while expressions address
    # steps.<id>.outputs.<name>, so insert the intermediate "outputs" level.
    context = {
        "steps": {
            str(step_id): {"outputs": values or {}}
            for step_id, values in (step_outputs or {}).items()
        }
    }
    resolved = {}
    for name, template in outputs_config.items():
        rendered = render_expressions(template, context, resolve_steps=True)
        resolved[str(name)] = "" if rendered is None else str(rendered)
    return resolved


async def build_run_expression_context(db, run) -> dict:
    """Rebuild the base expression context for an existing run.

    Job promotion happens long after the run was created, so the context is
    derived again from the stored trigger payload and the repository's current
    variables and secrets rather than being persisted with the run.
    """
    payload = run.trigger_payload or {}
    # Organization variables the repository can use, its own over them.
    from app.services.org_variables import vars_context

    variables = await vars_context(db, run.repo_id)
    secrets = {
        item.name: item.value or ""
        for item in (await db.execute(
            select(Secret).where(Secret.repo_id == run.repo_id)
        )).scalars().all()
    }
    repository = payload.get("repository", {}).get("full_name", "")
    return {
        "inputs": payload.get("inputs", {}),
        "vars": variables,
        "secrets": secrets,
        "github": {
            "event_name": run.event,
            "event": payload,
            "ref": payload.get("ref", f"refs/heads/{run.head_branch}"),
            "repository": repository,
            "repository_owner": repository.split("/", 1)[0],
            "run_id": run.id,
            "run_number": run.run_number,
            "sha": run.head_sha,
            "server_url": settings.BASE_URL,
        },
    }


def build_needs_context(jobs) -> dict:
    """Build the ``needs`` context from completed jobs, keyed by YAML job key.

    Inlining a reusable workflow prefixes its job keys with the calling job
    (``route`` becomes ``dispatch / route``) and remaps ``needs:`` to match.
    The called workflow's own expressions still say ``needs.route``, though,
    because that is its name for the job. Each job is therefore registered
    under both its prefixed key and its original one; without the latter,
    every stage condition read an empty stage and skipped.
    """
    context = {}
    aliases: dict[str, str] = {}
    for job in jobs:
        key = job.job_key or job.name
        entry = {
            "outputs": job.outputs or {},
            "result": job.conclusion or "",
        }
        previous = context.get(key)
        if previous is not None:
            # Instances of one matrix job share a key. Its result is the
            # worst of theirs; its outputs are whichever set them.
            entry = {
                "outputs": {**previous["outputs"], **entry["outputs"]},
                "result": _combined_result(previous["result"], entry["result"]),
            }
        context[key] = entry
        aliases.pop(key, None)
        bare_key = key.rsplit(" / ", 1)[-1]
        # A prefixed key wins over a bare alias if both somehow exist.
        if bare_key != key and (bare_key not in context or aliases.get(bare_key) == key):
            aliases[bare_key] = key
            context[bare_key] = entry
    return context


def _combined_result(first: str, second: str) -> str:
    for result in ("failure", "cancelled", "success", "skipped"):
        if result in (first, second):
            return result
    return first or second


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

    # Organization variables the repository can use, its own over them.
    from app.services.org_variables import vars_context

    variables = await vars_context(db, workflow.repo_id)
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
    # GitHub defaults cancel-in-progress to false: a group serialises runs
    # unless the workflow explicitly asks for supersede. Defaulting it to true
    # cancelled runs nobody asked to cancel.
    cancel_in_progress = False
    if isinstance(concurrency, dict):
        cancel_in_progress = concurrency.get("cancel-in-progress", False) is True
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
        dynamic_matrix = dynamic_matrix_expression(job_def)
        if dynamic_matrix is not None and not job_def.get("needs"):
            # Nothing to wait for: the matrix reads only the event and the
            # call's inputs, so it can be expanded now.
            value = evaluate_expression_value(
                dynamic_matrix, _job_context(expression_context, job_def),
            )
            job_def = {**job_def, "strategy": {**job_def["strategy"], "matrix": value}}
            dynamic_matrix = None
            # An empty or malformed matrix fails the job on GitHub; it must
            # not fall back to a single instance with no matrix.
            unexpandable = not matrix_combinations(value)
        else:
            unexpandable = False
        expanded = expand_matrix(job_def)
        for job_config in expanded:
            job_expression_context = _job_context(expression_context, job_config)
            display_name = render_expressions(job_config.get("_display_name", job_config.get("name", job_config["key"])), job_expression_context)
            needs = job_config.get("needs", [])
            initial_status = "queued" if not needs else "waiting"
            should_run = evaluate_job_if(job_config.get("if"), job_expression_context)

            steps_data = build_steps_data(
                job_config.get("steps", []),
                job_config.get("env") or {},
                job_expression_context,
            )

            # A job with dependencies cannot be evaluated yet: its condition and
            # steps may read needs.<job>.outputs.*, which only exist once those
            # jobs finish. Hold the unrendered form and resolve it at promotion.
            pending_render = None
            if needs:
                pending_render = {
                    "if": job_config.get("if"),
                    "steps": job_config.get("steps", []),
                    "env": job_config.get("env") or {},
                    "matrix": job_config.get("_matrix", {}),
                    "strategy": job_expression_context["strategy"],
                    "_call_inputs": job_config.get("_call_inputs") or {},
                    "_call_secrets": job_config.get("_call_secrets") or {},
                    "_workflow_repository": job_config.get("_workflow_repository") or "",
                    "_workflow_sha": job_config.get("_workflow_sha") or "",
                }
                if dynamic_matrix is not None:
                    # Expanded at promotion, once needs.*.outputs exist. The
                    # name and concurrency group read matrix.*, so they are
                    # rendered per instance then too.
                    pending_render["dynamic_matrix"] = dynamic_matrix
                    pending_render["name"] = job_config.get("name") or job_config["key"]
                    pending_render["concurrency"] = job_config.get("concurrency")
                    pending_render["strategy_options"] = {
                        k: v for k, v in (job_config.get("strategy") or {}).items()
                        if k != "matrix"
                    }
                should_run = True

            conclusion = None
            completed_at = None
            # The job's condition is decided before its matrix, as on
            # GitHub: a skipped job is skipped, whatever its matrix.
            if unexpandable and should_run:
                initial_status = "completed"
                conclusion = "failure"
                completed_at = datetime.now(timezone.utc)
                skipped_job_created = True
                steps_data = [
                    {**step, "status": "completed", "conclusion": "skipped"}
                    for step in steps_data
                ]
            elif not should_run:
                initial_status = "completed"
                conclusion = "skipped"
                completed_at = datetime.now(timezone.utc)
                skipped_job_created = True
                steps_data = [
                    {**step, "status": "completed", "conclusion": "skipped"}
                    for step in steps_data
                ]

            # Job-level concurrency group, recorded here and acted on when the
            # job becomes eligible to run. Cancelling at creation is wrong: a
            # stage job declares `needs: route`, so its `if:` cannot be
            # evaluated yet, and a job that is about to be skipped would
            # supersede the one actually doing the work. Every follow-on event
            # from an agent's own comments creates such a job, so the effect
            # was a run cancelling itself — caught by the conformance check.
            job_group, job_cancel = _job_concurrency(job_config, job_expression_context)
            # Recorded only when it supersedes. A group with
            # cancel-in-progress: false queues a job behind its predecessor on
            # GitHub; this emulator has no queue-behind state, so storing the
            # group would claim a serialisation it does not perform.
            job_group = job_group if job_cancel else None
            if dynamic_matrix is not None:
                job_group = None

            job = WorkflowJob(
                run_id=run.id,
                concurrency_group=job_group,
                name=display_name,
                workflow_name=workflow.name,
                status=initial_status,
                conclusion=conclusion,
                completed_at=completed_at,
                steps=steps_data,
                # runs-on may read matrix.*; a job with needs renders its
                # labels at promotion, where its matrix is known.
                labels=(
                    job_config.get("runs_on", ["ubuntu-latest"]) if needs
                    else render_job_labels(job_config.get("runs_on", ["ubuntu-latest"]), job_expression_context)
                ),
                run_attempt=1,
                needs=needs,
                permissions=job_config.get("permissions") or {},
                job_key=job_config.get("key"),
                outputs_config=job_config.get("outputs") or {},
                outputs={},
                pending_render=pending_render,
                workflow_ref=job_config.get("_workflow_ref") or None,
            )
            db.add(job)

    await db.flush()
    # A run containing only skipped jobs should finish immediately. Also
    # propagate skipped dependencies before the first runner poll.
    if skipped_job_created:
        await dispatch_ready_jobs(db, run.id)
        await check_run_completion(db, run.id)
    return run


def _job_concurrency(job_config: dict, context: dict) -> tuple[str | None, bool]:
    """Return a job's rendered concurrency group and whether it supersedes.

    Same shape as workflow-level concurrency: a bare string is a group name,
    a mapping carries `group` and `cancel-in-progress`. GitHub defaults
    cancel-in-progress to false here too.
    """
    concurrency = job_config.get("concurrency")
    if not concurrency:
        return None, False
    cancel = False
    if isinstance(concurrency, dict):
        cancel = concurrency.get("cancel-in-progress", False) is True
        concurrency = concurrency.get("group")
    if not concurrency:
        return None, False
    return str(render_expressions(concurrency, context)), cancel


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
    # An organization's repository is owned by the organization, not by the
    # member (or App) that created it, which is what ``Repository.owner`` is.
    organization = repository.__dict__.get("organization") if repository.owner_type == "Organization" else None
    if organization is not None:
        owner = {
            "login": organization.login,
            "id": organization.id,
            "node_id": f"O_{organization.id}",
            "type": "Organization",
            "site_admin": False,
        }
    elif repository.owner_type == "Organization":
        owner = {"login": repository.full_name.split("/", 1)[0], "type": "Organization", "site_admin": False}
    else:
        owner = _user_payload(repository.owner)
    return {
        "id": repository.id,
        "node_id": f"R_{repository.id}",
        "name": repository.name,
        "full_name": repository.full_name,
        "private": bool(repository.private),
        "default_branch": repository.default_branch,
        "description": repository.description,
        "fork": bool(repository.fork),
        "owner": owner,
        "html_url": f"{settings.BASE_URL}/{repository.full_name}",
        "url": f"{settings.BASE_URL}/api/v3/repos/{repository.full_name}",
        "clone_url": f"{settings.BASE_URL}/{repository.full_name}.git",
    }


def _milestone_payload(milestone, repository: Repository) -> dict:
    return {
        "id": milestone.id,
        "node_id": f"MI_{milestone.id}",
        "number": milestone.number,
        "title": milestone.title,
        "description": getattr(milestone, "description", None),
        "state": getattr(milestone, "state", "open"),
        "html_url": f"{settings.BASE_URL}/{repository.full_name}/milestone/{milestone.number}",
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
    # A fork's pull request names the fork as its head repository; workflows
    # tell a fork apart by comparing head.repo.full_name with base's.
    head_repository = repository
    if pr.head_repo_id is not None and pr.head_repo_id != repository.id:
        # Loaded with the pull request (selectin). Should it not be, the
        # head is reported as an unnamed other repository: a fork, never
        # mistaken for the base.
        head_repository = pr.__dict__.get("head_repository")
    base = {
        "ref": pr.base_ref,
        "sha": pr.base_sha,
        "label": f"{repository.full_name.split('/', 1)[0]}:{pr.base_ref}",
        "repo": _repository_payload(repository),
    }
    head = {
        "ref": pr.head_ref,
        "sha": pr.head_sha,
        "label": f"{head_repository.full_name.split('/', 1)[0]}:{pr.head_ref}" if head_repository else pr.head_ref,
        "repo": _repository_payload(head_repository) if head_repository else {"id": pr.head_repo_id, "full_name": "", "fork": True},
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
    changes: dict | None = None,
    assignee=None,
    milestone=None,
) -> dict:
    """Build the GitHub-like payload shared by REST-triggered activities."""
    payload = {
        "action": action,
        "repository": _repository_payload(repository),
        "sender": _user_payload(actor),
    }
    if changes:
        # GitHub's shape for an edit: the previous value of each changed
        # field, so a consumer can tell a body edit from anything else.
        payload["changes"] = changes
    if assignee is not None:
        payload["assignee"] = _user_payload(assignee)
    if milestone is not None:
        payload["milestone"] = _milestone_payload(milestone, repository)
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
    """Dispatch one successful repository activity to matching workflows,
    and to the repository's webhooks."""
    # Webhooks receive every activity, whether or not Actions can run for it.
    try:
        await schedule_webhooks(db, repository.id, repository.organization_id, event, payload)
    except Exception:
        logger.exception("Scheduling %s webhooks for %s failed", event, repository.full_name)
    if not repository.disk_path or not actor:
        return []
    if repository.actions_enabled is False:
        # Actions is off for this repository (GitHub's per-repository
        # setting): the activity is recorded, no run is created.
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
        "repository": _repository_payload(repository),
        "pusher": {"name": user.login, "email": user.email or ""},
        "sender": _user_payload(user),
    }
    if created or deleted:
        # GitHub sends a create or delete webhook for a branch a push adds or
        # removes, besides the push itself.
        ref_event = {
            "ref": head_branch,
            "ref_type": "branch",
            "pusher_type": "user",
            "repository": _repository_payload(repository),
            "sender": _user_payload(user),
        }
        if created:
            ref_event["master_branch"] = repository.default_branch or "main"
            ref_event["description"] = repository.description
        await schedule_webhooks(
            db, repository.id, repository.organization_id,
            "create" if created else "delete", ref_event,
        )
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
    #
    # Only an *open* pull request synchronizes. Without the state filter a push
    # to a branch that any historical pull request was once opened from raises
    # synchronize activity for every one of them, including merged and closed
    # ones. A repository whose default branch has ever been a pull request head
    # then dispatches a stale run on every push to it.
    result = await db.execute(
        select(PullRequest)
        .join(Issue, PullRequest.issue_id == Issue.id)
        .where(
            PullRequest.repo_id == repository.id,
            PullRequest.head_ref == head_branch,
            same_repository_head(repository.id),
            Issue.state == "open",
            PullRequest.merged.is_(False),
        )
    )
    for pr in result.scalars().all():
        issue = pr.issue
        # GitHub fires synchronize for a head that *changed* after the pull
        # request existed. A pull request opened after this push was received
        # but before it was processed already records the pushed commit as
        # its head, and dispatching for it ran a second review of one pull
        # request (2026-09-27, runs 1604 and 1609).
        if pr.head_sha == head_sha:
            continue
        pr.head_sha = head_sha
        # ``base_sha`` is the base commit recorded when the pull request was
        # opened. GitHub runs pull_request_target against the base branch as it
        # is *now*, which is the whole point of the event: it runs the base
        # branch's own workflow code. Using the stored value stamps the run
        # with a commit the branch may have moved far past, and a checkout of
        # it then asks the git transport for an object no ref points at.
        base_sha = await get_ref_sha(repository.disk_path, pr.base_ref) or pr.base_sha
        pr_payload = build_activity_payload(
            repository, user, "synchronize", issue=issue,
            pull_request=pr, ref=f"refs/heads/{pr.base_ref}", sha=base_sha,
        )
        runs.extend(await dispatch_event(
            db, repository, user, "pull_request_target", "synchronize",
            pr_payload, ref=pr.base_ref, sha=base_sha,
        ))
    runs.extend(await _synchronize_fork_pull_requests(db, repository, user, head_branch, head_sha))
    return runs


async def _synchronize_fork_pull_requests(
    db: AsyncSession, fork: Repository, user: User, branch: str, head_sha: str,
) -> list[WorkflowRun]:
    """A push to a fork's branch moves every open pull request that branch
    heads in another repository of the network: the base repository's
    refs/pull/N/head follows it, and the base repository raises
    pull_request_target ``synchronize``, as GitHub does."""
    result = await db.execute(
        select(PullRequest)
        .join(Issue, PullRequest.issue_id == Issue.id)
        .where(
            PullRequest.head_repo_id == fork.id,
            PullRequest.repo_id != fork.id,
            PullRequest.head_ref == branch,
            Issue.state == "open",
            PullRequest.merged.is_(False),
        )
    )
    runs: list[WorkflowRun] = []
    for pr in result.scalars().all():
        base_repository = (
            await db.execute(select(Repository).where(Repository.id == pr.repo_id))
        ).scalar_one_or_none()
        if base_repository is None:
            continue
        issue = pr.issue
        synced = await sync_head_ref(base_repository, fork, branch, issue.number)
        new_head = synced or head_sha
        if pr.head_sha == new_head:
            continue
        pr.head_sha = new_head
        pr.last_push_by_id = user.id
        # Recorded now: dispatch_event commits only when it starts a run, and
        # a git push's caller may not commit after this.
        await db.commit()
        base_sha = await get_ref_sha(base_repository.disk_path, pr.base_ref) or pr.base_sha
        pr_payload = build_activity_payload(
            base_repository, user, "synchronize", issue=issue,
            pull_request=pr, ref=f"refs/heads/{pr.base_ref}", sha=base_sha,
        )
        runs.extend(await dispatch_event(
            db, base_repository, user, "pull_request_target", "synchronize",
            pr_payload, ref=pr.base_ref, sha=base_sha,
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
    """Resolve a branch/ref in a bare repository to a commit SHA.

    A bare name is looked up as a branch first. ``git rev-parse main`` is
    ambiguous when a tag shares the name, and resolving a branch event to a
    tag's commit is not a failure anything downstream would report.
    """
    if not ref.startswith(("refs/", "HEAD")):
        branch = await _resolve_ref_sha(repo_disk_path, f"refs/heads/{ref}^{{commit}}")
        if branch:
            return branch
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", f"{ref}^{{commit}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"GIT_DIR": repo_disk_path},
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip() if proc.returncode == 0 else ""


from app.services.workflow_scheduler import (
    cancel_workflow_run,
    check_run_completion,
    dispatch_ready_jobs,
)

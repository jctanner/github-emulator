#!/usr/bin/env python3
"""Lightweight GitHub Actions runner for the GitHub Emulator.

Registers with the emulator, polls for jobs, executes local shell `run:` steps,
and reports results back. Requires only httpx + stdlib.

Environment variables:
  GITHUB_EMULATOR_URL   - Base URL of the emulator (e.g. https://ghemu.local)
  GITHUB_EMULATOR_TOKEN - Admin PAT for initial registration
  RUNNER_REPO           - Repository to poll (e.g. admin/test-repo)
  RUNNER_NAME           - Runner name (default: hostname)
  RUNNER_LABELS         - Comma-separated labels (default: self-hosted,linux)
  RUNNER_WORKDIR        - Working directory for job execution (default: /tmp/runner-work)
"""

import logging
import json
import os
import platform
import re
import shutil
import selectors
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("runner")

EMULATOR_URL = os.environ.get("GITHUB_EMULATOR_URL", "https://localhost")
ADMIN_TOKEN = os.environ.get("GITHUB_EMULATOR_TOKEN", "")
REPO = os.environ.get("RUNNER_REPO", "admin/test-repo")
RUNNER_SCOPE = os.environ.get("RUNNER_SCOPE", "repository").strip().lower()
RUNNER_NAME = os.environ.get("RUNNER_NAME", platform.node())
LABELS = os.environ.get("RUNNER_LABELS", "self-hosted,linux").split(",")
WORKDIR = os.environ.get("RUNNER_WORKDIR", "/tmp/runner-work")
API = f"{EMULATOR_URL}/api/v3"

# GitHub keeps the per-job temp directory beside the workspace, not inside it.
# That placement is load-bearing: a checkout with no ``path:`` initialises a
# git repository at the workspace root, and a source build unpacked inside it
# would land in that repository's working tree. Mirror the layout rather than
# the convenience.
RUNNER_TEMP = os.environ.get("RUNNER_TEMP_DIR") or str(
    Path(WORKDIR).parent / "_temp"
)

# GitHub reports the architecture as X64/ARM64, not as uname does.
_RUNNER_ARCH_BY_MACHINE = {
    "x86_64": "X64",
    "amd64": "X64",
    "aarch64": "ARM64",
    "arm64": "ARM64",
}


def _runner_arch() -> str:
    machine = platform.machine().lower()
    return _RUNNER_ARCH_BY_MACHINE.get(machine, machine.upper())

# GitHub's own request URL always carries a query string, because the
# mint-token action appends "&audience=..." to it rather than "?audience=...".
OIDC_TOKEN_URL = f"{EMULATOR_URL}/actions/oidc/token?api-version=2.0"

# Application-default credentials for the Google auth shim below.
GCP_CREDENTIALS_FILE = os.environ.get(
    "FULLSEND_DEV_GCP_CREDENTIALS_FILE", "/var/run/secrets/gcp/credentials.json"
)

# Third-party actions this runner emulates locally, keyed by "owner/repo".
#
# There is no network path to github.com here and no JavaScript action
# runtime, so a marketplace action cannot be fetched or executed. Refusing one
# by name is the honest default and stays the default. The exception is an
# action that only prepares an environment, where the local stack can
# reproduce the *effect* downstream steps depend on. Those are listed here, by
# name, so the list of things this runner pretends to be stays short and
# visible. The version is deliberately not matched: a version change should
# show up in the log, not turn into a sudden "unsupported action".
_ACTION_SHIMS = {
    "google-github-actions/auth": "_shim_google_auth",
    "actions/setup-go": "_shim_setup_go",
}

# The Go toolchain baked into the runner image. The setup-go emulation uses it
# when it satisfies the requested version instead of downloading one per job.
GO_ROOT = os.environ.get("FULLSEND_DEV_GO_ROOT", "/usr/local/go")
# Where a toolchain is fetched from when the pinned one will not do.
GO_DOWNLOAD_URL = os.environ.get(
    "FULLSEND_DEV_GO_DOWNLOAD_URL", "https://go.dev/dl"
)
# Go gained automatic toolchain switching in 1.21: running a build in a module
# that asks for a newer toolchain makes Go fetch that toolchain itself.
_GO_TOOLCHAIN_SWITCHING_FROM = (1, 21)


def _emulator_host() -> str:
    """Return the emulator's bare hostname for tools that expect a host."""
    from urllib.parse import urlparse

    return urlparse(EMULATOR_URL).hostname or "github.local"


_EXPRESSION_RE = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")


# Context roots the runner resolves. The server renders every other context
# before a step reaches here, so an unresolved root means something upstream
# did not run. The parser raises on one rather than quietly yielding empty,
# which leaves the expression visible as literal text instead of turning a
# missing renderer into a silently wrong value.
_RUNNER_CONTEXT_ROOTS = ("steps", "inputs", "github", "runner")


def _lookup_context_path(context: dict, path: str):
    """Resolve a dotted context path.

    A missing key yields empty, matching Actions, where reading an absent
    property is null rather than an error. An unknown *root* raises, because
    that means a whole context the runner does not implement, and rendering it
    empty would turn a missing renderer into a silently wrong value.
    """
    parts = path.split(".")
    if parts[0] not in _RUNNER_CONTEXT_ROOTS:
        raise ValueError(f"unsupported context {parts[0]!r}")
    value: object = context
    for part in parts:
        if not isinstance(value, dict):
            return ""
        value = value.get(part, "")
    return value


def _runner_context(
    inputs: dict | None,
    step_outputs: dict[str, dict[str, str]] | None,
    github_token: str = "",
) -> dict:
    """Build the context the runner can resolve expressions against.

    ``steps`` is nested under an explicit ``outputs`` key so a dotted path
    reads the same way it is written in a workflow.
    """
    return {
        "steps": {
            str(key): {"outputs": dict(value or {})}
            for key, value in (step_outputs or {}).items()
        },
        "inputs": {str(k): v for k, v in (inputs or {}).items()},
        "github": {"token": github_token},
        # The runner context describes this machine, so only the runner can
        # resolve it. The server leaves it alone for that reason, which is why
        # a composite action's ``${{ runner.temp }}`` arrives here unrendered.
        "runner": {
            "os": platform.system(),
            "arch": _runner_arch(),
            "name": RUNNER_NAME,
            "temp": RUNNER_TEMP,
        },
    }


class _StepIfParser:
    """Evaluate the small Actions expression subset needed by the runner."""

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
                raise ValueError(f"unsupported character at {position}")
            position = match.end()
            if match.lastgroup != "space":
                tokens.append((match.lastgroup, match.group(0)))
        tokens.append(("eof", ""))
        return tokens

    def _peek(self, value: str | None = None):
        token = self.tokens[self.position]
        return token[1] == value if value is not None else token

    def _take(self, value: str | None = None):
        token = self.tokens[self.position]
        if value is not None and token[1] != value:
            raise ValueError(f"expected {value!r}")
        self.position += 1
        return token

    def parse(self) -> bool:
        return bool(self.evaluate())

    def evaluate(self):
        """Evaluate to the underlying value rather than a boolean.

        Rendering needs the value: ``a || b`` in a step's ``env:`` has to
        produce the winning string, not True.
        """
        result = self._parse_or()
        if self._peek()[0] != "eof":
            raise ValueError("unexpected trailing expression")
        return result

    def _parse_or(self):
        result = self._parse_and()
        while self._peek("||"):
            self._take("||")
            # Parse before combining. Writing ``result or self._parse_and()``
            # lets Python short-circuit, which skips *consuming* the right-hand
            # tokens and leaves the parser reporting a trailing expression.
            right = self._parse_and()
            result = result or right
        return result

    def _parse_and(self):
        result = self._parse_not()
        while self._peek("&&"):
            self._take("&&")
            right = self._parse_not()
            result = result and right
        return result

    def _parse_not(self):
        if self._peek("!"):
            self._take("!")
            return not bool(self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self):
        left = self._parse_primary()
        if self._peek("==") or self._peek("!="):
            operator = self._take()[1]
            right = self._parse_primary()
            equal = left == right
            return equal if operator == "==" else not equal
        return left

    def _parse_primary(self):
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
            raise ValueError("expected value")
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
        return _lookup_context_path(self.context, token)

    def _call(self, name, arguments):
        if name == "always" and not arguments:
            return True
        if name == "hashFiles":
            return _hash_files([str(a) for a in arguments])
        if name == "format" and arguments:
            return _format_expression(arguments[0], arguments[1:])
        if name == "startsWith" and len(arguments) == 2:
            return str(arguments[0] or "").startswith(str(arguments[1] or ""))
        if name == "endsWith" and len(arguments) == 2:
            return str(arguments[0] or "").endswith(str(arguments[1] or ""))
        if name == "contains" and len(arguments) == 2:
            haystack, needle = arguments
            return needle in haystack if isinstance(haystack, (list, dict, str)) else False
        raise ValueError(f"unsupported function {name}")


class StepConditionError(ValueError):
    """Raised when a step condition cannot be evaluated at all."""


def _format_expression(template: object, values: list) -> str:
    """Implement Actions' ``format``: ``{0}`` placeholders, ``{{`` escapes.

    Python's ``str.format`` is not a substitute. It would honour attribute and
    index access inside a placeholder, which Actions does not, and reaching
    those from workflow text is not something a runner should offer.
    """
    text = "" if template is None else str(template)
    out = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in "{}" and text[index:index + 2] == char * 2:
            out.append(char)
            index += 2
            continue
        if char != "{":
            out.append(char)
            index += 1
            continue
        end = text.find("}", index)
        if end == -1:
            raise ValueError("unterminated placeholder in format()")
        digits = text[index + 1:end]
        if not digits.isdigit():
            raise ValueError(f"format() placeholder {digits!r} is not a number")
        position = int(digits)
        if position >= len(values):
            raise ValueError(f"format() has no argument {position}")
        value = values[position]
        out.append("" if value is None else str(value))
        index = end + 1
    return "".join(out)


def _hash_files(patterns: list[str]) -> str:
    """Implement hashFiles: a digest over matched files, empty if none match.

    Evaluated on the runner because it reads the job workspace, which the
    server cannot see. GitHub returns an empty string when no file matches,
    which is how workflows test "these files are absent".
    """
    import hashlib

    digest = hashlib.sha256()
    matched = False
    root = Path(WORKDIR)
    for pattern in patterns:
        # An absolute pattern reaches here whenever a workflow builds one, as
        # the agent action does with format('{0}/go.mod', inputs.target-repo).
        # Path.glob refuses those outright, and the resulting exception used to
        # escape the whole job. Anchor it instead: GitHub evaluates hashFiles
        # against the workspace, so a path outside it matches nothing.
        candidate = Path(pattern)
        if candidate.is_absolute():
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                continue
            search_root, search = root, str(relative)
        else:
            search_root, search = root, pattern
        for path in sorted(search_root.glob(search)):
            if path.is_file():
                matched = True
                digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest() if matched else ""


def _evaluate_step_if(condition: object, step_outputs: dict[str, dict[str, str]]) -> bool:
    """Return whether a runner step should execute."""
    if condition is None:
        return True
    if isinstance(condition, bool):
        return condition
    if not isinstance(condition, str):
        return bool(condition)
    expression = condition.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2].strip()
    try:
        return _StepIfParser(
            expression, _runner_context(None, step_outputs)
        ).parse()
    except ValueError as exc:
        # Treating an unevaluable condition as false silently drops the step,
        # which is indistinguishable from a deliberate skip. Raise so the step
        # fails and names the condition instead.
        raise StepConditionError(
            f"could not evaluate step condition {condition!r}: {exc}"
        ) from exc


# An expression needs the real parser once it contains an operator, a string
# literal, or a call. A plain dotted path keeps the cheaper lookup below.
_NEEDS_PARSER_RE = re.compile(r"\|\||&&|==|!=|\(|'|\"")


def _render_local_action(value, inputs, step_outputs, github_token=""):
    """Render the expressions the runner resolves at execution time.

    ``github.token`` is resolved here rather than on the server so the
    credential is never written into the persisted step records; the job token
    arrives with the job and lives only for the length of the run.

    Anything with an operator goes through the parser. Handling only plain
    paths meant a fallback chain such as
    ``steps.detect.outputs.source-ref || steps.detect.outputs.version-url``
    was returned as its own text and reached the shell verbatim, where git
    reported it as an invalid refspec. An expression the parser cannot handle
    is still left as literal text rather than rendered empty, so an
    unsupported context stays visible.
    """
    if isinstance(value, str):
        def replace(match):
            expression = match.group(1).strip()
            if expression == "github.token":
                return github_token
            if _NEEDS_PARSER_RE.search(expression):
                context = _runner_context(inputs, step_outputs, github_token)
                try:
                    resolved = _StepIfParser(expression, context).evaluate()
                except ValueError as exc:
                    log.warning(
                        "Could not render expression %r: %s", expression, exc
                    )
                    return match.group(0)
                if resolved is None or resolved is False:
                    return ""
                if resolved is True:
                    return "true"
                if isinstance(resolved, (dict, list)):
                    return json.dumps(resolved, separators=(",", ":"))
                return str(resolved)
            try:
                resolved = _lookup_context_path(
                    _runner_context(inputs, step_outputs, github_token),
                    expression,
                )
            except ValueError:
                # An unknown root: leave it visible rather than render empty.
                return match.group(0)
            if isinstance(resolved, (dict, list)):
                return json.dumps(resolved, separators=(",", ":"))
            return "" if resolved is None else str(resolved)
        return _EXPRESSION_RE.sub(replace, value)
    if isinstance(value, dict):
        return {
            key: _render_local_action(item, inputs, step_outputs, github_token)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _render_local_action(item, inputs, step_outputs, github_token)
            for item in value
        ]
    return value


def _parse_go_version(text: str) -> tuple[int, ...]:
    """Turn "1.26.5" or "go1.26.5" into a comparable tuple."""
    cleaned = text.strip().removeprefix("go").split("-", 1)[0]
    parts = []
    for piece in cleaned.split("."):
        digits = "".join(c for c in piece if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _requested_go_version(inputs: dict, workspace: Path) -> tuple[str, str]:
    """Return the version setup-go was asked for and where that came from.

    ``go-version`` wins over ``go-version-file``, as in the real action. A
    ``go.mod`` or ``go.work`` names its version on a ``toolchain`` line when it
    has one and a ``go`` line otherwise; any other file holds the bare version,
    which is how ``.go-version`` works.
    """
    explicit = str(inputs.get("go-version", "")).strip()
    if explicit:
        return explicit, "go-version"

    version_file = str(inputs.get("go-version-file", "")).strip()
    if not version_file:
        return "", ""
    path = Path(version_file)
    if not path.is_absolute():
        path = workspace / version_file
    if not path.is_file():
        raise FileNotFoundError(f"go-version-file not found: {path}")

    text = path.read_text()
    if path.name in ("go.mod", "go.work"):
        toolchain = ""
        declared = ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("toolchain "):
                toolchain = line.split(None, 1)[1].strip()
            elif line.startswith("go ") and not declared:
                declared = line.split(None, 1)[1].strip()
        chosen = toolchain or declared
        if not chosen:
            raise ValueError(f"no go or toolchain directive in {path}")
        return chosen.removeprefix("go"), str(path)
    return text.strip(), str(path)


class RunnerClient:
    def __init__(self):
        self.runner_id = None
        self.runner_token = None
        self.client = httpx.Client(verify=False, timeout=60.0)
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None
        self._masks: set[str] = set()

    def register(self):
        """Register this runner with the emulator."""
        if RUNNER_SCOPE == "site":
            log.info("Registering site-wide runner '%s' with labels %s ...", RUNNER_NAME, LABELS)
            resp = self.client.post(
                f"{API}/admin/actions/runners/register",
                headers={"Authorization": f"token {ADMIN_TOKEN}"},
                json={"name": RUNNER_NAME, "labels": LABELS, "os": "linux"},
            )
            resp.raise_for_status()
            data = resp.json()
            self.runner_id = data["runner_id"]
            self.runner_token = data["token"]
            log.info("Registered as site-wide runner #%d", self.runner_id)
            return

        log.info("Requesting registration token for %s ...", REPO)
        resp = self.client.post(
            f"{API}/repos/{REPO}/actions/runners/registration-token",
            headers={"Authorization": f"token {ADMIN_TOKEN}"},
        )
        resp.raise_for_status()
        reg_token = resp.json()["token"]

        log.info("Registering runner '%s' with labels %s ...", RUNNER_NAME, LABELS)
        resp = self.client.post(
            f"{API}/actions/runner/register",
            json={
                "token": reg_token,
                "name": RUNNER_NAME,
                "labels": LABELS,
                "os": "linux",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self.runner_id = data["runner_id"]
        self.runner_token = data["token"]
        log.info("Registered as runner #%d", self.runner_id)

    def _auth_headers(self):
        return {"Authorization": f"Bearer {self.runner_token}"}

    def start_heartbeat(self):
        """Start a background heartbeat thread."""
        def heartbeat_loop():
            while not self._heartbeat_stop.wait(30):
                try:
                    self.client.post(
                        f"{API}/actions/runner/heartbeat",
                        headers=self._auth_headers(),
                    )
                except Exception:
                    log.warning("Heartbeat failed")

        self._heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def poll_for_job(self):
        """Long-poll for an available job. Returns job dict or None."""
        try:
            jobs_url = (
                f"{API}/actions/runner/jobs"
                if RUNNER_SCOPE == "site"
                else f"{API}/repos/{REPO}/actions/runner/jobs"
            )
            resp = self.client.get(
                jobs_url,
                params={"labels": ",".join(LABELS), "timeout": "30"},
                headers=self._auth_headers(),
                timeout=45.0,
            )
            if resp.status_code == 204:
                return None
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            return None
        except httpx.HTTPStatusError as e:
            log.error("Poll error: %s", e)
            return None

    def execute_job(self, job: dict):
        """Execute a job's steps and report results."""
        job_id = job["job_id"]
        job_repository = str(job.get("repository") or REPO)
        log.info("=== Executing job #%d: %s ===", job_id, job.get("name", ""))

        os.makedirs(WORKDIR, exist_ok=True)
        # GitHub hands every job an empty RUNNER_TEMP. Reusing one across jobs
        # would let a build cache or a credential file outlive the job that
        # created it.
        shutil.rmtree(RUNNER_TEMP, ignore_errors=True)
        os.makedirs(RUNNER_TEMP, exist_ok=True)
        steps = job.get("steps", [])
        runtime_env = {}
        step_outputs: dict[str, dict[str, str]] = {}
        # The scoped credential the emulator issues for this job, used to
        # resolve ${{ github.token }} in steps.
        job_token = str(job.get("token") or "")
        all_passed = True

        def append_log(chunk: str) -> None:
            if chunk:
                self._upload_logs(job_repository, job_id, chunk)

        append_log(f"Job {job_id}: {job.get('name', '')}\n")

        for step in steps:
            step_num = step.get("number", 0)
            step_name = step.get("name", f"Step {step_num}")
            log.info("  Step %d: %s", step_num, step_name)
            append_log(f"\n##[group]Step {step_num}: {step_name}\n")

            try:
                should_run = _evaluate_step_if(step.get("if"), step_outputs)
            except StepConditionError as exc:
                step["status"] = "completed"
                step["conclusion"] = "failure"
                append_log(f"{exc}\n")
                append_log("##[endgroup]\n")
                all_passed = False
                self._report_progress(job_repository, job_id, steps)
                break
            if not should_run:
                step["status"] = "completed"
                step["conclusion"] = "skipped"
                append_log("Skipped because its condition evaluated to false\n")
                append_log("##[endgroup]\n")
                self._report_progress(job_repository, job_id, steps)
                continue

            step["status"] = "in_progress"
            self._report_progress(job_repository, job_id, steps)

            rendered_step = _render_local_action(step, {}, step_outputs, job_token)
            result, _step_log, step_updates = self._run_step(
                rendered_step, job, runtime_env, log_callback=append_log,
            )
            runtime_env.update(step_updates)
            if rendered_step.get("id"):
                outputs = dict(rendered_step.get("outputs") or {})
                step_outputs[str(rendered_step["id"])] = outputs
                step["outputs"] = outputs
            step["status"] = "completed"
            step["conclusion"] = result
            append_log("##[endgroup]\n")

            if result != "success":
                all_passed = False
                log.error("  Step %d FAILED", step_num)
                # Mark remaining steps as skipped
                for remaining in steps:
                    if remaining.get("status") == "queued":
                        remaining["status"] = "completed"
                        remaining["conclusion"] = "skipped"
                break
            else:
                log.info("  Step %d passed", step_num)

            self._report_progress(job_repository, job_id, steps)

        conclusion = "success" if all_passed else "failure"
        self._complete_job(job_repository, job_id, conclusion, steps, step_outputs)
        log.info("=== Job #%d finished: %s ===", job_id, conclusion)

        # Cleanup workdir
        try:
            shutil.rmtree(WORKDIR, ignore_errors=True)
            shutil.rmtree(RUNNER_TEMP, ignore_errors=True)
        except Exception:
            pass

    def _execute_job_guarded(self, job: dict) -> None:
        """Run a job, and fail it out loud if execution raises.

        An exception escaping ``execute_job`` used to be caught by the poll
        loop, logged as a poll-loop error, and then forgotten. The job stayed
        ``in_progress`` for ever: no conclusion, no log line in the run, and a
        workflow that simply never finished. A crash has to end the job, and
        the reason has to reach the run's own log, or the failure is invisible
        to everyone looking at the right place for it.
        """
        try:
            self.execute_job(job)
        except Exception as exc:  # noqa: BLE001 - the point is to catch everything
            job_id = job.get("job_id")
            repository = str(job.get("repository") or REPO)
            log.exception("Job #%s crashed", job_id)
            detail = f"{type(exc).__name__}: {exc}"
            try:
                self._upload_logs(
                    repository,
                    job_id,
                    f"\nThe runner crashed while executing this job.\n{detail}\n",
                )
            except Exception:  # noqa: BLE001 - reporting must not mask the crash
                log.exception("Could not upload the crash log for job #%s", job_id)
            try:
                self._complete_job(repository, job_id, "failure", job.get("steps") or [])
            except Exception:  # noqa: BLE001
                log.exception("Could not fail job #%s after a crash", job_id)

    def _run_step(
        self, step: dict, job: dict, runtime_env: dict[str, str],
        log_callback=None, action_path: str = "",
    ) -> tuple[str, str, dict[str, str]]:
        """Execute a single local shell step.

        ``action_path`` is the directory of the composite action this step
        belongs to, which GitHub exposes as ``GITHUB_ACTION_PATH`` so a step
        can find scripts shipped alongside its own action. It is empty for a
        step written directly in a workflow, where GitHub does not set it.
        """
        step_name = step.get("name", "")
        command = step.get("run")
        if not command:
            if step.get("uses", "").startswith("actions/checkout@"):
                result, output, updates = self._checkout_step(step, job)
                if log_callback:
                    log_callback(output)
                return result, output, updates
            if step.get("uses", "").startswith("./"):
                return self._composite_step(
                    step, job, runtime_env, log_callback,
                    github_token=str(job.get("token") or ""),
                )
            uses = step.get("uses", "")
            shim = _ACTION_SHIMS.get(uses.split("@", 1)[0])
            if shim:
                result, output, updates = getattr(self, shim)(step, uses)
                if log_callback:
                    log_callback(output)
                return result, output, updates
            if uses:
                # Reporting an unrun action as success manufactures false
                # passes: a skipped credential-minting or agent step looks
                # identical to one that worked. Fail loudly instead, and name
                # what this runner can execute.
                output = (
                    f"Unsupported action: {uses}\n"
                    "This runner executes 'run:' steps, actions/checkout@*, "
                    "local composite actions ('uses: ./path'), and these "
                    f"locally emulated actions: {', '.join(sorted(_ACTION_SHIMS))}. "
                    "Other actions are not fetched or executed.\n"
                )
            else:
                output = f"Step has neither 'run' nor 'uses': {step_name}\n"
            if log_callback:
                log_callback(output)
            return "failure", output, {}

        env = os.environ.copy()
        # Provide the small set of standard GitHub Actions variables needed by
        # shell-only smoke jobs and by the first Fullsend integration layer.
        # This runner is intentionally not a full actions/runner replacement.
        env.update({
            "CI": "true",
            "GITHUB_ACTIONS": "true",
            "GITHUB_API_URL": API,
            "GITHUB_REPOSITORY": str(job.get("repository") or REPO),
            "GITHUB_SERVER_URL": EMULATOR_URL,
            "GITHUB_WORKSPACE": WORKDIR,
            "GITHUB_RUN_ID": str(job.get("run_id", "")),
            "GITHUB_RUN_NUMBER": str(job.get("run_number", "")),
            "GITHUB_EVENT_NAME": str(job.get("event", "workflow_dispatch")),
            "GITHUB_REF": str(job.get("event_payload", {}).get("ref", "")),
            "GITHUB_SHA": str(job.get("head_sha", "")),
            "GITHUB_ACTOR": str(
                job.get("event_payload", {}).get("sender", {}).get("login", "")
            ),
            "GITHUB_REPOSITORY_OWNER": str(job.get("repository") or REPO).split("/", 1)[0],
            "GITHUB_REF_NAME": str(
                job.get("event_payload", {}).get("ref", "")
            ).removeprefix("refs/heads/").removeprefix("refs/tags/"),
            # The gh CLI does not read GITHUB_API_URL; without GH_HOST it
            # targets api.github.com and every call fails with "Bad
            # credentials" no matter how valid the job token is.
            "GH_HOST": _emulator_host(),
            "RUNNER_NAME": RUNNER_NAME,
            "RUNNER_OS": platform.system(),
            "RUNNER_ARCH": _runner_arch(),
            "RUNNER_TEMP": RUNNER_TEMP,
        })
        if action_path:
            env["GITHUB_ACTION_PATH"] = action_path
        else:
            # A workflow-level step has no action directory, and inheriting the
            # previous one would point a script at the wrong tree.
            env.pop("GITHUB_ACTION_PATH", None)

        # Actions OIDC. On GitHub these two variables are present only for a
        # job that declared "id-token: write", and the request token is scoped
        # to that job. Both properties matter here: a static shared secret in
        # the pod environment would let any step of any job mint a token for
        # any repository, which is exactly the check the mint is supposed to
        # perform. Clear whatever the pod environment carries when the job did
        # not ask, so an unprivileged job cannot inherit one.
        permissions = job.get("permissions")
        may_mint = not isinstance(permissions, dict) or (
            permissions.get("id-token") == "write"
        )
        job_oidc_token = str(job.get("token") or "")
        if may_mint and job_oidc_token:
            env["ACTIONS_ID_TOKEN_REQUEST_URL"] = OIDC_TOKEN_URL
            env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"] = job_oidc_token
        else:
            env.pop("ACTIONS_ID_TOKEN_REQUEST_URL", None)
            env.pop("ACTIONS_ID_TOKEN_REQUEST_TOKEN", None)
        env.update({str(key): str(value) for key, value in runtime_env.items()})
        if ADMIN_TOKEN:
            env.setdefault("GITHUB_TOKEN", ADMIN_TOKEN)
            env.setdefault("GH_TOKEN", ADMIN_TOKEN)
        for key, value in (job.get("env") or {}).items():
            env[str(key)] = str(value)
        for key, value in (step.get("env") or {}).items():
            env[str(key)] = str(value)

        # gh treats any host other than github.com as GitHub Enterprise and
        # reads its credential from GH_ENTERPRISE_TOKEN, ignoring GH_TOKEN.
        # Mirror whichever token the workflow chose so gh authenticates to the
        # emulator with it rather than reporting "Requires authentication".
        workflow_token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN") or ""
        if workflow_token:
            env.setdefault("GH_ENTERPRISE_TOKEN", workflow_token)
            env.setdefault("GITHUB_ENTERPRISE_TOKEN", workflow_token)

        step_dir = Path(WORKDIR) / ".runner-state"
        step_dir.mkdir(parents=True, exist_ok=True)
        env_file = step_dir / f"env-{step.get('number', 0)}"
        output_file = step_dir / f"output-{step.get('number', 0)}"
        path_file = step_dir / f"path-{step.get('number', 0)}"
        env_file.write_text("")
        output_file.write_text("")
        path_file.write_text("")
        event_file = step_dir / "event.json"
        event_file.write_text(json.dumps(job.get("event_payload") or {}))
        env["GITHUB_ENV"] = str(env_file)
        env["GITHUB_OUTPUT"] = str(output_file)
        env["GITHUB_PATH"] = str(path_file)
        env["GITHUB_EVENT_PATH"] = str(event_file)
        if step_name == "Mint token via OIDC":
            log.info(
                "OIDC action environment: url=%s request_token_len=%d",
                env.get("ACTIONS_ID_TOKEN_REQUEST_URL", "(not granted)"),
                len(env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")),
            )

        cwd = WORKDIR
        working_directory = step.get("working-directory")
        if working_directory:
            cwd = os.path.abspath(os.path.join(WORKDIR, working_directory))
            workdir_root = os.path.abspath(WORKDIR)
            if cwd != workdir_root and not cwd.startswith(workdir_root + os.sep):
                return "failure", "working-directory escapes runner workdir\n"
        os.makedirs(cwd, exist_ok=True)

        log.info("    Executing shell command for step: %s", step_name)
        captured: list[str] = []

        def emit(output: str) -> None:
            if not output:
                return
            for line in output.splitlines(keepends=True):
                if line.startswith("::add-mask::"):
                    value = line.removeprefix("::add-mask::").rstrip("\r\n")
                    if value:
                        self._masks.add(value)
            for value in sorted(self._masks, key=len, reverse=True):
                output = output.replace(value, "***")
            captured.append(output)
            if log_callback:
                log_callback(output)

        proc = None
        selector = selectors.DefaultSelector()
        timed_out = False
        try:
            shell_name = str(step.get("shell") or "bash")
            executable = "/bin/bash" if shell_name.split()[0] == "bash" else None
            proc = subprocess.Popen(
                command,
                shell=True,
                executable=executable,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            assert proc.stdout is not None
            selector.register(proc.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + int(job.get("timeout_seconds") or 3600)
            pending = b""
            while True:
                if time.monotonic() >= deadline and proc.poll() is None:
                    timed_out = True
                    proc.kill()

                events = selector.select(timeout=0.25)
                if events:
                    chunk = os.read(proc.stdout.fileno(), 65536)
                    if chunk:
                        pending += chunk
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            emit((line + b"\n").decode(errors="replace"))
                    else:
                        selector.unregister(proc.stdout)
                        break
                elif proc.poll() is not None:
                    break

            if pending:
                emit(pending.decode(errors="replace"))
            return_code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or b""
            if isinstance(output, bytes):
                output = output.decode(errors="replace")
            emit(output)
            timed_out = True
            return_code = 1
        except Exception as exc:
            output = f"Command failed to start: {exc}\n"
            emit(output)
            return "failure", "".join(captured), {}
        finally:
            selector.close()

        if timed_out:
            emit("Command timed out\n")
        updates = self._read_command_file(env_file)
        # GITHUB_PATH is a behaviour, not a value: every line a step writes is
        # prepended to PATH for the steps that follow. It is how each of
        # Fullsend's install paths puts its binary where the next step can run
        # it, so leaving it unset makes an install look like it worked and the
        # next step report "command not found".
        added = [
            line.strip()
            for line in path_file.read_text().splitlines()
            if line.strip()
        ]
        if added:
            updates["PATH"] = os.pathsep.join([*added, env.get("PATH", "")])
        step["outputs"] = self._read_command_file(output_file)
        return ("success" if return_code == 0 and not timed_out else "failure"), "".join(captured), updates

    def _installed_go(self) -> tuple[str, Path] | tuple[str, None]:
        """Return the version and bin directory of the image's Go, if any."""
        candidate = Path(GO_ROOT) / "bin" / "go"
        if not candidate.is_file():
            return "", None
        try:
            result = subprocess.run(
                [str(candidate), "version"],
                capture_output=True, text=True, timeout=30, check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return "", None
        # "go version go1.26.5 linux/amd64"
        for word in result.stdout.split():
            if word.startswith("go1"):
                return word.removeprefix("go"), candidate.parent
        return "", None

    def _download_go(self, version: str) -> tuple[Path | None, str]:
        """Fetch a toolchain into RUNNER_TEMP. Returns (bin dir, message)."""
        arch = {"X64": "amd64", "ARM64": "arm64"}.get(_runner_arch(), "")
        if not arch:
            return None, f"no Go build published for {_runner_arch()}"
        name = f"go{version}.linux-{arch}.tar.gz"
        url = f"{GO_DOWNLOAD_URL}/{name}"
        destination = Path(RUNNER_TEMP) / f"go-{version}"
        destination.mkdir(parents=True, exist_ok=True)
        archive = Path(RUNNER_TEMP) / name
        try:
            with self.client.stream("GET", url, follow_redirects=True) as response:
                response.raise_for_status()
                with archive.open("wb") as handle:
                    for chunk in response.iter_bytes(1 << 20):
                        handle.write(chunk)
            shutil.unpack_archive(str(archive), str(destination))
        except Exception as exc:  # network, archive, disk
            return None, f"could not download {url}: {exc}"
        finally:
            archive.unlink(missing_ok=True)
        binary = destination / "go" / "bin"
        if not (binary / "go").is_file():
            return None, f"{url} did not contain go/bin/go"
        return binary, f"downloaded {name}"

    def _shim_setup_go(
        self, step: dict, uses: str
    ) -> tuple[str, str, dict[str, str]]:
        """Emulate actions/setup-go against the image's pinned toolchain.

        Unlike the Google auth emulation, this one reproduces what the real
        action does rather than substituting for something unreachable: resolve
        a version, make that toolchain available, and put it on PATH. Hosted
        runners answer from a preinstalled tool cache first and download only on
        a miss, and this does the same with the toolchain baked into the image.

        A request the pinned toolchain cannot satisfy is not failed outright.
        Go 1.21 and later fetch the toolchain a module asks for during the
        build, so a newer request is delegated to Go itself and said out loud.
        The download path is the fallback for a Go too old to switch, and its
        failure is reported rather than swallowed.
        """
        inputs = {str(key): str(value) for key, value in (step.get("with") or {}).items()}
        lines = [f"Emulating {uses} locally.\n"]

        try:
            requested, source = _requested_go_version(inputs, Path(WORKDIR))
        except (FileNotFoundError, ValueError) as exc:
            lines.append(f"{exc}\n")
            return "failure", "".join(lines), {}

        installed, bin_dir = self._installed_go()
        if installed:
            lines.append(f"  image toolchain: go{installed} at {GO_ROOT}\n")
        else:
            lines.append(f"  image toolchain: none at {GO_ROOT}\n")
        lines.append(
            f"  requested: {requested or '(unspecified)'}"
            f"{f' (from {source})' if source else ''}\n"
        )

        def _use(directory: Path, version: str, note: str):
            lines.append(f"  using: go{version} from {directory} ({note})\n")
            step["outputs"] = {"go-version": version, "cache-hit": "false"}
            return "success", "".join(lines), {
                "PATH": os.pathsep.join([str(directory), os.environ.get("PATH", "")]),
                "GOROOT": str(Path(directory).parent),
            }

        if not installed:
            if not requested:
                lines.append(
                    "No Go is installed in this image and the step named no "
                    "version, so there is nothing to select.\n"
                )
                return "failure", "".join(lines), {}
            downloaded, message = self._download_go(requested)
            if downloaded is None:
                lines.append(f"{message}\n")
                return "failure", "".join(lines), {}
            return _use(downloaded, requested, message)

        if not requested:
            return _use(bin_dir, installed, "no version requested")

        wanted = _parse_go_version(requested)
        have = _parse_go_version(installed)
        if have >= wanted:
            return _use(bin_dir, installed, "satisfies the request")

        # The image's Go is older than the request. Say so either way: a silent
        # auto-upgrade is how a run ends up certifying a toolchain nobody chose.
        if have >= _GO_TOOLCHAIN_SWITCHING_FROM:
            lines.append(
                f"  go{installed} is older than the requested {requested}. Go "
                "fetches the toolchain a module asks for during the build, so "
                "the build will run on the requested version rather than this "
                "one.\n"
            )
            return _use(bin_dir, installed, "toolchain switching will upgrade it")

        downloaded, message = self._download_go(requested)
        if downloaded is None:
            lines.append(
                f"  go{installed} is too old to switch toolchains and {message}.\n"
            )
            return "failure", "".join(lines), {}
        return _use(downloaded, requested, message)

    def _shim_google_auth(
        self, step: dict, uses: str
    ) -> tuple[str, str, dict[str, str]]:
        """Emulate google-github-actions/auth with locally mounted credentials.

        The real action trades a GitHub OIDC token for Google credentials
        through Workload Identity Federation. There is no Google security token
        service reachable from this stack and no federation trust configured
        against the local issuer, so that exchange cannot be reproduced and
        pretending otherwise would be a lie in the log.

        What the steps after it actually depend on is narrower: an
        application-default credentials file on disk, and the environment
        variables that point at it. Fullsend's own
        ``prepare-sandbox-credentials.sh`` documents both shapes it handles and
        no-ops for any credential that is not ``external_account``, so a mounted
        credentials file is a mode Fullsend already supports rather than
        something invented here.

        Missing credentials fail the step. Exporting nothing and reporting
        success would let a later step fail somewhere far away, which is the
        failure mode this runner exists to avoid.
        """
        inputs = {str(key): str(value) for key, value in (step.get("with") or {}).items()}
        path = Path(GCP_CREDENTIALS_FILE)
        lines = [
            f"Emulating {uses} locally.\n",
            "Workload Identity Federation is not reachable from this stack, so "
            "the mounted credentials file is used instead.\n",
        ]

        if not path.is_file():
            lines.append(
                f"No credentials file at {path}.\n"
                "Mount one into the runner, or point "
                "FULLSEND_DEV_GCP_CREDENTIALS_FILE at a different path.\n"
            )
            return "failure", "".join(lines), {}

        try:
            credentials = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            lines.append(f"Could not read {path}: {exc}\n")
            return "failure", "".join(lines), {}

        credential_type = str(credentials.get("type", "")) or "unknown"
        # An external_account file points at a credential source URL that this
        # stack cannot reach, so say so here rather than letting the sandbox
        # credential script fail on a URL fetch.
        if credential_type == "external_account":
            lines.append(
                f"The credentials file at {path} is an external_account "
                "(federated) config, whose credential source is not reachable "
                "from here. Mount a service-account key or authorized-user "
                "credential instead.\n"
            )
            return "failure", "".join(lines), {}

        # The action's own project_id input wins. An authorized-user credential
        # carries no project_id, so fall back to its quota project, which is
        # what gcloud itself uses for such a credential.
        project = (
            inputs.get("project_id", "").strip()
            or str(credentials.get("project_id", "") or "")
            or str(credentials.get("quota_project_id", "") or "")
        )

        updates = {
            "GOOGLE_APPLICATION_CREDENTIALS": str(path),
            # The composite action that wraps this one masks these two by name.
            "GOOGLE_GHA_CREDS_PATH": str(path),
            "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(path),
        }
        if project:
            updates["GOOGLE_CLOUD_PROJECT"] = project
            updates["GCLOUD_PROJECT"] = project
            updates["CLOUDSDK_CORE_PROJECT"] = project

        self._masks.add(str(path))
        step["outputs"] = {
            "credentials_file_path": str(path),
            "project_id": project,
        }
        lines.append(f"  credentials: {path} (type: {credential_type})\n")
        lines.append(f"  project: {project or '(none resolved)'}\n")
        if not project:
            lines.append(
                "  No project id was supplied and the credential carries "
                "none, so GOOGLE_CLOUD_PROJECT is unset.\n"
            )
        return "success", "".join(lines), updates

    def _composite_step(
        self, step: dict, job: dict, runtime_env: dict[str, str], log_callback=None,
        github_token: str = "",
    ) -> tuple[str, str, dict[str, str]]:
        """Run a checked-out local composite action used by Fullsend."""
        action_ref = str(step.get("uses", ""))
        action_path = Path(WORKDIR) / action_ref.removeprefix("./")
        action_file = next((action_path / name for name in ("action.yml", "action.yaml") if (action_path / name).is_file()), None)
        if action_file is None:
            return "failure", f"Local action metadata not found: {action_ref}\n", {}
        try:
            definition = yaml.safe_load(action_file.read_text()) or {}
            action_steps = definition["runs"]["steps"]
        except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
            return "failure", f"Could not load local action {action_ref}: {exc}\n", {}

        inputs = {str(key): str(value) for key, value in (step.get("with") or {}).items()}
        step_outputs: dict[str, dict[str, str]] = {}
        log_lines = [f"Running local composite action {action_ref}\n"]
        if log_callback:
            log_callback(log_lines[0])
        for index, action_step in enumerate(action_steps, start=1):
            try:
                action_should_run = _evaluate_step_if(action_step.get("if"), step_outputs)
            except StepConditionError as exc:
                # The caller discards the returned output when it is streaming
                # through log_callback, so a condition error raised inside a
                # composite action was failing the job with an empty log.
                message = f"{exc}\n"
                if log_callback:
                    log_callback(message)
                log_lines.append(message)
                return "failure", "".join(log_lines), {}
            if not action_should_run:
                log_lines.append(
                    f"Skipping composite step {action_step.get('name', f'Step {index}')} "
                    "because its condition evaluated to false\n"
                )
                continue
            rendered = _render_local_action(action_step, inputs, step_outputs, github_token)
            rendered.setdefault("number", index)
            result, output, updates = self._run_step(
                rendered, job, runtime_env, log_callback=log_callback,
                action_path=str(action_path),
            )
            runtime_env.update(updates)
            if not log_callback:
                log_lines.append(output)
            if rendered.get("id"):
                step_outputs[str(rendered["id"])] = dict(rendered.get("outputs") or {})
            if result != "success":
                return result, "".join(log_lines), {}

        outputs = {}
        for name, output_def in (definition.get("outputs") or {}).items():
            value = output_def.get("value", "") if isinstance(output_def, dict) else str(output_def)
            outputs[str(name)] = _render_local_action(value, inputs, step_outputs, github_token)
        step["outputs"] = outputs
        return "success", "".join(log_lines), {}

    @staticmethod
    def _read_command_file(path: Path) -> dict[str, str]:
        values: dict[str, str] = {}
        if not path.is_file():
            return values
        for line in path.read_text(errors="replace").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        return values

    def _checkout_step(self, step: dict, job: dict) -> tuple[str, str, dict[str, str]]:
        """Checkout a repository using the emulator's Git smart HTTP endpoint."""
        options = step.get("with") or {}
        repository = str(options.get("repository") or job.get("repository") or REPO)
        ref = str(options.get("ref") or job.get("head_branch") or "main")
        ref = ref.removeprefix("refs/heads/")
        target = Path(WORKDIR) / str(options.get("path") or "")
        target.mkdir(parents=True, exist_ok=True)
        remote = f"{EMULATOR_URL}/{repository}.git"
        askpass = Path(WORKDIR) / ".git-askpass"
        askpass.write_text("#!/bin/sh\ncase \"$1\" in *Username*) echo x-access-token;; *) echo \"$GITHUB_TOKEN\";; esac\n")
        askpass.chmod(0o700)
        env = os.environ.copy()
        env.update({"GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0"})
        if ADMIN_TOKEN:
            env["GITHUB_TOKEN"] = ADMIN_TOKEN
        # An explicit token: takes precedence, so a step that checks out with a
        # minted, scoped credential uses that credential rather than the
        # runner's ambient admin token.
        explicit_token = str(options.get("token") or "").strip()
        if explicit_token:
            env["GITHUB_TOKEN"] = explicit_token
        try:
            depth = int(str(options.get("fetch-depth", 1)).strip() or 1)
        except ValueError:
            depth = 1
        sparse = [
            line.strip()
            for line in str(options.get("sparse-checkout") or "").splitlines()
            if line.strip()
        ]
        try:
            subprocess.run(
                ["git", "init", "-b", "main"], cwd=target, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
            )
            subprocess.run(
                ["git", "remote", "remove", "origin"], cwd=target, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
            )
            subprocess.run(
                ["git", "remote", "add", "origin", remote], cwd=target, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
            )
            fetch_command = ["git", "fetch"]
            # fetch-depth: 0 means full history on GitHub.
            if depth > 0:
                fetch_command += ["--depth", str(depth)]
            fetch_command += ["origin", ref]
            fetched = subprocess.run(
                fetch_command, cwd=target,
                env=env, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=True,
            )
            if sparse:
                subprocess.run(
                    ["git", "sparse-checkout", "init", "--cone"], cwd=target, env=env,
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    check=True,
                )
                subprocess.run(
                    ["git", "sparse-checkout", "set", *sparse], cwd=target, env=env,
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    check=True,
                )
            subprocess.run(
                ["git", "checkout", "-B", ref, "FETCH_HEAD"], cwd=target,
                env=env, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=True,
            )
            if str(options.get("persist-credentials", "true")).lower() == "false":
                # GitHub removes the credential helper from the checkout when
                # asked; leaving it behind would let later steps reuse a token
                # the workflow deliberately dropped.
                subprocess.run(
                    ["git", "config", "--unset-all", "http.extraheader"], cwd=target,
                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False,
                )
            detail = f" (sparse: {', '.join(sparse)})" if sparse else ""
            return "success", f"Checked out {repository}@{ref}{detail}\n{fetched.stdout}", {}
        except subprocess.CalledProcessError as exc:
            return "failure", f"Checkout failed: {exc.stdout or exc}\n", {}

    def _report_progress(self, repository: str, job_id: int, steps: list):
        try:
            self.client.patch(
                f"{API}/repos/{repository}/actions/runner/jobs/{job_id}",
                json={"steps": steps},
                headers=self._auth_headers(),
            )
        except Exception:
            log.warning("Failed to report progress for job %d", job_id)

    def _complete_job(
        self, repository: str, job_id: int, conclusion: str, steps: list,
        step_outputs: dict[str, dict[str, str]] | None = None,
    ):
        try:
            self.client.post(
                f"{API}/repos/{repository}/actions/runner/jobs/{job_id}/complete",
                # step_outputs lets the server resolve this job's declared
                # outputs, which dependent jobs read as needs.<job>.outputs.*.
                json={
                    "conclusion": conclusion,
                    "steps": steps,
                    "step_outputs": step_outputs or {},
                },
                headers=self._auth_headers(),
            )
        except Exception as e:
            log.error("Failed to report completion for job %d: %s", job_id, e)

    def _upload_logs(self, repository: str, job_id: int, log_data: str):
        try:
            self.client.post(
                f"{API}/repos/{repository}/actions/runner/jobs/{job_id}/logs",
                content=log_data.encode(),
                headers={**self._auth_headers(), "Content-Type": "text/plain"},
            )
        except Exception:
            pass

    def run(self):
        """Main loop: register, then poll and execute jobs forever."""
        if not ADMIN_TOKEN:
            log.error(
                "GITHUB_EMULATOR_TOKEN is required. Run the compose bootstrap "
                "helper or set GITHUB_EMULATOR_RUNNER_TOKEN in .env."
            )
            return
        if RUNNER_SCOPE not in {"repository", "site"}:
            log.error("RUNNER_SCOPE must be repository or site, got %r", RUNNER_SCOPE)
            return
        if RUNNER_SCOPE == "repository" and (not REPO or "/" not in REPO):
            log.error("RUNNER_REPO must be set as owner/repo, got %r", REPO)
            return

        while True:
            try:
                self.register()
                break
            except Exception as e:
                log.error("Registration failed: %s -- retrying in 10s", e)
                time.sleep(10)

        self.start_heartbeat()
        target = "all repositories" if RUNNER_SCOPE == "site" else REPO
        log.info("Runner ready. Polling for jobs on %s ...", target)

        while True:
            try:
                job = self.poll_for_job()
                if job:
                    self._execute_job_guarded(job)
                else:
                    log.debug("No jobs available, polling again...")
            except KeyboardInterrupt:
                log.info("Shutting down")
                break
            except Exception as e:
                log.error("Error in poll loop: %s", e)
                time.sleep(5)


if __name__ == "__main__":
    runner = RunnerClient()
    runner.run()

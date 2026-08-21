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
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
RUNNER_NAME = os.environ.get("RUNNER_NAME", platform.node())
LABELS = os.environ.get("RUNNER_LABELS", "self-hosted,linux").split(",")
WORKDIR = os.environ.get("RUNNER_WORKDIR", "/tmp/runner-work")
OIDC_PORT = int(os.environ.get("ACTIONS_ID_TOKEN_REQUEST_PORT", "8765"))

API = f"{EMULATOR_URL}/api/v3"


class _OIDCHandler(BaseHTTPRequestHandler):
    """Development-only local OIDC broker for composite Fullsend actions."""

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self):  # noqa: N802
        expected = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
        supplied = self.headers.get("Authorization", "")
        value = os.environ.get("FULLSEND_DEV_OIDC_TOKEN", "")
        scheme, _, token = supplied.partition(" ")
        if not expected or scheme.lower() != "bearer" or token != expected or not value:
            self.send_response(401)
            self.end_headers()
            return
        payload = json.dumps({"value": value}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _start_oidc_broker():
    try:
        server = ThreadingHTTPServer(("127.0.0.1", OIDC_PORT), _OIDCHandler)
    except OSError as exc:
        log.warning("Development OIDC broker unavailable: %s", exc)
        return None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


_EXPRESSION_RE = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")


def _render_local_action(value, inputs, step_outputs):
    if isinstance(value, str):
        def replace(match):
            expression = match.group(1).strip()
            if expression.startswith("inputs."):
                return str(inputs.get(expression.removeprefix("inputs."), ""))
            if expression.startswith("steps."):
                parts = expression.split(".")
                if len(parts) == 4 and parts[2] == "outputs":
                    return str(step_outputs.get(parts[1], {}).get(parts[3], ""))
            return match.group(0)
        return _EXPRESSION_RE.sub(replace, value)
    if isinstance(value, dict):
        return {key: _render_local_action(item, inputs, step_outputs) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_local_action(item, inputs, step_outputs) for item in value]
    return value


class RunnerClient:
    def __init__(self):
        self.runner_id = None
        self.runner_token = None
        self.client = httpx.Client(verify=False, timeout=60.0)
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None
        self._masks: set[str] = set()
        self._oidc_broker = _start_oidc_broker()

    def register(self):
        """Register this runner with the emulator."""
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
            resp = self.client.get(
                f"{API}/repos/{REPO}/actions/runner/jobs",
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
        log.info("=== Executing job #%d: %s ===", job_id, job.get("name", ""))

        os.makedirs(WORKDIR, exist_ok=True)
        steps = job.get("steps", [])
        runtime_env = {}
        step_outputs: dict[str, dict[str, str]] = {}
        all_passed = True
        log_lines = [f"Job {job_id}: {job.get('name', '')}\n"]

        for step in steps:
            step_num = step.get("number", 0)
            step_name = step.get("name", f"Step {step_num}")
            log.info("  Step %d: %s", step_num, step_name)
            log_lines.append(f"\n##[group]Step {step_num}: {step_name}\n")

            step["status"] = "in_progress"
            self._report_progress(job_id, steps)

            step = _render_local_action(step, {}, step_outputs)
            result, step_log, step_updates = self._run_step(step, job, runtime_env)
            runtime_env.update(step_updates)
            if step.get("id"):
                step_outputs[str(step["id"])] = dict(step.get("outputs") or {})
            log_lines.append(step_log)
            step["status"] = "completed"
            step["conclusion"] = result
            log_lines.append(f"##[endgroup]\n")

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

            self._report_progress(job_id, steps)

        conclusion = "success" if all_passed else "failure"
        self._upload_logs(job_id, "".join(log_lines))
        self._complete_job(job_id, conclusion, steps)
        log.info("=== Job #%d finished: %s ===", job_id, conclusion)

        # Cleanup workdir
        try:
            shutil.rmtree(WORKDIR, ignore_errors=True)
        except Exception:
            pass

    def _run_step(
        self, step: dict, job: dict, runtime_env: dict[str, str],
    ) -> tuple[str, str, dict[str, str]]:
        """Execute a single local shell step."""
        step_name = step.get("name", "")
        command = step.get("run")
        if not command:
            if step.get("uses", "").startswith("actions/checkout@"):
                return self._checkout_step(step, job)
            if step.get("uses", "").startswith("./"):
                return self._composite_step(step, job, runtime_env)
            return "success", f"Skipping non-shell step: {step_name}\n", {}

        env = os.environ.copy()
        # Provide the small set of standard GitHub Actions variables needed by
        # shell-only smoke jobs and by the first Fullsend integration layer.
        # This runner is intentionally not a full actions/runner replacement.
        env.update({
            "CI": "true",
            "GITHUB_ACTIONS": "true",
            "GITHUB_API_URL": API,
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_SERVER_URL": EMULATOR_URL,
            "GITHUB_WORKSPACE": WORKDIR,
            "GITHUB_RUN_ID": str(job.get("run_id", "")),
            "GITHUB_RUN_NUMBER": str(job.get("run_number", "")),
            "GITHUB_EVENT_NAME": str(job.get("event", "workflow_dispatch")),
            "GITHUB_REF": str(job.get("event_payload", {}).get("ref", "")),
            "GITHUB_SHA": str(job.get("head_sha", "")),
            "RUNNER_NAME": RUNNER_NAME,
            "RUNNER_OS": platform.system(),
            "ACTIONS_ID_TOKEN_REQUEST_URL": f"http://127.0.0.1:{OIDC_PORT}/oidc",
        })
        env.update({str(key): str(value) for key, value in runtime_env.items()})
        if ADMIN_TOKEN:
            env.setdefault("GITHUB_TOKEN", ADMIN_TOKEN)
            env.setdefault("GH_TOKEN", ADMIN_TOKEN)
        for key, value in (job.get("env") or {}).items():
            env[str(key)] = str(value)
        for key, value in (step.get("env") or {}).items():
            env[str(key)] = str(value)

        step_dir = Path(WORKDIR) / ".runner-state"
        step_dir.mkdir(parents=True, exist_ok=True)
        env_file = step_dir / f"env-{step.get('number', 0)}"
        output_file = step_dir / f"output-{step.get('number', 0)}"
        env_file.write_text("")
        output_file.write_text("")
        event_file = step_dir / "event.json"
        event_file.write_text(json.dumps(job.get("event_payload") or {}))
        env["GITHUB_ENV"] = str(env_file)
        env["GITHUB_OUTPUT"] = str(output_file)
        env["GITHUB_EVENT_PATH"] = str(event_file)
        if step_name == "Mint token via OIDC":
            log.info(
                "OIDC action environment: url=%s request_token_len=%d dev_token_len=%d",
                env.get("ACTIONS_ID_TOKEN_REQUEST_URL", ""),
                len(env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")),
                len(env.get("FULLSEND_DEV_OIDC_TOKEN", "")),
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
        try:
            shell_name = str(step.get("shell") or "bash")
            executable = "/bin/bash" if shell_name.split()[0] == "bash" else None
            proc = subprocess.run(
                command,
                shell=True,
                executable=executable,
                cwd=cwd,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=int(job.get("timeout_seconds") or 3600),
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ""
            return "failure", f"{output}\nCommand timed out\n", {}
        except Exception as exc:
            return "failure", f"Command failed to start: {exc}\n", {}

        output = proc.stdout or ""
        for line in output.splitlines():
            if line.startswith("::add-mask::"):
                value = line.removeprefix("::add-mask::")
                if value:
                    self._masks.add(value)
        for value in sorted(self._masks, key=len, reverse=True):
            output = output.replace(value, "***")
        updates = self._read_command_file(env_file)
        step["outputs"] = self._read_command_file(output_file)
        return ("success" if proc.returncode == 0 else "failure"), output, updates

    def _composite_step(
        self, step: dict, job: dict, runtime_env: dict[str, str],
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
        for index, action_step in enumerate(action_steps, start=1):
            rendered = _render_local_action(action_step, inputs, step_outputs)
            rendered.setdefault("number", index)
            result, output, updates = self._run_step(rendered, job, runtime_env)
            runtime_env.update(updates)
            log_lines.append(output)
            if rendered.get("id"):
                step_outputs[str(rendered["id"])] = dict(rendered.get("outputs") or {})
            if result != "success":
                return result, "".join(log_lines), {}

        outputs = {}
        for name, output_def in (definition.get("outputs") or {}).items():
            value = output_def.get("value", "") if isinstance(output_def, dict) else str(output_def)
            outputs[str(name)] = _render_local_action(value, inputs, step_outputs)
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
        repository = str(options.get("repository") or REPO)
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
            fetched = subprocess.run(
                ["git", "fetch", "--depth", "1", "origin", ref], cwd=target,
                env=env, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=True,
            )
            subprocess.run(
                ["git", "checkout", "-B", ref, "FETCH_HEAD"], cwd=target,
                env=env, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=True,
            )
            return "success", f"Checked out {repository}@{ref}\n{fetched.stdout}", {}
        except subprocess.CalledProcessError as exc:
            return "failure", f"Checkout failed: {exc.stdout or exc}\n", {}

    def _report_progress(self, job_id: int, steps: list):
        try:
            self.client.patch(
                f"{API}/repos/{REPO}/actions/runner/jobs/{job_id}",
                json={"steps": steps},
                headers=self._auth_headers(),
            )
        except Exception:
            log.warning("Failed to report progress for job %d", job_id)

    def _complete_job(self, job_id: int, conclusion: str, steps: list):
        try:
            self.client.post(
                f"{API}/repos/{REPO}/actions/runner/jobs/{job_id}/complete",
                json={"conclusion": conclusion, "steps": steps},
                headers=self._auth_headers(),
            )
        except Exception as e:
            log.error("Failed to report completion for job %d: %s", job_id, e)

    def _upload_logs(self, job_id: int, log_data: str):
        try:
            self.client.post(
                f"{API}/repos/{REPO}/actions/runner/jobs/{job_id}/logs",
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
        if not REPO or "/" not in REPO:
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
        log.info("Runner ready. Polling for jobs on %s ...", REPO)

        while True:
            try:
                job = self.poll_for_job()
                if job:
                    self.execute_job(job)
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

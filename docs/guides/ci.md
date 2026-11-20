# Use it in CI

This guide starts the emulator and its Actions runners inside another
repository's CI job, on the job's own Linux VM. It needs no containers and no
secrets. You get:

- the emulator as a plain process, on SQLite, over http or optional https;
- one or more runners: the Python runner, or the upstream `actions/runner`;
- an optional TLS setup with a CA made for each run;
- a site admin token made for each stack, never the published default.

Two scripts do the work: [`scripts/ci-stack.sh`](../../scripts/ci-stack.sh)
starts and stops the stack, and
[`scripts/ci-workflow-smoke.sh`](../../scripts/ci-workflow-smoke.sh) checks
that it executes a real workflow run.
[`.github/workflows/self-contained-smoke.yml`](../../.github/workflows/self-contained-smoke.yml)
runs both on every pull request. From a workflow in another repository, run
these scripts from a checkout of this repository, as below, or call the
composite action
[`.github/actions/emulator`](../../.github/actions/emulator/action.yml), which
wraps `ci-stack.sh` for the common case.

## Prerequisites

- Linux x64. `scripts/ci-stack.sh stats` reads `/proc`, and the upstream
  runner is the Linux x64 build.
- Python 3.11 or later, and `uv` to export the locked dependencies.
- `curl`, `git`, `jq` and `openssl`.
- `sudo` only for `CI_STACK_SYSTEM_TRUST=1` and `CI_STACK_LOCALHOST_DNS=1`.

## Start the stack and run a workflow

Run these from a checkout of this repository.

1. Install the emulator's locked dependencies into a virtual environment:

   ```bash
   python3 -m venv /tmp/emu-venv
   uv export -q --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/emu-requirements.txt
   /tmp/emu-venv/bin/pip install -q --require-hashes -r /tmp/emu-requirements.txt
   ```

2. Start the emulator and one Python runner. The script returns once the API
   answers:

   ```console
   $ export CI_STACK_PYTHON=/tmp/emu-venv/bin/python CI_STACK_DIR=/tmp/ci-stack
   $ scripts/ci-stack.sh start
   emulator http://127.0.0.1:8000 (pid 995), 1 python runner(s)
   admin token in /tmp/ci-stack/admin-token
   ```

3. Run a workflow on it. The smoke script creates an org and a repository,
   pushes a two-job workflow over git, dispatches it and waits for the run:

   ```console
   $ scripts/ci-workflow-smoke.sh
   ...
   run 1: success
     job produce: success
     job consume: success
   PASS: dispatch input crossed a job output into the consume job's log
   ```

   It exits 0 only when the run concludes `success` and the `consume` job's
   log holds the dispatched input. Against another URL, set `EMU`, for example
   `EMU=https://ghemu.localhost:443`. With `CI_STACK_TLS=1`, also set
   `EMU_CA=$CI_STACK_DIR/tls/ca.pem`.

4. Optional: print each process's peak memory and CPU seconds:

   ```console
   $ scripts/ci-stack.sh stats
   emulator peak_rss_mib=181 cpu_s=3.6
   runner-1 peak_rss_mib=41 cpu_s=0.4
   all runner processes now rss_mib=2
   ```

5. Stop the stack. In a workflow, run this step with `if: always()`:

   ```bash
   scripts/ci-stack.sh stop
   ```

Logs are in `$CI_STACK_DIR/emulator.log` and `$CI_STACK_DIR/runner-<n>.log`.

## The admin token

`scripts/ci-stack.sh start` writes the site admin's token to
`$CI_STACK_DIR/admin-token` (mode 600) and prints that path. Your own clients
read it from there:

```bash
TOKEN="$(cat "$CI_STACK_DIR/admin-token")"
```

If you set `GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN` before `start`, the stack
uses your value instead. Otherwise it makes one token for each state
directory, so a restart on the same `CI_STACK_DIR` keeps the same token.
`scripts/ci-workflow-smoke.sh` reads the token the same way, so give it the
same `CI_STACK_DIR`.

## Settings

All settings are optional environment variables for `scripts/ci-stack.sh`.

| Variable | Default | What it sets |
|---|---|---|
| `CI_STACK_DIR` | `.ci-stack` in the checkout | State directory: SQLite file, git repositories, logs, pid files, `admin-token` and `admin-password`. |
| `CI_STACK_PORT` | `8000` | API port. |
| `CI_STACK_PYTHON` | `python3` | Python with the locked dependencies installed. |
| `CI_STACK_RUNNERS` | `1` | Runner processes, that is, jobs that run at once. |
| `CI_STACK_RUNNER_KIND` | `python` | `python` or `upstream` (the real `actions/runner`, Linux x64). |
| `CI_STACK_RUNNER_LABELS` | `self-hosted,linux,ubuntu-latest,ubuntu-24.04` | Labels the runners serve. |
| `CI_STACK_RUNNER_ENV` | | Extra environment for every runner and its jobs: `NAME=VALUE` lines, one per line. |
| `CI_STACK_HOST` | `127.0.0.1` | Host name the emulator puts in its URLs. |
| `CI_STACK_BIND` | `127.0.0.1` (`0.0.0.0` with `CI_STACK_LOCALHOST_DNS=1`) | Address the API listens on. Use `0.0.0.0` with a non-loopback `CI_STACK_HOST`. |
| `CI_STACK_TLS` | | `1` to serve https with a CA and certificate made for this run under `$CI_STACK_DIR/tls`. Clients trust `tls/ca.pem`; `tls/bundle.pem` adds the public roots. |
| `CI_STACK_SYSTEM_TRUST` | | `1` to add that CA to the system store with `sudo` (Debian/Ubuntu). `actions/runner` reads only that store. |
| `CI_STACK_LOCALHOST_DNS` | | `1` to make a `CI_STACK_HOST` ending in `.localhost` resolve to this VM's own address for every client. Linux only, uses `sudo`. |
| `GITHUB_EMULATOR_*` | | Any emulator setting passes through, for example `GITHUB_EMULATOR_RATE_LIMIT=1000000`. |

`CI_STACK_DNS_EXTRA`, `CI_STACK_RAW_HOST` and `CI_STACK_RAW_ADDR` serve more
names on this VM. The header of
[`scripts/ci-stack.sh`](../../scripts/ci-stack.sh) describes them.

The upstream runner dials the scheme's default port for some URLs, so serve
it on port 80, or on 443 with TLS. A port below 1024 needs
`sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80` first. This is the
upstream runner over https, as the smoke workflow starts it:

```bash
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80
export CI_STACK_DIR=/tmp/ci-stack
CI_STACK_RUNNER_KIND=upstream CI_STACK_PORT=443 CI_STACK_TLS=1 \
  CI_STACK_HOST=ghemu.localhost CI_STACK_SYSTEM_TRUST=1 \
  scripts/ci-stack.sh start
EMU=https://ghemu.localhost:443 EMU_CA="$CI_STACK_DIR/tls/ca.pem" \
  scripts/ci-workflow-smoke.sh
```

## Seed GitHub Apps

[`scripts/ci-seed-mint-apps.py`](../../scripts/ci-seed-mint-apps.py) creates
the GitHub Apps that a token-minting service (for example fullsend's
standalone mint) needs on a running stack. It uses
only the Python standard library.

```text
ci-seed-mint-apps.py BASE_URL ADMIN_TOKEN ORG PEM_DIR [ROLE ...]
ci-seed-mint-apps.py token BASE_URL ADMIN_TOKEN ORG ROLE [SECONDS]
```

The first form creates the org if it is missing. For each role, it creates
one App with the slug `<app set>-<role>`, installs it on the org for all
repositories, and writes its private key to `PEM_DIR/<role>.pem`. The app set
comes from `APP_SET` (default `fullsend`). Without roles, it seeds `fullsend`,
`triage`, `coder`, `review`, `retro`, `prioritize`, `scribe` and `e2e`. On
stdout it prints the mint's `ROLE_APP_IDS` JSON, which leaves out `e2e`. It
reuses an existing App or installation.

The second form prints an installation token for one seeded App, for example
`e2e`. `SECONDS` is its lifetime and defaults to one day (86400).

```bash
TOKEN="$(cat "$CI_STACK_DIR/admin-token")"
scripts/ci-seed-mint-apps.py http://127.0.0.1:8000 "$TOKEN" my-org /tmp/pems
scripts/ci-seed-mint-apps.py token http://127.0.0.1:8000 "$TOKEN" my-org e2e
```

## Troubleshooting

| Error text | Cause | Fix |
|---|---|---|
| `a stack is already running in /tmp/ci-stack (emulator pid 995); run 'scripts/ci-stack.sh stop' first` | A stack started from this `CI_STACK_DIR` is still running. | Run `scripts/ci-stack.sh stop`, or use another `CI_STACK_DIR` and `CI_STACK_PORT`. |
| `http://127.0.0.1:8000 already answers; another server holds port 8000` | Another server holds the port, for example a stack from another `CI_STACK_DIR`. | Stop that server, or set another `CI_STACK_PORT`. |
| `CI_STACK_HOST must be a lower-case host name or IPv4 address, got 'Bad_Host'` | `CI_STACK_HOST` has upper-case letters, underscores, a scheme or a port. | Set a bare lower-case name or IPv4 address, for example `ghemu.localhost`. |
| `the emulator did not start; last lines of /tmp/ci-stack/emulator.log:` | The emulator exited or did not answer within about 30 seconds. The log lines follow. | Read the log lines. A missing module means `CI_STACK_PYTHON` lacks the locked dependencies (step 1). |
| `FAIL: run concluded ...` from `scripts/ci-workflow-smoke.sh` | The workflow run did not succeed. | Read `$CI_STACK_DIR/runner-<n>.log` and the job lines the script printed above it. |

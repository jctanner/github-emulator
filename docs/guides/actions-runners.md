# Actions Runners

This guide starts an Actions runner next to the emulator, so workflow jobs run
and show up in the web UI. Start with step 1 of
[Run the Python runner](#run-the-python-runner).

## Prerequisites

- A running stack from the [README quickstart](../../README.md#quickstart-container) (`make up`).
  The container image already includes the built web UI.
- With Podman, use `podman compose` wherever this page says `docker compose`.

## Run the Python runner

The Compose stack includes an `actions-runner` service. It runs the
deterministic Python runner, which executes the shell `run:` steps the emulator
stores in each job and uploads the captured logs.

1. Create a runner token and the default repository, `admin/test-repo`. The
   script rewrites `.env`, which Compose reads. Repeat this step after each
   `make up`, because `make up` starts from an empty database.

   If you started the stack on another port (`make PORT=9000 up`), first run
   `export PORT=9000 GITHUB_EMULATOR_API_URL=http://localhost:9000/api/v3`
   in this shell. Then the script and every later `docker compose` command
   use that port (otherwise Compose recreates the emulator on 8000), and you
   replace `8000` in the URLs below. This path was not run for this guide.

   ```console
   $ make actions-runner-env
   ./scripts/actions-compose-bootstrap.sh
   Creating runner admin token through http://localhost:8000/api/v3 ...
   Ensuring repository admin/test-repo exists ...
   Wrote .env for docker compose:
     GITHUB_EMULATOR_RUNNER_TOKEN=<redacted>
     RUNNER_REPO=admin/test-repo
   ```

2. Start the runner and check its log. It reaches the emulator over plain
   HTTP at `http://github-emulator:8000`:

   ```console
   $ docker compose up -d actions-runner
   $ docker compose logs actions-runner
   ...
   [INFO] Registered as runner #1
   [INFO] Runner ready. Polling for jobs on admin/test-repo ...
   ```

3. Push a workflow to the repository:

   ```bash
   git clone http://localhost:8000/admin/test-repo.git /tmp/test-repo
   cd /tmp/test-repo
   mkdir -p .github/workflows
   cat > .github/workflows/hello.yml <<'EOF'
   name: hello
   on: push
   jobs:
     hello:
       runs-on: self-hosted
       steps:
         - run: echo "hello from the emulator runner"
   EOF
   git add .github/workflows/hello.yml
   git commit -m "Add hello workflow"
   git push http://admin:ghp_admin_default_token@localhost:8000/admin/test-repo.git HEAD
   ```

4. Watch the runner pick up the job. These lines are excerpts; the real log
   lines start with the service name and a timestamp:

   ```console
   $ docker compose logs actions-runner
   ...
   [INFO]   Step 1 passed
   [INFO] === Job #1 finished: success ===
   ```

5. Open the run in the web UI at
   `http://localhost:8000/ui/admin/test-repo/actions`.

To watch another repository, set `RUNNER_REPO`, then repeat steps 1 and 2:

```bash
RUNNER_REPO=admin/my-repo make actions-runner-env
docker compose up -d actions-runner
```

### Artifacts and the runner's host name

`actions/upload-artifact` and `actions/download-artifact` v4+ accept only a
`github.com`, `*.ghe.com` or `*.localhost` server host. A runner that reaches
the emulator as `ghemu.local` or `github-emulator` cannot use them. To run them,
serve the emulator under a `*.localhost` name. See
[ADR-0004](../decisions/ADR-0004-actions-results-service-for-artifacts.md).

## Run a site-wide runner

Not run for this guide.

The Python runner also supports an emulator-wide scope for
administrator-managed shared workers:

```bash
RUNNER_SCOPE=site RUNNER_NAME=shared-runner \
RUNNER_LABELS=self-hosted,linux,shared docker compose up -d actions-runner
```

Site-wide runners register through the emulator-specific authenticated
`POST /api/v3/admin/actions/runners/register` endpoint and poll matching jobs
across every repository. Repository-scoped runner tokens cannot use this poll
path, and site-wide tokens cannot use repository-scoped polling.

## Run the real `actions/runner`

Not run for this guide: it builds the upstream runner image.

The project roadmap prefers real `actions/runner` compatibility for maximum
fidelity. An opt-in Compose profile builds the upstream `actions/runner`
binary. Like the Python runner, it reaches the emulator at `http://github-emulator:8000`.

1. Create the runner token and default repository:

   ```bash
   make actions-runner-env
   ```

2. Build and start the real runner:

   ```bash
   make actions-real-runner
   # equivalent:
   docker compose --profile real-runner up --build actions-real-runner
   ```

This real-runner path is the intended compatibility target. Repository- and
enterprise-scoped registration, session polling, timeline updates, log upload,
and completion have been validated against the emulator. The Python runner
remains the deterministic fallback and currently powers the tool-enriched
Fullsend worker image used by Breadboard.

## Check the Actions UI with Playwright

Not run for this guide.

Once a workflow run exists, run the desktop Playwright smoke test against the
compose-served UI:

```bash
python -m pip install playwright
python -m playwright install chromium
make actions-ui-smoke
```

`make actions-ui-smoke` reads `GITHUB_EMULATOR_UI_URL` (default
`http://localhost:8000`) and `RUNNER_REPO` (default `admin/test-repo`).

## Compose variables for runners

`docker-compose.yml` reads these from your shell or `.env`.
`make actions-runner-env` rewrites `.env` with only the first two, so export
the others in your shell, or add them to `.env` after the last
`make actions-runner-env`:

| Variable | Default | Used by |
|---|---|---|
| `GITHUB_EMULATOR_RUNNER_TOKEN` | empty; written by `make actions-runner-env` | both runners |
| `RUNNER_REPO` | `admin/test-repo` | both runners |
| `RUNNER_SCOPE` | `repository` (`site` for a site-wide runner) | Python runner |
| `RUNNER_NAME` | `docker-runner-1` | Python runner |
| `RUNNER_LABELS` | `self-hosted,linux` | Python runner |
| `RUNNER_TLS_VERIFY` | `false` | Python runner |
| `ACTIONS_RUNNER_VERSION` | `2.317.0` | real runner image build |
| `ACTIONS_REAL_RUNNER_CONTAINER_NAME` | `ghemu-actions-real-runner-test` | real runner |

## Troubleshooting

| Error text | Cause | Fix |
|---|---|---|
| `RUNNER_REPO must be owner/repo, got: ...` | `RUNNER_REPO` is not in `owner/repo` form. | Set it as `owner/repo`, for example `admin/my-repo`. |
| `json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)` from `make actions-runner-env` | The script could not reach the API to create its token. | Check that the stack is up: `curl -s http://localhost:8000/api/v3`. On another port, set `GITHUB_EMULATOR_API_URL` as in step 1. |
| `Failed to create or confirm repository ... (HTTP ...)` | The API answered the repository request with an error. | Read the JSON printed above that line. |

## Background

- [ADR-0001: Prefer real runner compatibility for Actions](../decisions/ADR-0001-actions-runner-strategy.md)
- [ADR-0004: Actions results service for artifacts](../decisions/ADR-0004-actions-results-service-for-artifacts.md)
- [Configuration](configuration.md) for the emulator-side Actions settings.

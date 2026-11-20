# Actions Runners

This guide starts an Actions runner next to the emulator, so workflow jobs run
and show up in the web UI. Start with step 1 of
[Run the Python runner](#run-the-python-runner).

Not run for this guide: none of the commands on this page were run when it was
written. They need a running container stack, and the runner images.

## Prerequisites

- A running stack from the [README quickstart](../../README.md#quickstart-container) (`make up`).
  The container image already includes the built web UI.

The repository Actions UI is at:

```text
http://localhost:8000/ui/<owner>/<repo>/actions
```

## Run the Python runner

The Docker Compose stack includes an `actions-runner` service. It runs the
deterministic Python runner.

1. Bootstrap the runner's token and default repository:

   ```bash
   make actions-runner-env
   ```

2. Start the runner:

   ```bash
   docker compose up -d actions-runner
   ```

By default the runner watches `admin/test-repo`. To watch another repository,
set `RUNNER_REPO`:

```bash
RUNNER_REPO=admin/my-repo make actions-runner-env
docker compose up -d actions-runner
```

The bundled Python runner is the deterministic fallback. It executes local
shell `run:` steps that the emulator stores in the job payload, and uploads the
captured logs.

### Artifacts and the runner's host name

This runner reaches the emulator as `ghemu.local`, so `actions/upload-artifact`
and `actions/download-artifact` v4+ don't work on it: they accept only a
`github.com`, `*.ghe.com` or `*.localhost` server host. To run them, serve the
emulator under a `*.localhost` name. See
[ADR-0004](../decisions/ADR-0004-actions-results-service-for-artifacts.md).

## Run a site-wide runner

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

The project roadmap prefers real `actions/runner` compatibility for maximum
fidelity. An opt-in compose profile builds the upstream `actions/runner`
binary:

1. Bootstrap the runner's token and default repository:

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

Once a workflow run exists, run the desktop Playwright smoke test against the
compose-served UI:

```bash
python -m pip install playwright
python -m playwright install chromium
make actions-ui-smoke
```

`make actions-ui-smoke` reads `GITHUB_EMULATOR_UI_URL` (default
`http://localhost:8000`) and `RUNNER_REPO` (default `admin/test-repo`).

## Background

- [ADR-0001: Prefer real runner compatibility for Actions](../decisions/ADR-0001-actions-runner-strategy.md)
- [ADR-0004: Actions results service for artifacts](../decisions/ADR-0004-actions-results-service-for-artifacts.md)
- [Configuration](configuration.md) for the runner-related `GITHUB_EMULATOR_` settings.

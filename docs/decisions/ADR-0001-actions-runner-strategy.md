# ADR-0001: Prefer Real Runner Compatibility for Actions

## Status

Accepted

## Context

The emulator has a partial GitHub Actions implementation:

- workflow discovery and run/job creation
- REST endpoints for workflows, runs, jobs, secrets, variables, and runners
- a custom Docker Compose runner service using `src/runners/emulator/runner.py`
- partial GHES/Azure Pipelines-style endpoints for possible real
  `actions/runner` compatibility

The project needs enough Actions API and frontend surface to visualize jobs.
It also needs a runner strategy. A natural question is whether GitHub-owned
hosted runners can be used by this emulator instead of running local or
self-hosted runners.

Official GitHub documentation describes GitHub-hosted runners as machines
provided by GitHub for GitHub Actions workflows. It describes self-hosted
runners as systems users deploy and manage to execute GitHub Actions jobs.
GitHub Enterprise Server documentation says GHES users should use self-hosted
runners and that GitHub-hosted runners are not supported.

The emulator is not GitHub's Actions control plane. It owns its own scheduler,
database, API, repository storage, identity, runner tokens, and job lifecycle.

## Decision

Do not plan on using GitHub-owned hosted runners as the execution backend for
this emulator.

Use emulator-managed runners instead, with the real `actions/runner` binary as
the preferred compatibility target:

1. Make the real `actions/runner` binary work against the emulator's GHES/Azure
   Pipelines-style endpoints.
2. Keep the Docker Compose `actions-runner` service as the first local runner
   path, but evolve it toward the real runner if the compatibility spike proves
   viable.
3. Keep the custom Python runner as a bootstrap, test, and deterministic
   simulation fallback rather than the primary fidelity target.
4. If scaling is needed later, explore ephemeral self-hosted runners or
   Kubernetes-managed runner pools controlled by this emulator.

## Consequences

- The first Actions milestone can focus on API/UI visibility without depending
  on external GitHub billing, identity, or runner provisioning.
- Maximum workflow/runtime compatibility should come from the real runner
  protocol, not from reimplementing runner behavior in Python.
- The project still controls the emulator-side scheduler, API, storage, and
  runner token model.
- The emulator will not perfectly match GitHub-hosted runner images, lifecycle,
  billing, isolation, or tool preinstalls.
- The custom Python runner should remain useful for tests and development even
  if it never becomes a full Actions runtime.

## Evidence

- GitHub-hosted runners are GitHub-provided machines for GitHub Actions
  workflows:
  `https://docs.github.com/en/actions/concepts/runners/github-hosted-runners`
- Self-hosted runners are deployed and managed by the user:
  `https://docs.github.com/en/actions/concepts/runners/self-hosted-runners`
- GitHub Enterprise Server uses self-hosted runners rather than GitHub-hosted
  runners:
  `https://docs.github.com/en/enterprise-server@3.17/actions/how-tos/manage-runners/self-hosted-runners/add-runners`
- Larger runners are GitHub-hosted runner features for GitHub Team or GitHub
  Enterprise Cloud:
  `https://docs.github.com/en/actions/concepts/runners/larger-runners`
- Actions Runner Controller is a Kubernetes operator for self-hosted runners:
  `https://docs.github.com/en/actions/concepts/runners/actions-runner-controller`
- Current repo evidence:
  - `docker-compose.yml` already defines an `actions-runner` service.
  - `docker-compose.yml` now has an opt-in `actions-real-runner` service
    profile that builds the upstream `actions/runner` binary.
  - `src/runners/emulator/runner.py` already implements a custom runner loop.
  - `src/app/api/actions_pipelines.py` and
    `src/app/api/actions_distributed_task.py` already begin a real-runner
    compatibility path.

## Remaining Validation

- Run a spike with the real `actions/runner` binary against the emulator's
  compatibility endpoints.
- Compare the upstream runner's actual registration and OAuth/token exchange
  behavior with the emulator's current pool-scoped distributed-task endpoints.
- Fill any remaining GHES/Azure Pipelines payload fields needed by the real
  runner beyond the currently tested session, message, timeline, log, and job
  request flow.
- Keep the custom Python runner as a deterministic local fallback that can
  execute simple shell `run:` steps, while avoiding a full Python reimplementation
  of the upstream runner runtime.

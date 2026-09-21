# Fullsend per-repo dispatch support (wave 1)

## Goal

Let the current Fullsend per-repo scaffold route a real issue event through
`reusable-dispatch.yml` to a stage job in this emulator.

Driven by breakpoint B2 of Breadboard's Fullsend integration conformance plan,
which traced the real scaffold against this emulator and produced a 25-gap
list. The reviewer ordered the fixes into four waves. This task is wave 1.

## Failure observed

Issue #38 in `fullsend-dev/triage-target` matched the installed shim and
created run `1103`. Its job `1752` ("dispatch (reusable workflow)") sat in
`queued` with zero steps and never progressed. Nothing past the second of ten
boundaries in the Fullsend flow ran.

## Scope (wave 1 only)

Four emulator gaps. A fifth wave-1 item (seeding the upstream Fullsend
repository) belongs to Breadboard, not here.

- **B1** Job `outputs:` are never parsed and there is no `needs` context, so
  `needs.route.outputs.stage` is always empty and every stage job's `if:`
  evaluates false. This is what actually blocks the dispatch design; the
  unresolved reusable call merely hid it.
- **B2** `fromJSON()` is not implemented. The triage job reads
  `fromJSON(needs.route.outputs.event_payload).issue.html_url`.
- **C1** Any `uses:` step that is not `actions/checkout@*` or a local
  composite action is skipped and reported as **success**, so a skipped mint
  or agent step shows green.
- **E1** `GET /installation/repositories` returns 404 where GitHub returns
  401/403 for a PAT, which hard-fails `fullsend repos install` preflight.
- **C3** (added 2026-09-17) `${{ github.token }}` is never resolved. Promoted
  into this wave after wave 1 showed it blocks the authorization gate.

Out of scope here (later waves): `toJSON`, `hashFiles`,
`job.workflow_repository`/`job.workflow_sha`, checkout option fidelity,
encrypted secrets, permission enforcement, OIDC claim derivation.

## Implementation

**B1 — job outputs and the `needs` context.** Three separate defects sat behind
this one gap.

- `build_job_graph` did not read `outputs:` at all. It does now, and the
  mapping is stored on the job as `outputs_config`.
- `needs:` entries name YAML job keys, but jobs were stored and matched by
  their rendered display name. A job written as `route:` with `name: Route`
  could never satisfy `needs: route`, so dependent jobs were unreachable
  regardless of outputs. Added a `job_key` column and matched on it.
- Jobs were fully rendered at run creation, before any job had run, so an
  expression reading `needs.*.outputs.*` could only ever render empty. Jobs
  that declare dependencies now keep their unrendered condition, steps, env,
  and matrix in `pending_render` and are resolved in `dispatch_ready_jobs`
  once their dependencies complete, with a `needs` context in scope. The base
  expression context is rebuilt there from the run's stored trigger payload
  and the repository's variables and secrets rather than being persisted.

The runner now returns its collected step outputs with the job completion, and
the server resolves the job's `outputs:` mapping against them.

New columns on `workflow_jobs`: `job_key`, `outputs`, `outputs_config`,
`pending_render`, added by migration `0004_workflow_job_outputs`.

**B2 — `fromJSON`.** Added to the shared expression evaluator, along with the
trailing property and index access that makes it useful
(`fromJSON(x).issue.html_url`, `fromJSON(x).include[0]`). Missing properties
resolve to null as on GitHub rather than raising. String interpolation routes
through the full parser only when the expression contains a call, so plain
context paths and the existing `||` fallback keep their previous behaviour.

**C1 — silent step skipping.** A `uses:` step the runner cannot execute was
logged as "Skipping non-shell step" and reported **success**. It now fails and
names what the runner does support. This is the change most likely to surface
other problems, which is the point: a skipped credential-minting or agent step
previously looked identical to one that worked.

**C3 — `github.token`.** The renderer had a carve-out preserving this
expression "for the runner's runtime renderer", sitting under a comment about
step outputs. That was true of `steps.*`, which the runner does resolve, but
never of `github.token`: the runner's renderer handled only `inputs.` and
`steps.`, so the value was handed to a component with no code to receive it. It
reached steps as literal text, `gh` sent it as a bearer token, and the routing
step's collaborator permission check failed closed.

The emulator already mints a job-scoped credential (`issue_job_token`) and
authenticates it, but only issued it on the upstream-runner protocol path. The
custom polling path now issues one too, and the runner resolves
`${{ github.token }}` from it at step-render time. Resolving it on the runner
rather than the server keeps the credential out of the persisted step records,
which is what the original comment always implied should happen.

**Known limitation.** A job token authenticates as the run actor with full
rights, because job-level `permissions:` are parsed, stored, and read nowhere.
So `github.token` is now a working credential that ignores the `permissions:`
block above it. Enforcing those permissions is separate, still-pending work;
this token should not be mistaken for a scoped one until that lands.

**E1 — `/installation/repositories`.** Added, scoped to installation tokens.
A personal access token now gets 403 instead of a 404 from a missing route,
because clients probe this endpoint to classify their credential and treat
401/403 as "ordinary token" while treating anything else as fatal.

## Additional defects found while doing the above

All five were pre-existing and unreachable until the reusable call resolved,
so the original gap list could not have named them. Each one silently skipped
work or reported success rather than reporting a problem.

- **Dynamic matrix crashed the event dispatch.** The harness stage declares
  `matrix: ${{ fromJSON(...) }}`. Strategy blocks are not rendered before
  expansion, so the value is a string and `dict()` raised — surfacing as
  **HTTP 500 on the API call that created the issue**. The trigger failed, not
  just the workflow. Now treated as a single job with a warning; real dynamic
  matrix support is still unbuilt.
- **Reusable-call substitution destroyed compound expressions.** Any
  expression merely *starting* with `inputs.` was rewritten as a path lookup,
  so `${{ inputs.matrix == '' }}` became the empty string and then failed to
  parse. That is the condition guarding the dispatch's Route job, so nothing
  routed. Only bare paths are substituted now; the call's inputs and secrets
  travel with each inlined job into its expression context instead.
- **Combining caller and child conditions produced unparseable text.** The two
  `if:` strings were concatenated with their `${{ }}` wrappers intact, giving
  `(...) && (${{ ... }})`. Both sides are unwrapped before combining now.
- **Step conditions were evaluated by the runner with almost no context.** The
  runner resolves only `steps.*`, so a guard such as
  `if: inputs.event_action == ''` was always true and fired the workflow's own
  validation, failing the Route job. Conditions that do not depend on runtime
  state are now decided server-side and reduced to a literal the runner
  understands; `steps.*`, `success()`, `failure()`, `cancelled()` and
  `always()` still belong to the runner.
- **Reusable-call inputs were never rendered.** A `with:` value is written in
  the caller's terms (`event_action: ${{ github.event.action }}`) but was
  carried in unrendered, so `inputs.event_action` was the literal expression
  text. Now rendered against the caller's context.

## Verification

- Full suite: 351 passed (342 before this work).
- New regression file `tests/actions/test_job_outputs.py` (17 tests) covers
  outputs parsing, resolution from step outputs, key-not-display-name
  dependency resolution, stage conditions reading `needs`, `fromJSON` with
  property and index access, compound-expression preservation in reusable
  calls, condition combining, dynamic-matrix safety, call-input rendering,
  server-side step-condition resolution, and that plain paths, `||`
  fallbacks, and preserved `steps.*` expressions are unchanged.
- `tests/test_database_migrations.py` head assertions moved to `0004`.
- `tests/actions/test_runner_live_logs.py` stub updated for the completion
  signature change.
- End-to-end evidence against the live Breadboard stack is recorded in that
  project's conformance plan; this repository's part is the four gaps above.

## Status

The four scoped gaps are complete, and the three inlining defects plus two
context defects found along the way are fixed. The end-to-end goal is **not**
met: the dispatch now runs as far as its authorization gate and fails closed
there, because `${{ github.token }}` is never resolved, so the routing step's
collaborator permission check gets `Bad credentials (HTTP 401)`.

Resolving `github.token` to a scoped job token is the next piece of work. It
pairs naturally with enforcing job-level `permissions:`, which are currently
parsed and ignored, so that the token carries the job's declared permissions
rather than the run actor's full rights.

Evidence: run 1120 in `fullsend-dev/triage-target` on the Breadboard stack.
Route completed successfully; its "Determine stage" step logged
`Permission API call failed ... Bad credentials (HTTP 401)` followed by
`No stage matched — skipping dispatch`.

## Update: Actions OIDC now describes the run

The stale status above is superseded. `${{ github.token }}` resolves, job-level
`permissions:` are enforced on job tokens, and the Actions OIDC endpoint has
been rebuilt.

### What changed

- `src/app/api/oidc.py` authenticates the caller with its own job token,
  looks up the run, and derives every identifying claim from it: repository,
  owner, repository id, run id, run number, run attempt, workflow, workflow
  ref, job workflow ref and sha, ref, sha, event name, actor, and actor id.
  The caller chooses only the audience, as on GitHub. It previously accepted a
  static bearer string and a caller-supplied `subject` query parameter, which
  let any caller claim any repository.
- `src/app/services/oidc_service.py` replaces the fixture-claim `issue()` with
  `subject_for()` and `issue_for_job()`. `subject_for()` returns GitHub's
  pull-request subject form for pull-request events.
- A job must declare `id-token: write` to get a token, unless it declares no
  permissions block at all, matching the permissive default documented for the
  scope check.
- `src/runners/emulator/runner.py` drops the loopback OIDC broker and the
  static `FULLSEND_DEV_OIDC_TOKEN`. Each step gets
  `ACTIONS_ID_TOKEN_REQUEST_URL` pointing at this emulator and
  `ACTIONS_ID_TOKEN_REQUEST_TOKEN` set to the job's own token. Both are cleared
  when the job did not ask, so a leftover in the pod environment cannot be
  inherited. The URL carries a query string because the caller appends
  `&audience=`.
- `src/app/api/actions_dispatch.py` sends the job's declared permissions with
  the claimed job so the runner can apply that gate.
- `src/app/api/actions_distributed_task.py` puts the same two variables in each
  step's environment in the job request message, under the same gate.
- `src/app/middleware/error_handler.py` no longer replaces every 403 body with
  the word "Forbidden". It preserves a supplied detail, so a refusal names the
  scope that was missing. Collapsing them made unrelated causes look identical.

### Tests

- `tests/actions/test_oidc_claims.py`, 8 tests: claims derived from the run, a
  caller-supplied subject ignored, the pull-request subject form, a static
  bearer refused, no authorization refused, a job without `id-token: write`
  refused with a 403 that names the reason, and GitHub's default audience.
- `tests/actions/test_runner_oidc_environment.py`, 4 tests: the request token
  is the job's own, the URL carries a query string, an ungranted job gets
  neither variable even with leftovers in the environment, and an absent
  permissions block still permits minting.
- `tests/test_apps_oidc.py` now asserts that an unauthenticated caller gets
  401 rather than a token.
- Full suite: 391 pass.

### Evidence

Run 1150 in `fullsend-dev/triage-target` on the Breadboard stack. Route
succeeded; Triage checked out the config repository and upstream defaults,
prepared its workspace, **minted a triage token**, and checked out the target
repository with it. The mint verified the assertion against this emulator's
published keys and logged
`repos=fullsend-dev/triage-target permissions=contents=read,issues=write,metadata=read`.
The run then stops on a third-party action the runner does not execute,
`google-github-actions/auth`, which is tracked in the Breadboard conformance
plan rather than here.

## Update: locally emulated third-party actions

The runner refuses any `uses:` it cannot execute, by name, rather than skipping
it and reporting success. That stays the default. It now also keeps a short,
named list of third-party actions it emulates locally, in `_ACTION_SHIMS`.

The list holds one entry, `google-github-actions/auth`. Workload Identity
Federation cannot be reproduced here, since there is no Google security token
service to reach and no federation trust against the local issuer. What the
steps after it depend on is narrower: an application-default credentials file
and the environment variables that point at it. Fullsend's own
`prepare-sandbox-credentials.sh` states that it no-ops for any credential type
other than `external_account`, so a mounted credentials file is a mode Fullsend
already supports. The emulation reads
`/var/run/secrets/gcp/credentials.json`, overridable with
`FULLSEND_DEV_GCP_CREDENTIALS_FILE`, and exports
`GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_GHA_CREDS_PATH`,
`CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE`, and the project variables. It fails
the step when the file is missing, unreadable, or federated.

Matching is on `owner/repo`, not on the version, so bumping a pinned SHA shows
up in the log rather than turning into a sudden unsupported action.

The refusal message for everything else now names the emulated list, so the
boundary is readable from a failing log without opening the source.

### Tests

`tests/actions/test_action_shims.py`, 10 tests: the exported variables, the
action input taking precedence over the credential's own project, the
quota-project fallback for an authorized-user credential, each of the four
refusal paths, the credential path being masked, an unlisted action still being
refused by name, and name-not-version matching. Full suite: 401 pass.

### Evidence

Run 1152 in `fullsend-dev/triage-target`. The Triage job now passes "Setup GCP
and prepare credentials" and "Setup agent environment", and reaches the agent
step. It logged
`credentials: /var/run/secrets/gcp/credentials.json (type: authorized_user)`
and the project it resolved. The remaining failure is in Fullsend's own
`action.yml`, which resolves releases against `api.github.com` by name rather
than through `GITHUB_API_URL`; that is tracked in the Breadboard conformance
plan, not here.

## Update: standard runner variables

A survey of every `RUNNER_*` and `GITHUB_*` variable the Fullsend tree reads
found four the runner never set. All four are now provided.

- `RUNNER_TEMP`. Created empty for each job and removed with the workspace.
  It sits **beside** the workspace, not inside it: a checkout with no `path:`
  initialises a git repository at the workspace root, and the step that needs
  this directory unpacks a source tree into it. The default is
  `<parent of RUNNER_WORKDIR>/_temp`, overridable with `RUNNER_TEMP_DIR`. The
  deployment manifests mount one volume at `/runner-root` with
  `/runner-root/workspace` and `/runner-root/_temp` as siblings.
- `RUNNER_ARCH`. `X64`/`ARM64`, GitHub's spelling rather than uname's.
- `GITHUB_PATH`. A behaviour, not a value: each line a step writes is prepended
  to `PATH` for the steps that follow, which is how an install step makes its
  binary runnable later. It does not affect the step that wrote it.
- `GITHUB_ACTION_PATH`. The directory of the composite action currently
  running. Set only inside one, and actively cleared outside one so a value
  inherited from the pod environment cannot point a script at the wrong tree.

### Tests

`tests/actions/test_runner_environment.py`, 10 tests. Full suite: 411 pass.

### Evidence

Run 1159 in `fullsend-dev/triage-target`. The agent step clones the Fullsend
source into `/runner-root/_temp/fullsend-src`. It then stops on a compound
expression the composite renderer passes through as literal text, tracked in
the Breadboard conformance plan as G19: `_render_local_action` handles only
`github.token`, `inputs.X`, and a four-part `steps.<id>.outputs.<name>`, and
returns anything else unchanged. The server-side renderer solved the same
problem by routing compound expressions through a real parser; the runner
already has a suitable one in `_StepIfParser`.

## Update: compound expressions in local composite actions

`_render_local_action` handled `github.token`, `inputs.X`, and a four-part
`steps.<id>.outputs.<name>`, and returned anything else as its own text. A
composite step's `env:` written as
`${{ steps.detect.outputs.source-ref || steps.detect.outputs.version-url }}`
therefore reached the shell as those literal characters.

The fix mirrors what `workflow_expressions.render_expressions` already does on
the server: anything containing an operator, a string literal, or a call goes
through `_StepIfParser`.

- `_StepIfParser` now takes a context instead of raw step outputs, built by
  `_runner_context()`, so one expression can read `steps`, `inputs`, and
  `github.token` together. Its constructor signature changed.
- `parse()` keeps returning a boolean; `evaluate()` returns the value, which is
  what rendering needs.
- A missing key under a known root renders empty, matching Actions. An unknown
  root raises, and the renderer leaves the expression as literal text. The
  server renders every other context before a step arrives here, so an
  unresolved root means an upstream renderer did not run, and rendering it
  empty would hide that. A step condition on an unknown root still fails the
  step loudly.

### Tests

`tests/actions/test_runner_expressions.py`, 13 tests.
`tests/actions/test_job_outputs.py::test_runner_parser_consumes_both_operands_of_and_or`
updated for the new constructor contract. Full suite: 424 pass.

### Evidence

Run 1161 in `fullsend-dev/triage-target`. The agent step logs
`Cloning fullsend at ref: de965fc4129b63054eded58483842ea1b7a5c828`, the
shallow fetch succeeds on the first attempt, and the checkout lands on that
commit. It then stops on `actions/setup-go`, which is a decision about which
install path the conformance run should take rather than an emulator gap; that
is tracked in the Breadboard conformance plan.

## Update: actions/setup-go emulation and the runner context

`actions/setup-go` joins `google-github-actions/auth` in `_ACTION_SHIMS`. The
two are not alike, and the difference is worth stating: the auth action needs a
Google security token service this stack cannot reach, while setup-go resolves
a version, makes a toolchain available, and puts it on `PATH`. All of that is
reproducible, so this emulation reproduces rather than substitutes.

- Go 1.26.5 is pinned in the runner image with a checksum, as `gh` and `yq`
  already are. `FULLSEND_DEV_GO_ROOT` overrides its location.
- The requested version comes from `go-version`, or from `go-version-file`
  where a `toolchain` directive beats a `go` directive and any other file holds
  a bare version.
- Selection: the image toolchain is used when it is new enough; when it is
  older than the request, it is still used and the log says Go will switch
  toolchains during the build, which Go 1.21 and later do; a download is the
  fallback for no Go or a Go too old to switch; no Go and no requested version
  fails rather than guessing. A failed download is reported, not swallowed.

### The runner context

`runner.*` describes the machine, so only the runner can resolve it and the
server correctly leaves it alone. The runner did not implement it either, so a
composite action's `${{ runner.temp }}` reached a shell as literal text. It is
now resolved for `os`, `arch`, `name`, and `temp`. Rendering and condition
evaluation previously carried two copies of the same dotted lookup; they now
share `_lookup_context_path`.

### Tests

`tests/actions/test_setup_go_shim.py`, 15 tests, plus three in
`tests/actions/test_runner_expressions.py` for the runner context. Full suite:
441 pass. Live runs cover registration, dispatch, version-file resolution and
the loud failure; selection itself is covered by unit tests only, because the
emulator's mirror carries no `go.mod`.

### Evidence

Runs 1163 and 1165 in `fullsend-dev/triage-target`. The second resolves
`${{ runner.temp }}` to `/runner-root/_temp` and fails with
`go-version-file not found`, which is correct: the mirror of
`fullsend-ai/fullsend` is a narrow subset with no Go source in it. Whether to
widen that mirror is a scope decision tracked in the Breadboard conformance
plan, not here.

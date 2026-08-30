# Task: Improve Repository Page Response Time

## Goal

Make `/ui/{owner}/{repo}` become useful quickly under the Breadboard Kubernetes
deployment and remain responsive when its repository API requests run
concurrently. Optimize from measurements rather than hiding the latency with a
loading indicator.

## Measured baseline

Profiled on 2026-08-30 using `admin/ansible-agent-harness` through
`https://github.local`:

- Warm DOM content loaded: 27 ms.
- Repository content visible: 2.3–3.4 seconds.
- Repository metadata: 100–214 ms from the browser.
- The five subsequent repository calls: approximately 1.2–2.6 seconds each.
- One branch-list request: approximately 0.2 seconds.
- Five concurrent branch-list requests: approximately 2.6 seconds each.
- Ten concurrent branch-list requests: approximately 7 seconds each.

During one profile, the GitHub emulator container was CPU-throttled in 186 of
196 scheduling periods. It consumed the full approximately 9.5 CPU-seconds
available during 19.6 wall-seconds and accumulated approximately 47 seconds of
aggregate throttled time. The Breadboard manifest currently limits the
container, including Caddy and Uvicorn, to `500m` CPU.

Record a fresh baseline before implementation because other Actions activity
can affect the shared emulator pod.

## Implementation progress

The collection-count issue was corrected on 2026-08-30:

- Added a typed, UI-specific repository summary endpoint returning accurate
  commit, branch, and tag counts.
- Replaced the homepage's full commit, branch, and tag collection requests with
  the summary request.
- Deferred the summary until after repository files and README are visible so
  count work does not compete with critical content under the throttled CPU
  budget.
- Added API coverage for an uncapped commit count and Playwright coverage that
  rejects collection requests from the repository homepage.
- Deployed request count fell from seven to five; only four requests occur on
  the critical file-content path because the summary is deferred.
- Single-run deployed content visibility improved from the 2.3–3.4 second
  baseline to 1.1–1.4 seconds. This is not yet the required 20-navigation
  p50/p95 acceptance sample.

## Problems to address

### 1. Kubernetes CPU throttling

The deployed container has a `500m` CPU limit. Caddy, one Uvicorn worker,
SQLite/aiosqlite work, response serialization, and Git subprocesses all share
that budget. Concurrent requests exhibit a nonlinear latency increase while
the container is continuously throttled.

The deployment resource change belongs in Breadboard at
`deploy/k8s/10-github-emulator.yaml`, not in this component repository. Measure
at `500m`, `1`, and `2` CPUs before selecting a new request/limit. Do not merely
increase the limit without preserving before/after results.

### 2. Repository-home API fan-out and waterfall

`RepositoryPage` first fetches repository metadata to discover the default
branch. It then starts five calls in parallel:

1. root contents;
2. README;
3. up to 100 commits;
4. up to 100 branches;
5. tags.

The application also fetches the browser session during startup. Nearly all
observed API duration is server time-to-first-byte; response bodies are small.
The fan-out turns the CPU limit into a page-wide latency spike.

### 3. Expensive and inaccurate activity counts

The homepage fetches complete collections only to render commit, branch, and
tag counts. The commit request is capped at 100, so `commits.length` is not a
correct total for larger repositories. Introduce lightweight, accurate counts
or a repository-home aggregate response instead of transferring and
serializing collections that the page does not display.

Prefer a GitHub-compatible mechanism where one exists. If a UI-specific
aggregate endpoint is necessary, keep it explicitly separate from the
GitHub-compatible REST surface and provide a typed response schema.

### 4. Repeated request setup

Each repository call independently verifies the browser session, looks up the
user, and resolves the repository. Git-backed endpoints also start separate Git
processes. Reduce repeated work through aggregation or safely scoped caching;
do not introduce process-global authorization results that can cross users.

### 5. No shared frontend data cache

`useApiData` stores state only in its component instance. Remounting a route
refetches unchanged session/repository resources, and returning to the
repository page repeats the homepage fan-out. Add bounded query caching and
explicit invalidation for mutations, or document and implement another
coherent reuse strategy.

### 6. All-or-nothing repository rendering

The current `Loadable` boundary hides the repository header and file content
until every homepage request finishes. Render useful critical content first:

- repository context and metadata;
- root file listing;
- README when available.

Load noncritical activity counts independently so their failure or latency does
not block repository navigation.

### 7. Incomplete database indexing

The live database correctly uses indexes for `users.login` and
`repositories.full_name`. The `branches` table has no index, so branch queries
scan all rows and create a temporary sort tree. Add an index or uniqueness
constraint appropriate for `(repo_id, name)` and audit other frequently
filtered foreign-key columns, especially Actions tables.

Indexing is a scaling requirement, not the primary homepage fix. With 1,685
branch rows, an isolated in-memory benchmark improved the branch SQL from
approximately 0.171 ms to 0.0066 ms per query. That saving is negligible beside
the measured request and throttling times. Include query plans and realistic
table sizes when proposing additional indexes.

## Required implementation approach

1. Add a repeatable response-time profiler that records navigation milestones,
   API start/duration, status, and response size for cold and warm loads.
2. Record CPU usage and cgroup throttling deltas alongside browser timings.
3. Measure the resource-limit change separately from application changes.
4. Replace full collection requests used only for counts.
5. Make critical repository content progressively renderable.
6. Add safe frontend caching and mutation invalidation.
7. Add justified indexes through the versioned migration system and verify
   their live query plans.
8. Re-profile after each category of change so improvements can be attributed.

## Acceptance criteria

- [ ] A checked-in profiler can reproduce cold/warm repository-page timings and
      list all API requests without mutating repository data.
- [ ] The selected Kubernetes CPU request/limit is documented with `500m`, `1`,
      and `2` CPU comparison results in Breadboard.
- [ ] At the selected limit, five concurrent branch-list requests do not show
      the current multi-second nonlinear latency cliff.
- [ ] Warm repository file content is visible within 750 ms at p50 and 1.5
      seconds at p95 across at least 20 measured navigations on an otherwise
      idle Breadboard deployment.
- [x] Repository metadata, files, and README are not blocked by activity-count
      requests.
- [x] Commit, branch, and tag totals are accurate beyond 100 entries without
      loading full collections into the browser.
- [ ] Returning to an unchanged repository reuses cached data; repository
      mutations invalidate affected entries.
- [ ] `(repo_id, name)` branch lookups use an index in the live SQLite query
      plan, and every additional index is justified by a measured query.
- [ ] Existing API, frontend, Git transport, Actions, and legacy-UI tests pass.
- [ ] Rebuild and deploy through Breadboard with `make host-rebuild-github`, then
      record deployed profiler results.

## Out of scope

- Replacing SQLite solely to improve this page.
- Adding multiple Uvicorn workers before measuring SQLite and resource-limit
  behavior.
- Caching authorization decisions across users or without invalidation.
- Removing `/ui-legacy` as part of this performance work.

## Status

Pending

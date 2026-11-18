# Bug: every workflow lookup re-reads and re-syncs the default branch's workflows

Status: fixed 2026-11-14.

## Summary

On GitHub, `GET /repos/{o}/{r}/actions/workflows/{workflow_id}/runs` is a
plain read and costs about as much as `GET /repos/{o}/{r}`.

In the emulator, every request that looked a workflow up by id or file name
(get a workflow, list its runs, dispatch it) and the workflow list first ran
`sync_workflows_to_db` on the default branch. That is `git ls-tree`, one
`git show` per workflow file, a YAML parse of each, and a database write and
commit. Fullsend's behaviour tests poll
`/actions/workflows/{file}/runs?per_page=10` from about 12 workers at once,
so this endpoint dominated emulator load. Idle, it took 135-250 ms against
about 20 ms for `GET /repos/{o}/{r}`. With 8 concurrent runs on 4 emulator
runners and one poller per repository, a py-spy profile put 20% of
event-loop time in `_resolve_workflow` and another 20% in
`sync_workflows_to_db` and `detect_workflows`.

## Fix

`ensure_workflows_synced` (in `src/app/services/workflow_service.py`)
replaces the unconditional sync in `_resolve_workflow` and `list_workflows`.
It reads one key per lookup with a single `git rev-parse` call: the
repository's disk path, its default branch, and the tree id of
`HEAD:.github/workflows`. It syncs and commits only when that key differs
from the key of the last sync. Then:

- a push that adds, changes or removes a workflow file changes the tree id,
  so the next lookup syncs;
- moving the default branch changes `HEAD`, and changing the
  `default_branch` setting changes the key, so the next lookup syncs;
- a workflow's `state` is only ever set by a sync, and a skipped sync leaves
  the stored rows as the last sync wrote them.

The cache is in-process, keyed by repository id. The emulator serves from
one uvicorn process, and every other caller of `sync_workflows_to_db` (the
push and trigger path, which also syncs other branches) goes through the
same function. That function drops the repository's cache entry and bumps a
per-repository generation counter. A lookup stores its key only if no other
sync ran while it was syncing. So after a push to any branch, the next
lookup reconciles against the default branch exactly as before. The
push/trigger path itself is unchanged.

Not changed: `PATCH /repos/{o}/{r}` with `default_branch` updates only the
database column, not the bare repository's `HEAD`, so workflow detection
keeps reading the old branch. This was already the case before this change.

## Timing

50 sequential `GET .../actions/workflows/wf0.yml/runs?per_page=10` calls
in-process with the test client, on a repository with four workflow files.
The machine was under memory pressure, so the absolute numbers are noisy;
the ratio held in both rounds.

| run | before (main) | after |
| --- | --- | --- |
| round 1, total | 10.15 s | 5.57 s |
| round 1, median | 173 ms | 38 ms |
| round 2, total | 8.00 s | 4.01 s |
| round 2, median | 150 ms | 63 ms |

The first call after a push still syncs, as before.

## Tests

`tests/actions/test_workflow_sync_cache.py`:

- `test_repeated_lookups_detect_workflows_once`: two runs-list calls, a get
  and a list with no push between them call `detect_workflows` once. It
  fails on the previous code (two calls after the first two requests).
- `test_a_push_is_visible_on_the_next_lookup`: with Actions disabled, so no
  push-side sync runs, a changed, an added and a removed workflow are each
  visible on the next lookup, and a removed workflow stays
  `disabled_manually`.
- `test_a_default_branch_change_is_visible_on_the_next_lookup`
- `test_a_branch_push_resyncs_the_next_lookup`: a workflow pushed only to
  another branch is reconciled to `disabled_manually` on the next lookup, as
  before.

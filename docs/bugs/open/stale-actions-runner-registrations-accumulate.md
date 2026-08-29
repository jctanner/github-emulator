# Bug: Stale Actions Runner Registrations Accumulate

## Summary

Restarting repository-scoped runner pods creates additional runner records, and
old registrations can continue to report `online` indefinitely.

## Reproduction

1. Register a runner against a repository.
2. Restart or replace its runner pod repeatedly.
3. Open `/admin/runners` or list the repository's runners through the API.

## Expected

Replacement registration reuses/removes the prior runner identity, or missed
heartbeats reliably transition stale registrations offline and permit cleanup.

## Actual

On 2026-08-28 the live emulator contained 39 registrations for
`fullsend-dev/triage-target` and 8 for `fullsend-dev/.fullsend`, despite only one
Kubernetes runner Deployment for each repository. Thirty-six and five of those
records respectively still reported `online`.

## Impact

The admin and repository APIs overstate available capacity and make it unclear
which runner can actually accept a queued job.

## Related Tasks

- `docs/tasks/done/admin-runner-management-page.md`

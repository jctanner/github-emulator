# Bug: secrets forwarded to a reusable workflow arrive empty

Status: fixed 2026-11-08.

## Summary

When a run is created, the emulator inlines job-level reusable workflows. It
substituted `${{ secrets.X }}` in each call's `secrets:` mapping against the
secrets supplied to the materialiser. For the triggering workflow, those come
from the event payload, which never carries any. Every forwarded secret
therefore became `""`.

At job promotion the emulator layers the call's secrets over the
repository's, so the `""` also overrode the repository's own value. The
called job saw the secret as unset, even though the repository had it.

The same substitution ran over the triggering workflow's plain jobs, so a
plain job's `${{ secrets.X }}` was emptied as well.

Fullsend forwards `FULLSEND_GITHUB_RUNNER_SECRETS` from its shim to
`reusable-dispatch.yml` this way. In Breadboard's behaviour-test T2 tier,
`runner-secret-overlay-guard.feature` ("An overlay guarded on runtime.forge
runs") failed with `BEHAVIOUR_RUNNER_SECRET is not set`, because "Stage runner
secrets" saw an empty input and was skipped.

## Fix

- The triggering workflow's secrets context is now unknown (`None`) at
  materialisation, not empty. `secrets.*` stays an expression there, and
  `_job_context` renders it against the repository's secrets when the job is
  promoted.
- A call's own `secrets:` and `with:` substitute against the caller's
  mapping. That mapping may itself hold a deferred expression in the
  repository's names, so a value chains through each call back to the
  repository.
- A callee's body keeps its own name for a secret it was passed
  (`secrets.A`), which `_job_context` resolves from the call's secrets.
  Substituting there too wrote the caller's name into the body, and the
  call's secrets then remapped it a second time. A swap
  (`A: ${{ secrets.B }}`, `B: ${{ secrets.A }}`) read back the wrong secret.
  This was found in review.
- `secrets: inherit` from the triggering workflow passes the deferred context
  on.
- A called job still sees only the secrets it was passed.

Tests: `tests/actions/test_workflow_call_fidelity.py` (the "secrets forwarded
from the triggering workflow" section, the swap, the two-level rename, and
a secret passed as an input).

Not covered: a secret passed as a `with:` input whose value names a secret
that the same call also remaps under that name. The input's expression is
in the repository's names but is read in the callee's.

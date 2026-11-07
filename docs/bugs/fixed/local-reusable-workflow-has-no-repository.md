# Bug: a local reusable workflow call names no repository

## Summary

For a job defined by `uses: ./.github/workflows/x.yml`, the OIDC
`job_workflow_ref` read `/.github/workflows/x.yml@refs/heads/main` and the
job's workflow source repository was empty. GitHub reports
`owner/repo/.github/workflows/x.yml@refs/heads/main`. Fullsend's mint keys
on that repository, so it refused every vendored install (`fullsend github
setup --vendor`), whose dispatch workflow is local to the installed
repository: "job_workflow_ref does not reference an allowed workflow host
repo".

## Cause

`_resolve_reusable_workflow` filled in the called repository only for a
remote `owner/repo/...` reference. `_called_workflow_ref` expected the caller
to supply the name for a local one, but nothing did.

## Fix

For a local call, look up the repository whose disk path the call resolves
against. That is the calling repository, also when the local call sits inside
a workflow imported from another repository. Regression tests:
`tests/actions/test_local_reusable_workflow_ref.py`. Found by the Breadboard
behaviour-test install spike (BT-E1).

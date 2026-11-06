# Bug: A contents-API commit leaves open pull requests on the old head

## Summary

`PUT /repos/{owner}/{repo}/contents/{path}` with a `branch` commits and moves
the branch, but open pull requests whose head is that branch keep their old
`head_sha`. A git push (`src/app/git/smart_http.py`) and the refs API
(`_record_branch_change` in `src/app/api/git_refs.py`) both update the pull
request; the contents route (`src/app/api/contents.py`, after `update-ref`)
calls only `record_branch`.

## Reproduction

1. Create a repository, branch `feature`, and a file on it with the contents API.
2. Open a pull request from `feature` to `main`; note `head.sha`.
3. Change the file again with the contents API on `feature`.
4. `GET .../pulls/{n}`: `head.sha` is still the step-2 commit.

## Expected

As on GitHub, `head.sha` is the new commit, the pull request records the
push, and auto-merge and review-thread state (`isOutdated`) follow the new
head.

## Actual

The pull request reports the old head until something else moves the ref.

## Impact

Clients that commit through the contents API (bots without git) see stale
pull request heads: review threads are not marked outdated, merge readiness
and auto-merge are evaluated on the old commit. Found while testing review
threads (github-emulator#20); the test works around it with a refs-API
update. The likely fix is to call `_record_branch_change` (or share its pull
request update) from the contents route.

# Task: GraphQL updateRefs (github-emulator#21)

## Goal

Fullsend's code and fix agents publish signed commits by building them with
the REST Git Data API and moving the branch with GraphQL `updateRefs`
(`agents/scripts/lib/github-signed-push.lib.sh`). The mutation and its
`RefUpdate` input were missing.

## Done

- `RefUpdate {name, afterOid, beforeOid, force}`, `UpdateRefsInput`,
  `UpdateRefsPayload {clientMutationId}`, and `Mutation.updateRefs`
  (`src/app/graphql/mutations.py`).
- `src/app/services/ref_update_service.py` checks every update, then applies
  the batch in one `git update-ref --stdin` transaction against the values
  it checked, with a `verify` line for refs left unchanged, so the batch
  changes all refs or none:
  - `beforeOid` all zeros: the ref must not exist; otherwise it must match
    (STALE_DATA); omitted: no check.
  - `afterOid` all zeros deletes the ref.
  - Without `force`, a move must be a fast-forward (UNPROCESSABLE).
  - Names must be fully qualified and valid; object IDs must exist; branch
    refs must point to commits; a ref may appear once per batch.
- Authorization as `createCommitOnBranch`: a hidden repository is NOT_FOUND,
  and the credential needs `contents` write (FORBIDDEN).
- Branch protection as REST's ref update: a locked branch, a force update
  (only an actual non-fast-forward counts), and a deletion are refused
  unless allowed. The default branch cannot be deleted.
- Changed branches update their `Branch` rows and `pushed_at`, then raise
  push activity (with create/delete) through `process_push_event`, which
  moves open pull request heads and raises `synchronize`. A dispatch failure
  is logged and still records the heads.

## Found and fixed on the way

A git push to an open pull request's branch raised no `synchronize`: the
post-push work recorded the new head before `process_push_event`, which then
treated the pull request as already synchronized.
`docs/bugs/fixed/git-push-does-not-synchronize-pull-requests.md`.

## Not done

- Tag changes raise no push, create, or delete events (neither do REST ref
  updates or git pushes of tags): `docs/bugs/open/tag-ref-changes-raise-no-events.md`.
- As with REST ref updates and git pushes, refs move on disk before the
  database records them; a database failure in between leaves `Branch` rows
  behind the refs until the next push resynchronizes them.

## Evidence

`tests/test_update_refs_graphql.py` (12 tests), including Fullsend's exact
mutation document and a git-push synchronize regression test that fails
without the fix.

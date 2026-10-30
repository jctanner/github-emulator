# GraphQL `createCommitOnBranch` is missing

## Symptom

`fullsend github setup <repo> --direct` (Fullsend v0.52.0) fails before it
writes anything:

```
• Committing scaffold files to experiment/testrepo2 (main branch)
✗ Failed to commit scaffold files
Error: committing scaffold files: create commit on branch: github graphql: : Unknown type 'CreateCommitOnBranchInput'. Did you mean 'CreateRepositoryInput'?
```

Since fullsend-ai/fullsend#8368 (ADR 0140) the CLI commits inline regular files
with GraphQL `createCommitOnBranch`, because GitHub signs those commits for any
credential and rulesets requiring signatures accept them. Every scaffold write
from that CLI version failed against the emulator.

## Fix

- `createCommitOnBranch` in `src/app/graphql/mutations.py`, with GitHub's input
  shape: `branch { repositoryNameWithOwner, branchName }`, `expectedHeadOid`,
  `message { headline, body }`, `fileChanges { additions { path, contents },
  deletions { path } }`. It returns `commit { oid }`.
- `commit_files` in `src/app/git/bare_repo.py` builds one commit from the
  additions and deletions on a temporary index, with `expectedHeadOid` as the
  parent, and moves the branch with a compare-and-swap `git update-ref <ref>
  <new> <old>`, so a concurrent push fails the mutation instead of being lost.
- Author is the authenticated account, committer is `GitHub
  <noreply@github.com>`, as for GitHub's web-UI commits. Files are mode 100644.
  Commits are not cryptographically signed.
- Errors carry GitHub's top-level `type`, which Fullsend classifies on:
  `NOT_FOUND` (repository or branch), `FORBIDDEN` (no contents write, same rule
  as the REST contents API), `STALE_DATA` (head moved; the message also has
  GitHub's "is at <sha> but expected <sha>" text), `UNPROCESSABLE` (bad Base64,
  deleting a path that does not exist, or a branch given by `id`). The GraphQL
  router (`app.main`) lifts `extensions.type` to the top level for every
  resolver that sets it.

Not implemented: branch given by node `id`, branch protection and ruleset
enforcement on this path (the REST contents API does not enforce them either),
and push events or webhooks for the new commit (also as the REST contents API).

## Regression coverage

`tests/test_create_commit_on_branch.py`: additions and deletions in one commit
with parent, committer and full message checked; stale head reported as
`STALE_DATA` with the branch unchanged; missing repository and branch
`NOT_FOUND`; deleting a missing path aborts with no commit; an account without
write access is `FORBIDDEN`. End to end: `fullsend github setup
experiment/testrepo2 --direct` then pushed 3 files to main (commit `202fcc5`,
author Admin, committer GitHub).

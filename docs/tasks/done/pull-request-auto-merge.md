# Pull request auto-merge compatibility

## Objective

Support the GitHub GraphQL auto-merge mutations used by `gh pr merge --auto`
and provide a repeatable local equivalent of the merge-queue handoff used by
the Fullsend code agent.

## Implementation

- Persist one auto-merge request per pull request, including actor, timestamp,
  method, and optional commit text.
- Implement `enablePullRequestAutoMerge` and
  `disablePullRequestAutoMerge`.
- Expose the persisted request through `PullRequest.autoMergeRequest`.
- Process queued requests synchronously when a PR receives the
  `ready-for-merge` label. The existing Git merge implementation is reused;
  missing local refs retain the emulator's DB-only fallback.
- Process the same queue after an approved REST review and after the GraphQL
  or REST label mutation.
- Set `mergeStateStatus` to `BLOCKED` for open, non-draft PRs. This is a
  deliberate emulator approximation: full GitHub calculates this from branch
  protection, checks, approvals, conflicts, and merge-queue state.
- Enable `CODE_AUTO_MERGE=true` with squash as the method in the local
  Fullsend code-agent seed. Upstream defaults remain unchanged.

## Readiness contract

`ready-for-merge` is the deterministic local readiness signal. The Fullsend
review post-script already applies this label after an approval. A future
branch-protection/check-run implementation can replace this surrogate without
changing the persisted auto-merge API.

## Evidence

`tests/test_auto_merge.py` verifies queue creation, GraphQL visibility, the
blocked state before readiness, and automatic merge after the readiness label.
The local Fullsend seed contract verifies the Code agent's auto-merge opt-in.

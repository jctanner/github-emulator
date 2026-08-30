# Task: Branch Protection and Merge Readiness Parity

## Goal

Implement GitHub-compatible branch-protection behavior in the emulator so
`mergeStateStatus`, normal merges, and `gh pr merge --auto` are driven by
actual repository policy and PR state rather than the current blanket
`BLOCKED` approximation.

This task should cover the full branch-protection feature surface supported by
the public GitHub API, documenting any intentionally unsupported fields or
behaviors rather than silently accepting them.

## Context

The emulator currently returns `BLOCKED` for every open, non-draft PR because
it does not calculate branch protection, reviews, checks, conflicts, or merge
queues. Its auto-merge request model and service already exist, but processing
is currently gated by the Fullsend-specific `ready-for-merge` label.

Fullsend's code post-script calls `gh pr merge --auto` and expects GitHub to
hold the request until branch-protection requirements pass. Preserve the
existing auto-merge API and integrate it with the new readiness evaluator.

Use the official GitHub REST, GraphQL, webhook, and branch-protection
documentation as the compatibility reference.

## Scope

- Persist branch-protection settings per repository and protected branch or
  branch pattern, using the project's existing migration and ORM patterns.
- Implement the applicable REST branch-protection endpoints and GitHub-shaped
  request/response objects.
- Expose the policy and readiness data required by the existing GraphQL
  clients, including accurate `mergeStateStatus` and `autoMergeRequest`
  behavior.
- Model and evaluate, where supported by GitHub's public contract:
  - required approving reviews and stale-review dismissal
  - code-owner and last-pusher approval requirements
  - required status checks and up-to-date branches
  - conversation resolution
  - signed commits and linear-history requirements
  - required deployments
  - force-push and branch-deletion restrictions
  - administrator enforcement, bypass actors, and push restrictions
  - merge queue settings and queue-related readiness
  - draft state and merge conflicts
- Apply the same readiness evaluator to ordinary merge requests and queued
  auto-merge requests; do not create an `autoMergeRequest` merely because a PR
  is approved, labeled, or currently blocked.
- Reevaluate queued auto-merges after relevant changes, including pushes,
  reviews, review dismissals, status/check-run updates, deployment updates,
  branch-protection changes, and merge-queue changes.
- Add admin/frontend controls where the emulator already provides analogous
  repository or branch configuration surfaces.
- Keep the Fullsend `ready-for-merge` label as an optional policy signal only;
  it must not replace branch-protection evaluation globally.

## Acceptance Criteria

- [x] A protected branch can be configured through the supported API and/or
      admin UI, persisted, inspected, updated, and removed.
- [x] `mergeStateStatus` distinguishes at least draft, merged, clean,
      blocked, conflicting, and unknown states based on actual PR state and
      configured requirements.
- [x] Required reviews, required checks, conflicts, and other enabled rules
      prevent ordinary and auto-merges until satisfied.
- [x] Satisfying all configured requirements allows an enabled auto-merge to
      complete without requiring a synthetic label.
- [x] An enabled auto-merge remains represented by `autoMergeRequest` until it
      merges or is explicitly disabled.
- [x] Review, check, push, and policy changes reevaluate pending auto-merges
      without creating duplicate merges or event loops. Deployment rules are
      explicitly unsupported because the emulator has no deployment model.
- [x] `gh pr merge --auto` works against the emulator with the same request
      semantics used by Fullsend.
- [x] REST and GraphQL responses match GitHub-shaped success and error
      behavior for supported fields; unsupported fields are explicit and
      tested.
- [x] Regression tests cover each implemented protection rule, readiness
      transitions, auto-merge transitions, and conflicting/unknown cases.
- [x] A Fullsend-style end-to-end test demonstrates: PR created → auto-merge
      queued → requirements unmet → requirements satisfied → merged.
- [x] The Fullsend code harness passes `CODE_AUTO_MERGE` and its merge method
      to the runner-side post-script, and the end-to-end test verifies that the
      request is actually queued.

## Likely Files/Areas

- `src/app/models/`
- `src/app/api/`
- `src/app/graphql/`
- `src/app/services/`
- `src/app/admin/` and `src/app/web/`
- `alembic/`
- `tests/`
- Existing auto-merge implementation and GitHub CLI compatibility helpers

Inspect neighboring routes, models, migrations, and tests before adding new
abstractions. Preserve unrelated working-tree changes.

## Verification and Deployment

Run the focused emulator tests and the complete test suite appropriate to the
changes. For the integrated breadboard environment, build and deploy the
updated emulator with this command from the breadboard repository root:

```bash
make host-rebuild-github
```

Do not treat a local-only container build or `docker compose` run as integrated
deployment evidence. Record the exact test commands, deployment result, and
any intentionally unsupported GitHub behavior in this task or the relevant
ledger entry.

## Status

Done

## Implementation Scope

The emulator-native implementation covers exact protected branches, required
reviews, stale and last-push review handling, required commit statuses and
check runs, strict up-to-date checks, draft/conflict readiness, administrator
enforcement, linear-history merge-method enforcement, and force-push/deletion
enforcement through the Git References API.

Rules that depend on domain models the emulator does not currently have are
rejected explicitly when enabled: code owners, resolved conversations, commit
signatures, deployments, actor restrictions/bypass lists, branch patterns,
merge queues, branch creation locks, branch locks, and fork syncing. Direct
smart-HTTP force-push/deletion rejection remains unsupported; smart-HTTP pushes
do update PR head state and trigger readiness reevaluation after acceptance.

The GraphQL schema exposes `Repository.mergeQueue(branch:)` and returns `null`
when no merge queue is configured, matching GitHub and preventing Fullsend from
misidentifying an unsupported field error as an active queue. Enabling merge
queues remains explicitly unsupported through the branch-protection API.

## Implementation

- Added persisted branch-protection settings and SQLite compatibility upgrades.
- Added GitHub-shaped branch-protection REST endpoints, granular status-check,
  review, and administrator-enforcement endpoints, plus explicit validation
  errors for unsupported rules.
- Added one shared readiness evaluator for GraphQL state, REST/web merges, and
  queued auto-merges. It evaluates reviews, stale approvals, last-pusher
  approval, statuses/check runs, strict base ancestry, conflicts, drafts,
  administrator bypass, and linear-history merge methods.
- Removed the global `ready-for-merge` label requirement. Auto-merge requests
  stay queued while blocked and are reevaluated after review, review dismissal,
  status, check-run, push/ref, and protection-policy changes.
- Synchronized branch and open-PR head metadata after Git References API and
  smart-HTTP updates, and enforced protected force updates/deletions through
  the Git References API.
- Kept merged PRs represented by the existing `merged` field while returning
  `CLEAN` from GitHub's `MergeStateStatus` vocabulary; closed unmerged PRs
  return `UNKNOWN`.
- Added the GitHub CLI fields used by `gh pr view` and `gh pr merge --auto`,
  including auto-merge enabler/email data and nested pull-request commit nodes.
- Synchronized the persisted base-branch SHA after REST, web, and queued
  auto-merges so branch APIs agree with the actual bare Git ref.

## Verification

- `/tmp/github-emulator-test-venv/bin/python -m pytest tests/test_branch_protection_readiness.py tests/test_branches_api.py tests/test_auto_merge.py tests/test_pulls_api.py tests/test_graphql.py -q -x`:
  57 passed.
- `/tmp/github-emulator-test-venv/bin/python -m pytest tests/ -q`:
  322 passed after the final compatibility fixes.
- `/tmp/github-emulator-test-venv/bin/python -m pytest tests/test_graphql.py tests/test_branch_protection_readiness.py tests/test_auto_merge.py tests/test_pulls_api.py -q`:
  50 passed.
- `make host-rebuild-github` rebuilt, imported, and successfully rolled out the
  integrated K3s deployment.
- Live repaired-flow validation: issue #30 produced Code run 979 and PR #31;
  auto-merge stayed `BLOCKED` until review #58 from `fullsend-review[bot]`, then
  merged as `3351f4c0b66dcb76b65f345976f68ec8291b515b`.
- Live uninterrupted validation: issue #34 automatically produced Triage run
  999, Code run 1003, and PR #35. The Code post-script logged
  `Auto-merge: enabling on PR #35 (--squash)`; GraphQL showed the request owned
  by `fullsend-code[bot]` in `BLOCKED`/`REVIEW_REQUIRED` state. Review run 1005
  approved as `fullsend-review[bot]`, and the request merged automatically as
  `15f8a771a4ab71a53c09434e4fa94adee2363c99`.

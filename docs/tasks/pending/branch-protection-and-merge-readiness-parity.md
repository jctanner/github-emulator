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

- [ ] A protected branch can be configured through the supported API and/or
      admin UI, persisted, inspected, updated, and removed.
- [ ] `mergeStateStatus` distinguishes at least draft, merged, clean,
      blocked, conflicting, and unknown states based on actual PR state and
      configured requirements.
- [ ] Required reviews, required checks, conflicts, and other enabled rules
      prevent ordinary and auto-merges until satisfied.
- [ ] Satisfying all configured requirements allows an enabled auto-merge to
      complete without requiring a synthetic label.
- [ ] An enabled auto-merge remains represented by `autoMergeRequest` until it
      merges or is explicitly disabled.
- [ ] Review, check, push, deployment, and policy changes reevaluate pending
      auto-merges without creating duplicate merges or event loops.
- [ ] `gh pr merge --auto` works against the emulator with the same request
      semantics used by Fullsend.
- [ ] REST and GraphQL responses match GitHub-shaped success and error
      behavior for supported fields; unsupported fields are explicit and
      tested.
- [ ] Regression tests cover each implemented protection rule, readiness
      transitions, auto-merge transitions, and conflicting/unknown cases.
- [ ] A Fullsend-style end-to-end test demonstrates: PR created → auto-merge
      queued → requirements unmet → requirements satisfied → merged.
- [ ] The Fullsend code harness passes `CODE_AUTO_MERGE` and its merge method
      to the runner-side post-script, and the end-to-end test verifies that the
      request is actually queued.

## Likely Files/Areas

- `app/models/`
- `app/api/`
- `app/graphql/`
- `app/services/`
- `app/admin/` and `app/web/`
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

Pending

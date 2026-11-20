# Project Plan

This repository uses the filesystem-native work ledger described in
`docs/agentic_work_ledger.md`.

## Current Milestone

Actions visibility and runner evaluation.

## Plans

- `docs/plans/000-overview.md`
- `docs/plans/actions-visibility-roadmap.md`
- `docs/plans/api-client-frontend-migration.md`

## Pending Tasks

- `docs/tasks/pending/actions-runner-compose-bootstrap.md`
- `docs/tasks/pending/actions-job-execution-loop.md`
- `docs/tasks/pending/actions-hosted-runner-feasibility.md`

## Active Tasks

- `docs/tasks/current/fullsend-per-repo-dispatch-support.md`

## Completed Tasks

- `docs/tasks/done/graphql-review-threads-and-minimize.md`
- `docs/tasks/done/graphql-update-refs.md`
- `docs/tasks/done/org-actions-variables.md`
- `docs/tasks/done/fullsend-app-lookups-and-actions-lists.md`
- `docs/tasks/done/live-issue-and-pull-updates.md`
- `docs/tasks/done/web-ui-reload-in-place.md`
- `docs/tasks/done/web-ui-markdown-rendering.md`
- `docs/tasks/done/issue-cross-references.md`
- `docs/tasks/done/pull-request-web-review-submission.md`
- `docs/tasks/done/org-membership-api.md`
- `docs/tasks/done/admin-organization-owner.md`
- `docs/tasks/done/repository-home-last-commit-columns.md`
- `docs/tasks/done/graphql-user-contributions-collection.md`
- `docs/tasks/done/pull-detail-request-latency.md`
- `docs/tasks/done/pat-last-used-write-amplification.md`
- `docs/tasks/done/api-client-frontend-migration.md`
- `docs/tasks/done/architectural-maintainability-followups.md`
- `docs/tasks/done/admin-ui-and-http-middleware.md`
- `docs/tasks/done/route-and-actions-modularization.md`
- `docs/tasks/done/versioned-database-migrations.md`
- `docs/tasks/done/sqlite-write-policy.md`
- `docs/tasks/done/test-architecture-and-contracts.md`
- `docs/tasks/done/src-layout-reorganization.md`
- `docs/tasks/done/repository-github-apps-settings-page.md`
- `docs/tasks/done/repository-collaborator-settings-page.md`
- `docs/tasks/done/repository-branch-protection-settings-page.md`
- `docs/tasks/done/repository-general-settings-page.md`
- `docs/tasks/done/repository-owner-selector.md`
- `docs/tasks/done/enterprise-real-actions-runner.md`
- `docs/tasks/done/site-wide-actions-runner.md`
- `docs/tasks/done/admin-runner-management-page.md`
- `docs/tasks/done/branch-protection-and-merge-readiness-parity.md`
- `docs/tasks/done/actions-issue-label-compatibility.md`
- `docs/tasks/done/actions-stale-runner-recovery.md`
- `docs/tasks/done/actions-live-job-logs.md`
- `docs/tasks/done/seed-default-admin-pat.md`
- `docs/tasks/done/actions-visible-api-and-web-ui.md`
- `docs/tasks/done/admin-repos-import-api.md`
- `docs/tasks/done/admin-app-frontend-lifecycle.md`
- `docs/tasks/done/pull-request-web-conversation-controls.md`
- `docs/tasks/done/pull-request-web-labels.md`
- `docs/tasks/done/issue-conversation-timeline-parity.md`
- `docs/tasks/done/pull-request-commits-and-files-navigation.md`
- `docs/tasks/done/repository-hygiene-and-locked-dependencies.md`

## Milestones

- `docs/milestones/M1-skeleton-auth-repo-crud-git-http.md`
- `docs/milestones/M2-core-rest-api.md`
- `docs/milestones/M3-additional-rest-api.md`
- `docs/milestones/M4-graphql-api.md`
- `docs/milestones/M5-admin-frontend.md`
- `docs/milestones/M6-actions-api-surface.md`
- `docs/milestones/M7-refinements.md`
- `docs/milestones/M8-gap-fixes.md`
- `docs/milestones/M9-infrastructure-tooling.md`

## Notes

- `docs/notes/test-coverage-summary.md`

## Open Bugs

- `docs/bugs/open/git-commits-endpoint-returns-only-the-subject.md`
- `docs/bugs/open/stale-actions-runner-registrations-accumulate.md`
- `docs/bugs/open/contents-commit-leaves-pull-request-head-stale.md`
- `docs/bugs/open/graphql-repository-issues-include-pull-requests.md`
- `docs/bugs/open/graphql-write-authorization.md`
- `docs/bugs/open/rest-pull-request-issue-routes-require-issues-scope.md`
- `docs/bugs/open/tag-ref-changes-raise-no-events.md`
- `docs/bugs/open/graphql-installation-content-scopes.md`
- `docs/bugs/open/graphql-contributions-count-hidden-activity.md`
- `docs/bugs/open/job-tokens-cannot-use-graphql.md`

## Fixed Bugs

- `docs/bugs/fixed/graphql-issue-sub-issues-unknown.md`
- `docs/bugs/fixed/graphql-pull-request-comment-and-issue-input.md`
- `docs/bugs/fixed/graphql-app-authors-are-typed-as-users.md`
- `docs/bugs/fixed/close-issue-ignores-the-state-reason.md`
- `docs/bugs/fixed/issue-closed-by-pull-requests-references-is-empty.md`
- `docs/bugs/fixed/runner-agent-routes-are-not-scoped-to-the-runner.md`
- `docs/bugs/fixed/actions-write-routes-do-not-check-the-repository-role.md`
- `docs/bugs/fixed/job-server-routes-do-not-validate-the-job-token.md`
- `docs/bugs/fixed/notice-commands-stay-raw-on-the-upstream-runner.md`
- `docs/bugs/fixed/python-runner-uses-a-fixed-bash.md`
- `docs/bugs/fixed/gh-in-upstream-runner-steps-calls-github-com.md`
- `docs/bugs/fixed/upstream-runner-logs-land-on-the-wrong-job.md`
- `docs/bugs/fixed/upstream-runner-job-outputs-are-empty.md`
- `docs/bugs/fixed/rest-labels-state-reason-and-hash-algorithm-gaps.md`
- `docs/bugs/fixed/installation-token-revoke-route-missing.md`
- `docs/bugs/fixed/json-body-refused-without-a-json-content-type.md`
- `docs/bugs/fixed/rate-limit-is-fixed-at-5000.md`
- `docs/bugs/fixed/repository-private-and-visibility-drift.md`
- `docs/bugs/fixed/author-can-approve-their-own-pull-request.md`
- `docs/bugs/fixed/app-bot-permission-is-not-found.md`
- `docs/bugs/fixed/collaborator-permission-of-a-user-without-access-is-404.md`
- `docs/bugs/fixed/compare-returns-an-empty-merge-base.md`
- `docs/bugs/fixed/contents-update-ignores-the-sha-precondition.md`
- `docs/bugs/fixed/issue-create-drops-unknown-labels.md`
- `docs/bugs/fixed/duplicate-creates-do-not-report-already-exists.md`
- `docs/bugs/fixed/team-membership-routes-missing.md`
- `docs/bugs/fixed/graphql-repository-visibility.md`
- `docs/bugs/fixed/graphql-pull-request-payload-missing-greenlet.md`
- `docs/bugs/fixed/git-push-does-not-synchronize-pull-requests.md`
- `docs/bugs/fixed/graphql-create-commit-on-branch-is-missing.md`
- `docs/bugs/fixed/job-log-page-polls-forever-and-pull-list-runs-git-per-row.md`
- `docs/bugs/fixed/checkout-ignores-sparse-checkout-cone-mode.md`
- `docs/bugs/fixed/login-page-opens-a-basic-auth-dialog.md`
- `docs/bugs/fixed/repository-webhooks-are-never-delivered.md`
- `docs/bugs/fixed/pagination-links-use-the-internal-host.md`
- `docs/bugs/fixed/pagination-links-drop-query-parameters.md`
- `docs/bugs/fixed/deleted-repository-leaves-its-history-for-the-next-id.md`
- `docs/bugs/fixed/conversation-description-and-comment-timestamps.md`
- `docs/bugs/fixed/merged-pr-does-not-close-linked-issues.md`
- `docs/bugs/fixed/busy-real-runner-claims-second-job.md`
- `docs/bugs/fixed/graphql-pull-request-create-does-not-dispatch-actions.md`
- `docs/bugs/fixed/concurrent-repository-mutations-return-500-on-sqlite-lock.md`
- `docs/bugs/fixed/issue-comment-web-route-does-not-dispatch-actions.md`
- `docs/bugs/fixed/fullsend-installation-token-attribution-and-review-loop.md`
- `docs/bugs/fixed/cancelled-run-leaves-active-runner-job.md`
- `docs/bugs/fixed/branch-selector-lists-only-the-first-100-branches.md`
- `docs/bugs/fixed/users-and-organizations-share-ids.md`
- `docs/bugs/fixed/python-runner-skips-tls-verification.md`
- `docs/bugs/fixed/installation-token-narrowing-by-repository.md`
- `docs/bugs/fixed/actions-oidc-claims-and-discovery-differ-from-github.md`
- `docs/bugs/fixed/raw-githubusercontent-requests-are-not-served.md`
- `docs/bugs/fixed/contents-reads-run-several-git-processes.md`
- `docs/bugs/fixed/contents-ignore-the-raw-media-type-and-the-1-mib-limit.md`
- `docs/bugs/fixed/upload-artifact-v4-has-no-results-service.md`
- `docs/bugs/fixed/two-runners-claim-the-same-job.md`
- `docs/bugs/fixed/upstream-runner-keeps-running-a-cancelled-job.md`
- `docs/bugs/fixed/runner-stays-busy-after-its-job-is-deleted.md`
- `docs/bugs/fixed/cancellation-never-reaches-a-runner-that-moved-on.md`
- `docs/bugs/fixed/job-handed-to-an-abandoned-runner-poll.md`
- `docs/bugs/fixed/runner-self-delete-leaves-jobs-pointing-at-it.md`
- `docs/bugs/fixed/app-logs-silenced-after-the-startup-migration.md`
- `docs/bugs/fixed/runners-registering-together-lose-their-tokens.md`
- `docs/bugs/fixed/github-context-identity-values-are-empty.md`
- `docs/bugs/fixed/rendered-github-context-differs-from-github.md`
- `docs/bugs/fixed/container-and-services-jobs-run-on-the-host.md`
- `docs/bugs/fixed/unresolved-reusable-workflow-call-passes.md`
- `docs/bugs/fixed/skipped-reusable-workflow-caller-lists-the-called-jobs.md`
- `docs/bugs/fixed/job-needing-a-reusable-workflow-caller-waits-forever.md`
- `docs/bugs/fixed/actions-in-emulator-repositories-are-fetched-upstream.md`
- `docs/bugs/fixed/jobs-list-shows-jobs-waiting-on-needs.md`
- `docs/bugs/fixed/indexing-a-push-holds-the-write-lock-through-the-reads.md`
- `docs/bugs/fixed/workflow-runs-hold-the-write-lock-for-seconds.md`
- `docs/bugs/fixed/workflow-lookups-resync-the-workflow-table-on-every-request.md`
- `docs/bugs/fixed/connection-pool-caps-concurrent-runners.md`
- `docs/bugs/fixed/writes-fail-after-a-5s-lock-wait.md`
- `docs/bugs/fixed/api-commit-answers-503-after-the-branch-moved.md`
- `docs/bugs/fixed/concurrent-repository-create-returns-500.md`
- `docs/bugs/fixed/concurrent-issues-get-the-same-number.md`
- `docs/bugs/fixed/concurrent-label-create-returns-500.md`
- `docs/bugs/fixed/update-of-deleted-row-answers-500.md`
- `docs/bugs/fixed/comment-on-deleted-issue-500.md`
- `docs/bugs/fixed/failed-post-push-step-drops-the-push-event.md`
- `docs/bugs/fixed/pull-request-workflows-never-run.md`

## Decisions

- `docs/decisions/ADR-0001-actions-runner-strategy.md`
- `docs/decisions/ADR-0002-artifact-storage-on-disk.md`
- `docs/decisions/ADR-0003-org-memberships-are-active-on-add.md`
- `docs/decisions/ADR-0004-actions-results-service-for-artifacts.md`

## CC-43

- [Release workflows](docs/tasks/current/cc-43-releases.md)

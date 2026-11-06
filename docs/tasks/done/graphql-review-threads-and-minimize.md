# Task: GraphQL review threads, resolveReviewThread, minimizeComment (#20)

## Goal

Close [#20](https://github.com/jctanner/github-emulator/issues/20): a review bot reads a pull
request's review threads, resolves the threads it opened that are fixed, and hides its outdated
summaries. GitHub exposes all three only through GraphQL, and the emulator's schema had none of
them. Fullsend's review agent (`agents/scripts/lib/github-review-ops.lib.sh`) and CLI
(`internal/forge/github/github.go`, `internal/cli/postreview.go`) use them on every re-review;
against the emulator they logged a warning and silently skipped the work.

## Changes

### Storage (migration `0014_review_threads_and_minimized_comments`)

All new columns are nullable; NULL means unresolved or not minimized, so existing rows keep
their meaning.

- `pr_review_comments.thread_resolved_at`, `thread_resolved_by_id` (FK `users.id`): a thread's
  resolved state, on its root comment.
- `minimized_reason` on `issue_comments`, `pr_review_comments`, and `reviews`, in GitHub's
  `minimizedReason` spelling (`outdated`, `off-topic`, ...).

### GraphQL

- `PullRequest.reviewThreads`: a connection of `PullRequestReviewThread`, built from review
  comments. A comment with no `in_reply_to_id` is a root; replies are followed back to their root.
  Fields: `id`, `isResolved`, `isOutdated`, `isCollapsed`, `viewerCanResolve`,
  `viewerCanUnresolve`, `viewerCanReply`, `path`, `diffSide`, `line`, `originalLine`,
  `startLine`, `originalStartLine`, `startDiffSide`, `resolvedBy`, and `comments`.
- `PullRequestReviewComment` (new type): `id` (equal to the REST `node_id`), `databaseId`,
  `author`, `body`, `path`, `createdAt`, `updatedAt`, `line`, `originalLine`, `diffHunk`, `url`,
  `outdated`, `viewerDidAuthor`, plus the `Minimizable` fields.
- `Minimizable` interface (`isMinimized`, `minimizedReason`, `viewerCanMinimize`,
  `viewerCanUnminimize`), implemented by `IssueComment`, `PullRequestReviewComment`, and reviews
  (`ReviewType`).
- Mutations: `resolveReviewThread`, `unresolveReviewThread`, `minimizeComment`,
  `unminimizeComment` (accepts issue comment, review comment, and review node ids, including REST
  `node_id` values). Unknown or unreadable ids are `NOT_FOUND`; missing permission is `FORBIDDEN`.

### Authorization (`src/app/graphql/authz.py`)

REST scopes installation and job tokens at the authentication chokepoint by the repository in
the URL, which `/api/graphql` lacks. `authz.may_read` and `authz.may_write` take the repository
the resolver found:

- An installation token reaches only the repositories it was minted for, and writes only under
  its own permissions: resolving needs `pull_requests: write`; minimizing needs
  `pull_requests: write`, or `issues: write` for an issue comment (either for a pull request's
  conversation comment). Fullsend's review App (`contents: read`, `pull_requests: write`) can
  therefore resolve and minimize; `contents: write` alone cannot.
- A job token writes only under its job's declared permissions, in its own repository.
- A user credential needs write access (owner, site admin, collaborator with push); an App's bot
  user gets its installation's grants; the pull request's author may also resolve.
- `viewerCan*` fields answer with the same rules. Threads of a repository the caller cannot read
  are not returned.

### Outdated threads

A comment on the pull request's head commit is current. Otherwise a new-side (RIGHT) line
comment is followed from its commit to the head through the file's diff (`difflib`), so lines
added or removed above it move it (`line` reports where it is now) and only a change to the line
itself outdates it. An outdated thread reports `line: null` and keeps `originalLine`. File
comments, and old-side (LEFT) comments, are outdated when their file differs at the head; GitHub
follows LEFT comments through the base, which the emulator does not. Outdated state is computed
only for the threads and comments a query returns, with blobs cached per request.

### Deleting a thread's root

`DELETE .../pulls/comments/{id}` keeps the thread together: replies to a deleted reply move up to
its parent, and when a root goes, its oldest reply becomes the root and keeps the resolved state.

## Not done

- `startLine`, `originalStartLine`, and `startDiffSide` are always null: REST review comments do
  not store `start_line` or `start_side`, so every thread is single-line.
- GraphQL-created threads (`addPullRequestReviewThread`, `addPullRequestReviewThreadReply`), as
  the issue scoped out; clients create them through REST.
- Reviews keep the emulator's existing GraphQL type name `ReviewType` rather than GitHub's
  `PullRequestReview`, so a `... on PullRequestReview` fragment does not match.

## Review (Codex CLI)

Codex reviewed the first version and found seven issues; all are addressed and tested: private
threads were readable without access (P1); the mutations checked the user's role, not the
presented token's grant, and required `contents: write`, which Fullsend's review App lacks (P1);
`viewerCanUnminimize` was missing; a moved line was reported outdated; every thread's outdated
state was computed even for one page; the minimize payload's review comment lacked its context
(`viewerDidAuthor`, `url`); and deleting a thread's root split the thread and broke its id.

A second pass confirmed those fixes and found three more, also fixed and tested: an installed
App could not read a private repository at all, because `repository_access.can_read` had no case
for App bots (this affected REST too; a covering installation now grants read, as GitHub's
metadata permission does); an installation token covering the repository read its review threads
without any pull request permission (threads now need `pull_requests` read; a missing grant on a
visible repository is `FORBIDDEN`, an invisible one `NOT_FOUND`); and promoting a reply to root
lost the thread's location (the promoted root now takes the deleted root's path, line, side, and
commits).

## Found along the way

- `docs/bugs/open/contents-commit-leaves-pull-request-head-stale.md`: a contents-API commit does
  not move open pull requests' `head_sha`, so their threads are not marked outdated. The test moves
  the head through the refs API, as a push would.
- `docs/bugs/open/graphql-repository-visibility.md`: GraphQL `repository` and `node` return a
  private repository's data to anyone (confirmed anonymously). The new thread fields check
  visibility themselves; the general fix is left for a decision because it changes what existing
  GraphQL callers see.
- `docs/bugs/open/job-tokens-cannot-use-graphql.md`: a job token's `POST /api/graphql` is refused
  at the chokepoint, so no job token can run a GraphQL mutation. `authz` already handles job tokens
  for when that is lifted.

## Evidence

- `tests/test_review_threads_graphql.py` (17 tests): grouping, pagination, outdated detection
  after a head change and after lines move, resolve and unresolve, permissions for users,
  installation tokens (Fullsend's review-App grant, a `contents`-only grant, another repository's
  token) and job tokens, private-repository hiding, reply ids rejected as threads, minimizing all
  three subject types by REST `node_id`, `viewerCanUnminimize`, the minimize payload's fields, the
  reasons' spelling, and root deletion.
- `tests/test_database_migrations.py::test_review_thread_columns_upgrade_keeps_existing_rows`.
- On a fresh SQLite database, upgrade to `0014`, downgrade to `0013`, and re-upgrade leave the
  columns, the foreign key, and no `_alembic_tmp_*` tables; the upgraded columns match the models.
- Fullsend's five GraphQL documents (the Go `ListPullRequestReviewThreads` and `MinimizeComment`
  queries and the three in `github-review-ops.lib.sh`) validate against the schema.

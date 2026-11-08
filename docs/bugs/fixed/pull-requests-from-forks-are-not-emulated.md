# Bug: pull requests from forks are not emulated

Status: fixed 2026-11-08.

## Summary

A GitHub pull request may take its head from another repository in the
base's fork network. Fullsend's dispatch tells such a pull request apart by
comparing `pull_request.head.repo.full_name` with the base's, and its
behaviour suite opens one with GraphQL `createPullRequest` and
`headRepositoryId` (fork-dispatch, fork-bash-routing). The emulator had no
cross-repository pull requests at all:

- `CreatePullRequestInput` had no `headRepositoryId`, so the mutation was
  rejected; REST read `owner:branch` heads only from the base repository.
- REST pull requests answered `head.repo` and `base.repo` as `null`
  ("Simplified"), and the Actions/webhook payload had no `repo` under `head`
  or `base` at all. Every pull request therefore looked the same to a fork
  check, fork or not.
- The payload's `repository.owner` for an organization's repository was the
  member who created it, not the organization.
- A fork into an organization (`POST /repos/{o}/{r}/forks` with
  `organization`) was recorded as owned by the calling user, and no one
  checked that the caller could create repositories there. Asking again for
  an existing fork answered 422 instead of returning it.
- A commit through the contents API was not a push: no `push` activity, no
  `synchronize` for a pull request from that branch, and the pull request's
  head stayed at the old commit. `DELETE /repos/{o}/{r}/contents/{path}` was
  a stub that deleted nothing.

## Fix

- `app/services/cross_repo_pulls.py`: the fork network, resolving REST's
  `owner:branch` (and `head_repo`) to a fork, and `sync_head_ref`, which
  keeps the head as `refs/pull/{number}/head` in the base repository.
- GraphQL `createPullRequest` takes `headRepositoryId`; REST
  `POST /pulls` takes another owner's `owner:branch`. Both refuse a
  repository outside the network or a missing branch.
- Git operations on a fork's pull request (files, commits, diff, merge,
  auto-merge, closing references) use `refs/pull/N/head`
  (`pr.git_head_ref`); the merge clone fetches that ref.
- REST and payloads carry `head.repo` and `base.repo`; payload repositories
  carry `fork`, `clone_url`, and the organization as owner.
- A push to a fork's branch moves its pull requests in the base repository
  and raises `pull_request_target` `synchronize` there. Same-repository head
  updates (git push, ref updates, `updateRefs`) no longer move a fork's pull
  request whose branch merely shares a name.
- Forks into an organization are the organization's (`owner_type`,
  `organization_id`), need membership or an App with
  `administration: write`, and are returned when they already exist.
- `app/services/api_push.py`: a contents-API commit is processed as a push.
  `DELETE …/contents/{path}` commits the deletion, requiring the current
  blob `sha` (409 when stale).

## Evidence

`tests/test_cross_repo_pulls.py`: an organization fork; a member-only fork;
the GraphQL fork pull request (head and base repositories, label, files,
commit count); synchronize on a push to the fork, with the payload naming
both repositories and the organization as owner; a same-named base branch
leaving the fork's pull request alone; REST from a user's fork; a repository
outside the network; a same-repository pull request reporting its
repository; a fork pull request merging; an installation token refused a
head on another account. `tests/test_contents_api.py` and
`tests/test_forks_api.py` now expect GitHub's behaviour (a real deletion with
a current sha; an existing fork returned), and
`tests/actions/test_consolidation_bug_fixes.py` clears the run its setup
commit now starts.

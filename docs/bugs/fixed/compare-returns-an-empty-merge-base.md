# Bug: REST compare returns an empty merge base and no commits

Status: fixed 2026-11-14.

## Summary

On GitHub, `GET /repos/{owner}/{repo}/compare/{base}...{head}` returns:

- `base_commit` and `merge_base_commit` as full commit objects (`sha`,
  `commit`, `parents`, ...);
- `ahead_by`, `behind_by` and `total_commits`;
- `status`: `ahead`, `behind`, `diverged` or `identical`;
- `commits` on head since the merge base, oldest first;
- `files` changed from the merge base to head.

Either side may be a branch, a SHA, or `owner:branch` naming a fork in the
network.

The emulator's endpoint was a stub. For `main...feat`, with `feat` one commit
ahead, it returned `base_commit: {}`, `merge_base_commit: {}`, `ahead_by: 0`,
`behind_by: 0`, no commits, no files, and always `status: "ahead"`.
Fullsend's behaviour tests read `merge_base_commit.sha`.

## Fix

The endpoint now resolves both sides in the repository and uses new git
helpers in `src/app/git/bare_repo.py` (`get_merge_base`,
`get_ahead_behind`), plus the existing `get_compare_diff` for files.

- An `owner:branch` side for another owner is resolved to that owner's fork
  in the network, if the caller can read it. Its branch's objects are fetched
  into the base repository, without a ref, so the comparison runs there
  (`cross_repo_pulls.fetch_branch_objects`).
- Without `page` or `per_page`, up to 250 commits are listed. With them, the
  commits are paginated and `files` appear on the first page only, as on
  GitHub.
- An unknown ref, or a path without `...`, is `404 Not Found`. Unrelated
  histories are `404 No common ancestor between base and head.`
- `get_commit` and compare share one helper for the per-file URLs.

Either side may name a branch with `/` in it (`main...feature/x`,
`main...owner:fix/y`). A paginated comparison sends a `Link` header to the
other pages of `commits`, as any GitHub list does.

## Tests

- `tests/test_compare_api.py`
  (`test_compare_reports_the_merge_base_and_commits_ahead`,
  `test_compare_reports_behind_diverged_and_identical`,
  `test_compare_accepts_shas_and_the_owner_prefix`,
  `test_compare_with_a_fork_branch`,
  `test_compare_with_an_unknown_ref_is_not_found`,
  `test_compare_with_slashed_branch_names`,
  `test_a_fork_branch_with_a_slash_compares`,
  `test_compare_pages_link_to_each_other`).

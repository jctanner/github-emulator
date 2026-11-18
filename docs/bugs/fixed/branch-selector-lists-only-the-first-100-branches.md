# Bug: the branch selector lists only the first 100 branches

## Summary

In a repository with more than 100 branches, the web UI's "Switch branches"
menu and the Branches page showed only the first 100 by name. Their filters
could not find later branches, such as `branch-120` in a repository with
`branch-000` through `branch-149`. Those branches could be reached only by
typing `/{owner}/{repo}/tree/{branch}`. Reported in GitHub issue #25.

## Cause

`BranchSelector.tsx` and `BranchesPage.tsx` each made one request,
`GET /api/v3/repos/{owner}/{repo}/branches?per_page=100`, and filtered the
result on the client. The branches API caps `per_page` at 100 and sorts by
name, so anything after the first page was never loaded.

## Fix

A shared helper, `loadAllBranches` in `src/frontend/src/api/branches.ts`,
requests `page=1,2,...` with `per_page=100` and stops at the first page that
returns fewer than 100 branches. This is the same loop `src/api/reviews.ts`
uses for reviews. Both components now use the helper. A repository with
exactly 100 branches makes one more request, which returns an empty page.

Regression tests: `src/frontend/src/components/BranchSelector.test.tsx` and
`src/frontend/src/pages/BranchesPage.test.tsx` serve 150 branches over two
pages and check that filtering finds a branch from page 2. The Branches page
test also covers exactly 100 branches.

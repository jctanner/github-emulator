# Pagination links drop the request's query parameters

## Symptom

The `Link` header's `next`, `prev`, `first` and `last` URLs keep only `page` and `per_page`.
Every other query parameter of the request is lost. A client following `rel="next"`, as GitHub
clients and Markov's `http_request` `paginate` do, gets the wrong second page:

```
GET /api/v3/repos/experiment/markov-test-repo/issues?state=all&per_page=100
Link: <https://github.local/api/v3/repos/experiment/markov-test-repo/issues?page=2&per_page=100>; rel="next", ...
```

Page 2 of that link is page 2 of *open* issues (the default `state`), not of all issues, so a
paged list silently mixes two different queries. Filters such as `labels`, `since`, `sort`,
`direction`, `assignee` and `creator` are dropped the same way.

Real GitHub keeps every parameter of the request in its pagination links and changes only
`page`.

## Cause

The link builders take the request URL without its query and add `page`/`per_page` back:

- `src/app/api/issues.py:139` `_pagination_links` (`str(request.url).split("?")[0]` at line 140)
- `src/app/api/repos.py:139` `_pagination_links` (line 143)
- `src/app/api/comments.py:86`
- `src/app/api/review_comments.py:114`
- `src/app/api/pulls.py:615`

`src/app/services/import_service.py` only reads `rel="next"` from an upstream GitHub's `Link`
header (`_get_next_link`); it builds none, so it is not affected.

## Fix

Build each link from the request's own query parameters, replacing `page` (and setting
`per_page`) and keeping the rest, e.g. `request.url.include_query_params(page=n, per_page=pp)`
in Starlette. One shared helper for all the builders above would stop the copies drifting.

## Regression coverage

A test per endpoint family (issues, issue comments, pulls, review comments, repos) that sends a
filter and `per_page` small enough to need two pages, then checks that:

- every link (`next`, `prev`, `first`, `last`) keeps the filter and other parameters;
- following `next` gives the second page of the filtered list (e.g. with `state=all` and closed
  issues on page 2).

## Found by

Markov plan 006 (`markov/.ledger/plans/006-idempotent-codified-workflows.md`, phase 2): a
150-issue fixture read with `state=all` and `paginate: true`.

## Status

Fixed (2026-10-24).

## Resolution

`src/app/api/pagination.py` `pagination_links` builds every link with
`request.url.include_query_params(page=..., per_page=...)`, so the request's other parameters
are kept and only `page` and `per_page` change. The five builders listed above now call it. The
issue-comment and review-comment lists, which emitted only `next` and `prev`, now emit `first`
and `last` too, as the other lists and GitHub do.

## Verification

- `tests/test_pagination_links.py`: one test per endpoint family (issues, pulls, issue comments,
  review comments, user repos). Each sends filters with a small `per_page`, checks that every
  link keeps them, and follows `next` to the rest of the filtered list (closed issues and pulls
  under `state=all`). All five fail against the previous builders and pass with the fix.
- Full suite passing (see the task commit).

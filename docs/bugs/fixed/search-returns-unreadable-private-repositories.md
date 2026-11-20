# Bug: search returns private repositories the caller cannot read

Status: fixed 2026-11-20 (github-emulator#93).

## Summary

GitHub's search only returns results from repositories the caller can read.
The REST search endpoints (`src/app/api/search.py`) did not:

| Endpoint | Filter before the fix |
|---|---|
| `/search/repositories` | Private repositories hidden from anonymous callers only; any signed-in user found every one. |
| `/search/issues` | None, anonymous callers included. |
| `/search/code` | None, including with a `repo:` qualifier naming a private repository. |
| `/search/commits` | None, including with a `repo:` qualifier. |

Since github-emulator#92 a job token's searches were narrowed to its own
repository plus public ones; every other credential saw everything.

## Fix

`app.services.repository_access.readable_repositories` is the query form of
`can_read`, a condition over `Repository` applying the same rules: public
repositories, and for a signed-in user the private ones they own,
collaborate on, are an active member of the organization of, or whose App
installation covers them; a site admin reads everything. An installation
token is further held to the repositories it was minted for, and a job token
keeps #92's rule (`job_token_scope.job_token_condition`), whoever its run's
actor is.

All four search endpoints apply it before counting and paging, so
`total_count` counts readable results only. A `repo:` qualifier naming a
repository the caller cannot read answers `total_count: 0`, as for a
repository that does not exist.

Organization base permissions are not modelled separately: an active member
reads the organization's private repositories, which is what `can_read`
already decides for direct access, so search and direct access agree.

Tests: `tests/test_search_visibility.py` (non-member, anonymous,
collaborator, organization member and pending member, owner, site admin,
installation tokens, and `repo:` qualifiers); the job-token search test in
`tests/actions/test_pull_request_event.py` still passes.

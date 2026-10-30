# Job log page polls forever; pull list runs Git per row

## Symptom

Every page that lists issues or pull requests took 10–16 seconds, and the
container sat at its 500m CPU limit with no one using the UI:

| request | time |
| --- | --- |
| `GET /repos/{o}/{r}/issues?state=all&per_page=30` | 15.1 s |
| `GET /repos/{o}/{r}/pulls?state=all&per_page=30` | 15.2 s |
| `GET /repos/{o}/{r}/actions/jobs/{id}/logs` (39 KB) | 9.1 s |

The database was not the cause: the issue list query itself took 1 ms.

## Cause

Two independent faults.

1. **The job page polled its log without end.**
   `src/frontend/src/pages/ActionJobPage.tsx` fetched the whole log on a fixed
   `setInterval` of 1.5 s for as long as the tab stayed open, including after
   the job completed. The interval did not wait for the previous fetch, so
   once a fetch took longer than 1.5 s requests overlapped and piled up. One
   open tab on a finished job produced about 76 log requests a minute, enough
   to hold uvicorn at the CPU limit and queue every other request behind them.
2. **The pull list computed Git statistics for every row.** `list_pulls` called
   `_attach_pr_stats` per pull request: two Git subprocesses (commit count and
   diff stats), about 0.4 s each. GitHub's list endpoint returns the simple
   pull-request shape, which has no `commits`, `additions`, `deletions` or
   `changed_files`.

## Fix

- The job page polls only while the job is not `completed`, reading the job's
  status alongside the log, and schedules each poll after the previous one
  finishes.
- `list_pulls` no longer computes the four counts, and `_pr_json(...,
  include_stats=False)` leaves them out of list responses. Single pull request
  responses (get, create, update) still carry them.

After the fix: issue list 0.37 s, job log 0.12 s, pull list 3.2 s.

## Regression coverage

- `src/frontend/src/pages/ActionJobPage.test.tsx`: a completed job's log is
  fetched once and polling stops; a running job is polled until it completes,
  then polling stops. Both fail against the previous component.
- `tests/test_response_counters.py::test_pr_list_omits_git_counters`, and the
  two counter tests now assert the counts on single pull request responses
  only.

## Follow-up: ref resolution

The pull list still ran `normalize_branch_ref` twice per row in
`_attach_resolved_refs` (one `git rev-parse --verify` per candidate ref,
about 0.1 s per pull request, 3.2 s for 30). `_attach_resolved_refs_many` now
resolves every row's head and base with one `git cat-file --batch-check`
process (`resolve_refs`, `normalize_branch_refs` in `src/app/git/bare_repo.py`),
and the single-PR path uses the same code with one item. The 30-row list then
took 0.11–0.18 s.

`tests/test_batch_ref_resolution.py` checks that the batched resolver returns
exactly what `resolve_ref` / `normalize_branch_ref` return for branches,
`refs/heads/...`, tags, full and short SHAs, `owner:branch` labels (own and
foreign owner), missing names and the empty ref.

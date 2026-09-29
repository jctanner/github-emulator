# A deleted repository leaves its history behind, and the next repository inherits it

Status: fixed 2026-09-29 (migration 0011, `repository_purge`).

## Symptom

`experiment/testrepo2` was created on 2026-09-29 at 13:31 and its Actions
page listed 22 runs, 15 of them from 2026-09-27. Run 1 was a scaffold pull
request whose head commit returned 404 in the repository. The runs were not
another repository's leaking through a filter: the API and the page both
filtered by repository id correctly. The id was the problem.

## Cause

Two things, which only bite together.

1. **Deleting a repository removed the row and the ORM's cascades** (issues,
   labels, milestones, branches, collaborators, stars) **and nothing else.**
   Workflows, runs, jobs, artifacts, secrets, variables, pull requests,
   checks, releases, webhooks and deploy keys all stayed, keyed on a
   repository id that no longer existed. SQLite does not enforce foreign
   keys on these connections, so the delete succeeded.
2. **The repositories table had no `AUTOINCREMENT`.** SQLite then hands out
   `max(id) + 1`, so once the highest-numbered repository is deleted, the next
   one created gets its id, and every row the deletion left behind reads as
   the new repository's own.

The 2026-09-27 rows came from throwaway repositories used to verify the
onboarding button, deleted afterwards. The census on the live database before
the fix: 277 runs across 12 deleted repositories, 30 workflows, 2 secrets and
3 variables with no repository; and on id 428 specifically, 15 runs, 150
jobs and 3 artifacts from before the repository existed. The secrets and
variables on 428 were the ghost's, created two days before the repository;
the onboarding CLI's "set" then wrote the same names, so they were live
configuration by the time anyone looked.

## Fix

- `app/services/repository_purge.py` derives the delete plan from the table
  metadata rather than a hand-kept list, so a table added later that hangs
  off a repository is purged the day it appears. A column named `repo_id`
  that references `repositories.id` owns its rows; any other reference (a
  fork's `parent_id`, a pull request's `head_repo_id`) is a pointer and is
  set to NULL, which is what GitHub shows for a fork whose parent is gone.
  Below the repository, a row belongs to what it must name (a job to its
  run) and merely points at what it may leave empty (a job to its runner).
  `delete_repo` runs the plan and expunges the ORM row instead of deleting
  it, so the cascades do not chase rows the purge already took.
- `Repository.__table_args__` sets `sqlite_autoincrement`, so a fresh
  database never reuses an id.
- Migration 0011 purges rows owned by a repository that no longer exists,
  using the same plan over the reflected schema, then rebuilds the
  repositories table with `AUTOINCREMENT`, which seeds `sqlite_sequence`
  with the highest id present.

Rows that predate their own repository on a reused id are not touched by the
migration: by id they are indistinguishable from history, and a snapshot
restore could legitimately carry old timestamps. On this stack the 15 ghost
runs on id 428, their 150 jobs and 3 artifacts were removed by hand after the
migration ran; the ghost workflow and configuration rows were kept because
the live repository's runs already point at them.

## Evidence

- `tests/test_repository_purge.py`: a deleted repository's workflows, runs,
  jobs, artifacts, secrets and variables are gone and its neighbour's are
  untouched; nothing in any table names the deleted id; a deleted id is
  never handed out again; the plan owns by `repo_id`, clears pointers, and
  orders children before parents.
- `tests/test_database_migrations.py`: a pre-0011 database with an orphaned
  run, job, secret and variable and a fork of a missing parent comes out of
  the upgrade without them, with the parent pointer cleared, the table
  rebuilt with `AUTOINCREMENT`, and the sequence at or above the highest id.

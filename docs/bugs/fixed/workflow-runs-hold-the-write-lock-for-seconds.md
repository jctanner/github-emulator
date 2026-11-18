# Bug: creating or finishing a workflow run holds SQLite's write lock for seconds

Status: fixed 2026-11-15.

## Summary

SQLite has one writer. From a transaction's first write to its commit,
every other write waits. Under Fullsend's behaviour tests and the
reliability runs, these held the lock for 2–13 s each:

- an issue, pull request, label or comment that starts a workflow run;
- a job finishing, which promotes the jobs waiting on it;
- a push, which reindexes the repository for search.

They held it that long because of what ran inside the window:

| Path | Inside the window | Cause |
|---|---|---|
| Run creation | 64 statements for one run of 13 jobs; 50 of them reads | The variables and secrets were read after the run's INSERT. Each read loaded a Repository or User, and those load eagerly: every issue, label, branch and collaborator of the repository, and every repository of the run's actor |
| Job promotion | 68 statements for 12 jobs | One concurrency query per job, each flushing the jobs changed so far, plus the same eager loads |
| Reindexing | One INSERT per file and per commit (990 in one push) | ORM inserts fetch each new id, so they are not batched on SQLite |

## Fix

- Run creation reads everything before its first write; the window holds
  only the run and job INSERTs (14 statements).
- The collections that grow with use load only when a query asks for them:
  a repository's issues, labels, branches, milestones, collaborators and
  stars, and the repositories of a user or organization. Nothing read them
  implicitly except a user's `public_repos`, which now comes from a count.
- `vars` reads only the columns it uses.
- Promotion supersedes other runs' jobs with one query after the loop, so
  the jobs are written in one flush (13 statements).
- Reindexing inserts each table in one executemany (4 statements).

`tests/test_workflow_write_lock_statements.py` pins these counts; the run
and promotion tests fail on the old code.

## Evidence

- An agents behaviour-test run on the emulator: 170 "writer lock held"
  lines over 1 s, max 6491 ms; top holders `POST .../issues`, `.../pulls`
  (first write `INSERT INTO workflow_runs`, 100–166 statements) and
  `git-receive-pack` (`DELETE FROM file_contents`, 990 statements).
- A Fullsend behaviour-test run on the emulator: job completion and run creation at
  150–170 statements per hold.

# Bug: the rendered github context differs from GitHub's

Status: fixed 2026-11-18.

## Summary

Compared with the same workflow on GitHub, the emulator's server-side
rendering of a step's `run:`, `with:` and `env:` differed:

| Expression | GitHub | Emulator |
| --- | --- | --- |
| a `false` value, e.g. `${{ inputs.flag }}` | `false` | `False` |
| a `false` comparison, e.g. `${{ github.event_name == 'x' }}` | `false` | empty |
| `${{ a \|\| b }}` with `a` empty and `b` false | `false` | `False` |
| `github.ref_name`, `ref_type`, `ref_protected`, `workflow_sha`, `repository_visibility`, `repositoryUrl`, `retention_days` | set | empty |
| `github.token`, `github.workspace`, `github.job`, `github.event_path`, `github.action`, `job.status` ... | the runner's value | empty |
| `${{ github.token != '' }}` | `true` | empty |

The runner exports `GITHUB_REF_NAME`, `GITHUB_REF_TYPE`,
`GITHUB_REF_PROTECTED` and `GITHUB_WORKFLOW_SHA` from the job message's github
context, and those keys were missing there, so the variables were unset.
`RUNNER_ENVIRONMENT` was unset too.

Two authentication answers differed as well:

- A job's `GITHUB_TOKEN` on `GET /installation/repositories` got 403
  "Resource not accessible by personal access token". On GitHub
  `GITHUB_TOKEN` is an installation token, and the route lists the run's
  repository. Clients that probe this route to tell an installation token
  from a personal access token took a job token for a PAT.
- A refused credential (unknown, revoked or expired) got 401 "Requires
  authentication". GitHub answers "Bad credentials" and keeps "Requires
  authentication" for a request that sent none.

## Fix

- A false result renders `false`, a null one stays empty, and `a || b` is the
  first truthy alternative, else the last.
- The ref, sha and repository facts are filled (`ref_protected` as a boolean: whether the branch is protected) for rendering and in the job
  message's github context. `RUNNER_ENVIRONMENT` is `github-hosted`.
- An expression that reads a value only the runner knows is left for the
  runner, with the server's own contexts bound in, in `run:`, `with:` and
  `env:`. Elsewhere (a concurrency group, say) nothing evaluates it again, so
  it still renders empty. `job.workflow_*` stay the server's: the runner has
  no job context for them.
- A server value bound into an expression left for the runner quotes a quote
  the Actions way (`''`), not with a backslash, which the runner rejects. An
  object or array is bound whole (`fromJSON('...')`), so a function or
  `a && b` gets the value; the runner applies truthiness itself. A bound string that holds a brace is bound as JSON
  too, with its braces escaped, so event data such as an issue title can
  never end the expression it is bound into.
- `working-directory` and `shell` are left for the runner the same way as
  `run`.
- Index notation (`github['workspace']`) is recognised like dot notation.
- A `'...'` literal's only escape is a doubled quote, as in Actions; a
  backslash is an ordinary character, so bound JSON keeps its escapes.
- The emulator's own runner finishes what the server leaves: it reads `''`
  inside a string, writes a false result as `false`, knows `job.status`, has
  `fromJSON` and `toJSON`, and gives `github.env`, `github.output`,
  `github.path` and `github.event_path` their file paths.
- `GET /installation/repositories` with a job token lists the run's
  repository with `repository_selection: selected`. A personal access token
  still gets 403.
- A 401 says "Bad credentials" when an `Authorization` header was sent.

Tests: `tests/actions/test_render_github_parity.py`,
`tests/actions/test_job_token_installation_routes.py`.

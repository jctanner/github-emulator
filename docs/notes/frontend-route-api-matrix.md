# Frontend Route and API Matrix

This is the M0 inventory for the API-client frontend migration. It describes
the browser contract currently implemented by Jinja routes and the API contract
the replacement frontend must use.

## Authentication Contract

The current repository UI and admin UI use separate signed, HTTP-only cookies
(`ui_session` and `admin_session`). The REST API authenticates PAT, Basic, App,
and installation credentials and does not currently treat either browser
cookie as API authentication.

The new same-origin frontend will use a browser-session API:

- `POST /api/v3/session` authenticates a user and sets `ui_session`.
- `GET /api/v3/session` returns the current browser user and a CSRF token.
- `DELETE /api/v3/session` clears the browser session.
- API authentication accepts a valid `ui_session` cookie when no Authorization
  credential is supplied.
- Unsafe cookie-authenticated requests require an `X-CSRF-Token` header.
- Explicit Authorization credentials keep their existing behavior and do not
  require the browser CSRF token.
- Admin APIs use the authenticated user's `site_admin` authorization rather
  than a second frontend-only session model. The legacy `admin_session` remains
  only while the Jinja admin UI is retained.

Session expiry, invalid credentials, missing CSRF, and insufficient permission
must return JSON errors suitable for a typed client.

## Route Matrix

| Browser routes | Current interactions/context | API contract | Status |
|---|---|---|---|
| `/ui/`, `/ui/search` | current user, repository summaries, global search | `GET /user`, `GET /user/repos`, search APIs | Existing; landing aggregation may use multiple calls |
| `/ui/login`, `/ui/logout` | signed UI cookie | browser-session API above | Implemented |
| `/ui/new` | owner selector, create repository | `GET /user`, `GET /user/orgs`, `POST /user/repos`, `POST /orgs/{org}/repos` | Existing |
| `/ui/{owner}` | user/org identity, repositories, counts | users, orgs, and repository-list APIs | Existing |
| `/ui/{owner}/{repo}` | repository metadata, README/tree, branch | repository, README, contents, branches APIs | Existing |
| `.../tree`, `.../blob`, `.../new`, `.../edit` | tree/blob display and file commits | contents, branches, Git data APIs | Existing |
| `.../commits`, `.../commit`, `.../branches`, `.../tags` | commit history/detail, refs | commits, branches, refs, tags APIs | Existing |
| `.../labels` | list/create/delete labels | labels APIs | Existing |
| `.../issues`, `.../issues/new`, `.../issues/{n}` | list/filter/create/edit/state, labels, comments | issues, comments, labels, reactions APIs | Existing |
| `.../pulls`, `.../pulls/new`, `.../pulls/{n}` | list/create/edit/state, comments, files, reviews, checks, merge | pulls, comments, reviews, files, commits, checks, merge APIs | Existing |
| `.../actions`, `.../runs/{id}`, `.../jobs/{id}` | workflow/run/job/step state and logs | Actions workflows/runs/jobs/log APIs | Existing |
| `.../actions/runners`, `.../settings/actions/runners` | repository runners | repository runner APIs | Existing |
| `.../settings` | name, visibility/features, default branch | repository GET/PATCH API | Existing |
| `.../settings/branches` | branch protection CRUD | branch protection APIs | Existing |
| `.../settings/access` | collaborators and permissions | collaborator APIs | Existing |
| `.../settings/installations` | all Apps installed on a repository | repository installation listing API | Implemented |
| `/ui/_admin/users*` | list/create/edit/delete users | admin user APIs | Implemented |
| `/ui/_admin/orgs*` | list/create/edit/delete organizations | admin organization APIs | Implemented |
| `/ui/_admin/repos*` | global list/delete repositories | admin repository APIs | Implemented |
| `/ui/_admin/tokens*` | list/create/revoke tokens | admin token APIs | Implemented |
| `/ui/_admin/apps*` | App/key/installation/token lifecycle | `/admin/api/apps` and App APIs | Implemented |
| `/ui/_admin/runners` | site/config/repository runner inventory | global admin runner read model | Implemented |
| `/ui/_admin/import*` | start/list/view import jobs | admin import APIs | Implemented |
| `/ui/_admin/issues`, dashboard | aggregate operational counts and recent data | admin summary and issue APIs | Implemented |

## Template and Interaction Inventory

The migration route manifest must cover these Jinja templates:

- Global: `base`, `landing`, `login`, `search`, `profile`, `new_repo`.
- Repository code: `repo`, `tree`, `blob`, `new_file`, `edit_file`,
  `commits`, `commit_detail`, `branches`, `tags`.
- Planning/review: `labels`, `issues`, `new_issue`, `issue_detail`, `pulls`,
  `new_pull`, `pull_detail`.
- Actions: `actions`, `action_run_detail`, `action_job_detail`,
  `action_runners`.
- Settings: `repo_settings`, `repo_settings_branches`,
  `repo_settings_collaborators`, `repo_settings_apps`,
  `repo_settings_runners`.
- Admin: dashboard, users, organizations, repositories, tokens, Apps,
  installations, runners, issues, and imports list/detail/form templates.

Forms currently perform repository creation, file creation/editing, label
creation/deletion, issue and pull-request creation/editing/state changes,
comment creation/editing, label assignment, pull-request merge, repository
settings, collaborator management, branch protection, and every listed admin
mutation. The Actions job page additionally polls a JSON live-log endpoint.

## Deterministic Parity Fixtures

The comparison seed must include:

- one user-owned public repository and one organization-owned repository;
- branches, tags, nested files, README, and several commits;
- open/closed issues and pull requests with labels, comments, reviews, checks,
  and merge states;
- workflows with queued, running, successful, and failed jobs and stable logs;
- collaborators with multiple permission levels and branch protection;
- one GitHub App installation and representative admin users/tokens/imports.

Normalize timestamps, token/private-key values, generated IDs shown only for
diagnostics, live runner heartbeats, log cursor timing, animation, and caret
rendering. Desktop and narrow viewport comparisons are required.

## Compatibility Rules

- Existing REST, GraphQL, Git, webhook/event, and runner contracts remain
  unchanged except for additive APIs.
- API mutations must continue through the same services that dispatch Actions
  and Fullsend-relevant events.
- The generated client is checked against the committed OpenAPI contract.
- A route is not considered migrated until its API, interaction, accessibility,
  and screenshot assertions pass against the same fixture.

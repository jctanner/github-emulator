# Task: Fullsend App lookups and Actions lists (#19, #17)

## Goal

Close two Fullsend compatibility gaps filed against `648b955`:

- [#19](https://github.com/jctanner/github-emulator/issues/19): `GET /apps/{app_slug}`,
  `GET /orgs/{org}/installation`, `GET /orgs/{org}/installations`, App-scoped and
  "all repositories" matching on `GET /repos/{owner}/{repo}/installation(s)`, and integer App ids.
- [#17](https://github.com/jctanner/github-emulator/issues/17): `workflow_id` as a file name on
  `GET .../actions/workflows/{workflow_id}`, `GET .../actions/workflows/{workflow_id}/runs`, and
  the repository-wide `GET .../actions/artifacts`.

## Changes

### Apps (`src/app/api/apps.py`)

- `GET /apps/{app_slug}` returns the `GET /app` shape (no private key).
- `GET /orgs/{org}/installation` needs an App JWT and returns that App's installation.
- `GET /orgs/{org}/installations` needs an organization admin (or site admin) and returns
  `{total_count, installations}` with `per_page` (max 100), `page`, and a `Link` header.
- `GET /repos/{owner}/{repo}/installation` matches with `_covers`: the installation's account owns
  the repository, and its repository list is empty ("all") or names it. With an App JWT it
  returns only the calling App's installation. Other callers keep the earlier behavior (the
  first covering installation) so existing PAT callers do not break. The response is now the
  full `_installation_json` (including `permissions`, `app_id`, and the real
  `repository_selection`) instead of a hardcoded `"selected"` stub.
- `GET /repos/{owner}/{repo}/installations` uses the same coverage rule.
- `id` in `_app_json` and `app_id` in `_installation_json` are integers;
  `InstallationResponse.app_id` is `int`. The column stays a string because the admin API
  addresses Apps by it in URLs; the emulator-specific `/admin/api/apps` responses keep strings.
- Both App-create routes (`POST /admin/apps`, `POST /admin/api/apps`) reject a non-numeric
  `app_id` with 422, so every stored id converts to an integer.

### Actions (`src/app/api/actions.py`)

- `_resolve_workflow` (numeric id, stored path, or file name beneath `.github/workflows`) is
  shared by get, runs, and dispatch.
- `GET .../workflows/{workflow_id}/runs`: newest first, `per_page`/`page`, `Link` header, and
  the `status` (status or conclusion), `event`, `branch`, and `head_sha` filters.
- `GET .../actions/runs` uses the same query builder, so it gains the filters and a `Link`
  header, and `total_count` is now the number of matching runs, not the page length.
- `GET .../actions/artifacts`: newest first, `per_page`/`page`, `name`, `Link` header, real
  `total_count`, existing `_artifact_json` shape.

Not done: the `actor`, `created`, `exclude_pull_requests`, and `check_suite_id` run filters.

## Review (Codex CLI)

A first Codex review pass found four defects, each reproduced as a failing test before the fix:

1. An App JWT lookup on a **private** repository returned 404. `CurrentUser` hides private
   repositories from callers that are not users, and a JWT is not a user. `repo_installation`
   now resolves its caller itself: an App JWT is gated by installation coverage, any other caller
   goes through `get_current_user`, which keeps the private-repository gate.
2. An App stored with a **non-numeric id** before validation would have made every response that
   includes it fail. `_github_app_id` keeps such an id as a string, and
   `InstallationResponse.app_id` is `int | str`.
3. An "all repositories" installation matched **repositories that do not exist**. Both repository
   installation routes now resolve the repository first (404 when missing).
4. The repository **artifacts list required credentials** on a public repository. It now uses
   `CurrentUser` with the same read check as the runs list.

A second pass confirmed those four fixes, found no private-repository existence leak in
`repo_installation` (an unrelated App, an unauthorized caller, and a missing repository all get
the same 404), and found two more, also reproduced first:

5. Actions reads on a private repository refused **collaborators and organization members**.
   `actions._check_read_access` allowed only the owner and site admins, although the central
   visibility gate had already let those readers through. This predates the task (eight existing
   Actions read routes used it) and the new routes inherited it. It now delegates to
   `repository_access.can_read`, the gate's own rule.
6. `GET /orgs/{org}/installation` answered for a **user account** or an account that is not an
   organization. It now 404s unless `org` is an organization.

## Evidence

- `tests/test_app_installation_lookups.py` (11 tests) and
  `tests/actions/test_workflow_lookup_and_lists.py` (7 tests) pass, including one regression
  test per review finding.
- Frontend API types regenerated with `make frontend-api`; `npm run typecheck` and `npm test`
  (82 tests) pass.
- Breadboard's App seeds (`deploy/fullsend/seed/*.py`, `deploy/scripts/18-deploy-fullsend-mint-dev.sh`)
  use numeric ids (`1001`–`1005`, `1100`) and only interpolate ids into URLs, so integer ids do
  not affect them.

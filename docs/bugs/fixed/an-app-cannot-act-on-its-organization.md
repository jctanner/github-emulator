# Bug: an App installed on an organization cannot create, administer or read it

Status: fixed 2026-11-08.

## Summary

GitHub authorizes an App's installation token by the installation's
permissions, never by membership: the App's bot is not a member of anything.
Fullsend's behaviour suite acts as such an App (its e2e App) to lease slot
repositories, so it creates, configures and deletes an organization's
repositories with an installation token. The emulator authorized those
routes by membership or by repository ownership only:

- `POST /orgs/{org}/repos` answered 403 "Must be an organization member" to
  an installation with `administration: write`.
- `PATCH` and `DELETE /repos/{o}/{r}` were filed under metadata, which has no
  write form: "Resource not accessible by integration: metadata is
  read-only". GitHub files them under Administration. So were repository
  settings (`actions/permissions`, environments, rulesets, topics, branch
  protection), which the job-token map files under coarser scopes.
- `GET /orgs/{org}/memberships/{user}` refused an installation with
  `members: read`, and `GET /orgs/{org}/installations` refused one with
  `organization_administration: read`.
- `GET /user` answered an installation token with its bot. GitHub answers
  403: the authenticated-user routes are for people, and clients ask
  GraphQL's `viewer` for a bot's login (Fullsend's `GetAuthenticatedUser`
  falls back exactly so).

Found once the suite acted as its App: the emulator also over-reported an
App bot's own access. `GET /repos/{o}/{r}/collaborators/{bot}/permission`
answered with the installation's grant (`write`), and with `admin` for a
repository the bot had created (it is the `owner_id`). GitHub reports
neither; Fullsend's CLI documents that the endpoint "does not report an
installation's repository permissions" (`internal/layers/commit.go`).
Fullsend's dispatch therefore took the e2e bot for an authorized person and
routed its own new issues to triage.

## Fix

- `deps.installation_token_grant` and `require_installation_grant` answer
  for an installation token on an organization's routes; the routes above use
  them before their membership checks.
- `_installation_scope` files repository writes on the repository itself,
  and the settings paths, under `administration`.
- `repository_access.installation_grant` generalizes `installation_role` to
  any App permission; `is_repository_admin` and repository deletion accept
  `administration: write`.
- `/user` and `/user/...` refuse installation tokens with 403.
- An installation token sent as a Basic password (git's
  `x-access-token:<token>`) gets the same scoping as a bearer one: its own
  repositories and permissions. It was resolved to the App's bot without
  that check, so a token narrowed below its installation could do what the
  installation could (found in review).
- `repository_permission` gives no creator admin to an App's bot, and the
  permission endpoint no longer falls back to the installation's grant: a
  bot without a collaborator row is 404. Its git push and contents writes
  still follow its installation.
- Not a GitHub route: `POST /admin/apps/{app_id}/installations/{id}/access_tokens`
  lets a site admin issue an installation token without the App's JWT, for
  up to a day (`expires_in`), so a development harness can act as an App's
  bot for a run longer than GitHub's hour.

## Evidence

- `tests/test_installation_administration.py`: create, change and delete
  with and without `administration: write`; another account's App; members
  and installations reads.
- `tests/test_apps_oidc.py`: `/user` refuses the token, GraphQL `viewer`
  names the bot; the admin token route's lifetime bounds and scoping.
- `test_a_narrowed_token_stays_narrow_as_a_basic_password`;
  `test_creating_a_repository_gives_the_bot_no_repository_role`.
- `tests/test_app_installation_grants.py`: the permission endpoint does not
  report an installation's grant; push still follows it.

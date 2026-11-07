# Task: organization Actions variables (github-emulator#18)

## Goal

Fullsend sets values once per organization: its CLI creates organization
variables with visibility "all" (PATCH, then POST on 404), lists the ones a
repository inherits, and its shared workflow reads them as `vars.*`. The
emulator had repository variables only.

## Done

- `org_variables` and `org_variable_repositories` tables (models in
  `src/app/models/actions.py`, migration `0015_org_actions_variables`).
- `src/app/api/org_variables.py`:
  - `GET/POST /orgs/{org}/actions/variables` (list pages at 30, with `Link`;
    create 201, 409 for a duplicate, 422 for a missing name or value or an
    unknown visibility);
  - `GET/PATCH/DELETE /orgs/{org}/actions/variables/{name}` (PATCH 204 and
    404 for a missing variable, as Fullsend's create-or-update expects;
    rename, value, visibility, selection);
  - `GET/PUT .../repositories` and `PUT/DELETE .../repositories/{id}`
    (409 unless visibility is `selected`; only the organization's own
    repositories may be selected or are listed);
  - `GET /repos/{owner}/{repo}/actions/organization-variables`.
- Visibility (`services/org_variables.py`): `all` reaches every repository of
  the organization, `private` private and internal ones, `selected` the
  selected ones.
- Workflow runs: `vars` is the visible organization variables with the
  repository's own over them, at run creation and job promotion
  (`workflow_service`).
- Access: organization admins and site admins; an installation token on the
  organization with `organization_actions_variables` (read for reads, write
  for changes); job tokens refused. The repository listing needs
  collaborator-level access (owner, collaborator, organization admin,
  installed App, site admin); installation tokens are held to
  `actions_variables` at the chokepoint.
- Deleting a repository drops its selections (`repository_purge`, by its
  `repo_id`); deleting an organization deletes its variables.

## Not done

Organization Actions secrets and environment variables, as scoped in the
issue. The repository's own variable routes still check only that a caller
is signed in; GitHub requires collaborator access there too.

## Evidence

`tests/actions/test_org_variables.py` (10 tests) and
`tests/test_database_migrations.py::test_org_variable_tables_upgrade_keeps_existing_rows`.

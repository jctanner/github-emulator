# Default Admin Personal Access Token

Every fresh emulator seeds an admin token, `ghp_admin_default_token`, so your
scripts can call the API as soon as the server is up. Start a server with the
[README quickstart](../../README.md#quickstart-container), then use the token.

## Use it

```console
$ curl -s http://localhost:8000/api/v3/user -H "Authorization: token ghp_admin_default_token" | python3 -m json.tool | head -4
{
    "login": "admin",
    "id": 1,
    "node_id": "VXNlcjox",
```

A wrong token returns
`{"message":"Bad credentials","documentation_url":"https://docs.github.com/rest"}`.
For more calls, see the [API usage guide](api-usage.md).

## What Gets Seeded

When `GITHUB_EMULATOR_SEED_DATA=true`, the startup path ensures:

- an admin user exists, using `GITHUB_EMULATOR_ADMIN_USERNAME` and
  `GITHUB_EMULATOR_ADMIN_PASSWORD`
- a personal access token named `default-admin-token` exists for that admin user
- the token has broad admin-oriented scopes for repository, user, organization,
  workflow, hook, and delete-repo operations

By default, the seeded credentials are:

```text
username: admin
password: admin
token:    ghp_admin_default_token
```

## Configuration

The relevant settings use the standard `GITHUB_EMULATOR_` prefix:

```bash
GITHUB_EMULATOR_SEED_DATA=true
GITHUB_EMULATOR_ADMIN_USERNAME=admin
GITHUB_EMULATOR_ADMIN_PASSWORD=admin
GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN=ghp_admin_default_token
```

Set `GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN` to choose a different deterministic
token for your local environment or CI job. Not run for this guide. This form
is for a server started from source; `docker-compose.yml` does not pass this
variable into the container.

```bash
GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN=ghp_my_ci_admin_token uv run uvicorn app.main:app
```

Set `GITHUB_EMULATOR_SEED_DATA=false` to disable startup seeding.

## Other auth forms

Bearer auth also returns `admin`:

```bash
curl -s http://localhost:8000/api/v3/user \
  -H "Authorization: Bearer ghp_admin_default_token" \
  | python3 -m json.tool
```

Basic auth also works when the password field is the token. `curl -u` builds
the header for you:

```console
$ curl -s -u admin:ghp_admin_default_token http://localhost:8000/api/v3/user | python3 -m json.tool | head -4
{
    "login": "admin",
    "id": 1,
    "node_id": "VXNlcjox",
```

## Operational Notes

The seeded token is idempotent. Restarting the emulator does not create
duplicates; startup updates the existing default token record for the configured
raw token.

This feature is intended for local development, test harnesses, and isolated CI
environments. For shared or long-lived deployments, override the default token
with an environment-specific value or disable seeding and create tokens through
the admin API.

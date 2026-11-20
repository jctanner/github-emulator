# API Usage

This guide shows the calls you need most: authenticate, create a token, a
repository and an issue, query GraphQL, push with git, and use `gh` over TLS.
First start a server with the [README quickstart](../../README.md#quickstart-container).

All REST paths start with `/api/v3`. The examples use `http://localhost:8000`.

## Authenticate

Every fresh instance seeds the admin token `ghp_admin_default_token`. Send it
in the `token` form:

```console
$ curl -s http://localhost:8000/api/v3/user -H "Authorization: token ghp_admin_default_token" | python3 -m json.tool | head -4
{
    "login": "admin",
    "id": 1,
    "node_id": "VXNlcjox",
```

The `Bearer` form also returns `admin`:

```bash
curl -s http://localhost:8000/api/v3/user -H "Authorization: Bearer ghp_admin_default_token"
```

A wrong token returns:

```json
{"message":"Bad credentials","documentation_url":"https://docs.github.com/rest"}
```

For Basic auth and how to change the seeded token, see the
[default admin token guide](default-admin-pat.md).

The examples below use this variable. Set it to the seeded token, or to a token you create:

```bash
TOKEN=ghp_admin_default_token
```

## Create a token

```bash
curl -s -X POST http://localhost:8000/api/v3/admin/tokens \
  -H "Content-Type: application/json" \
  -d '{"login":"admin","name":"my-token","scopes":["repo","user"]}' \
  | python3 -m json.tool
```

Success is `201` with the keys `created_at`, `id`, `name`, `scopes` and
`token`. The `token` value looks like `ghp_<redacted>`.

## Create a repository

```bash
curl -s -X POST http://localhost:8000/api/v3/user/repos \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-repo","description":"Test repo"}' \
  | python3 -m json.tool
```

Success is `201`, with `"full_name": "admin/my-repo"` and
`"clone_url": "http://localhost:8000/admin/my-repo.git"`.

## Create an issue

```bash
curl -s -X POST http://localhost:8000/api/v3/repos/admin/my-repo/issues \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Bug report","body":"Something is broken"}' \
  | python3 -m json.tool
```

Success is `201`, with `"number": 1`.

## Query GraphQL

```console
$ curl -s -X POST http://localhost:8000/api/graphql \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ viewer { login } }"}'
{"data":{"viewer":{"login":"admin"}}}
```

## Use GitHub App endpoints

The admin API creates an App. Send the admin token:

```bash
curl -s -X POST http://localhost:8000/admin/api/apps \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-app","owner":"admin"}' | python3 -m json.tool
```

The response has the keys `app_id`, `client_id`, `created_at`,
`installations_count`, `name`, `owner`, `private_key` and `slug`.

Client IDs are persisted per App. Existing SQLite databases are backfilled on
startup; private-key retrieval and rotation require the admin token.

App metadata and installations use the GitHub-style API and an App JWT.
Not run for this guide: they need an App JWT (`$APP_JWT`). Without one, they
return `401`.

```bash
curl -s http://localhost:8000/api/v3/app \
  -H "Authorization: Bearer $APP_JWT" | python3 -m json.tool
curl -s http://localhost:8000/api/v3/app/installations \
  -H "Authorization: Bearer $APP_JWT" | python3 -m json.tool
```

## Clone and push over HTTP

```bash
git clone http://localhost:8000/admin/my-repo.git /tmp/my-repo
cd /tmp/my-repo
echo "# Hello" > README.md
git add README.md && git commit -m "initial commit"
git push http://admin:$TOKEN@localhost:8000/admin/my-repo.git main
```

`make smoke` runs the same clone and push. On a new repository you see:

```text
warning: You appear to have cloned an empty repository.
...
 * [new branch]      main -> main
```

## Download a pull-request diff

Append `.diff` to a PR's UI URL to download its aggregate Git unified diff:

```text
http://localhost:8000/ui/<owner>/<repo>/pulls/<number>.diff
```

The GitHub-style `/<owner>/<repo>/pull/<number>.diff` URL advertised by the
PR API works too. API clients can request the same diff from
`GET /api/v3/repos/<owner>/<repo>/pulls/<number>` with
`Accept: application/vnd.github.diff` (or `application/vnd.github.v3.diff`).
Private repositories require authentication with read access; scoped job and
installation tokens also need pull-request read permission. Merged PRs retain
their saved pre-merge comparison, including after head-branch deletion.

## Use `gh` and `git` over TLS

The container serves HTTPS through Caddy on port 443. Caddy signs the
`ghemu.local` certificate with its own local CA, so `curl`, `git` and `gh`
reject it until you trust that CA. Trust it per command, as below. Don't turn
off verification: `gh` has no setting for that, and
`git config --global http.sslVerify false` affects every repository on your
machine.

**Prerequisites**

- The container stack is running, with host port 443 published.
- `ghemu.local` resolves to your host. Add `127.0.0.1  ghemu.local` to `/etc/hosts`.

**Steps**

1. Copy Caddy's root certificate out of the container. The file exists once
   Caddy has served its first HTTPS request:

   ```bash
   docker compose cp github-emulator:/root/.local/share/caddy/pki/authorities/local/root.crt ./caddy-root.crt
   ```

2. Pass the certificate to `curl`. Without `--cacert`, `curl` exits with code 60 (certificate not trusted).

   ```console
   $ curl -s --cacert ./caddy-root.crt https://ghemu.local/api/v3/user -H "Authorization: token ghp_admin_default_token"
   {"login":"admin", ...}
   ```

3. Pass the certificate to `git` with `GIT_SSL_CAINFO`. Here `admin/tls-repo`
   is a repository with a `main` branch:

   ```console
   $ GIT_SSL_CAINFO=./caddy-root.crt git ls-remote https://ghemu.local/admin/tls-repo.git
   b9c5748a077fb3cbcd5fdb2922013a095520fccf	refs/heads/main
   ```

These steps were verified with port 443 published on host port 8443, using
`https://ghemu.local:8443/...` and `curl --resolve ghemu.local:8443:127.0.0.1`.

**`gh` CLI**

Not run for this guide: `gh` needs `ghemu.local` resolving to the host on
port 443, and `caddy-root.crt` added to your system trust store.

```bash
export GH_HOST=ghemu.local
gh auth login --hostname ghemu.local --with-token <<< "$TOKEN"
gh repo list
gh issue create --repo admin/my-repo --title "Test" --body "Hello"
```

**Troubleshooting**

| Error text | Cause | Fix |
|---|---|---|
| `curl` exit code 60 | `curl` does not trust Caddy's CA. | Add `--cacert ./caddy-root.crt`. |
| `SSL certificate problem: unable to get local issuer certificate` | `git` does not trust Caddy's CA. | Set `GIT_SSL_CAINFO=./caddy-root.crt`. |

## Vagrant two-VM setup

Not run for this guide: needs Vagrant with libvirt.

For full `gh` CLI integration testing with TLS, a Vagrantfile provisions a
**server** VM (Debian 12 + Docker, static IP `192.168.123.10`) and a **client**
VM for running tests:

```bash
# Add the hostname to /etc/hosts
echo "192.168.123.10  ghemu.local" | sudo tee -a /etc/hosts

# Boot both VMs, sync code, build, and start
make vm-deploy

# The server is now reachable at https://ghemu.local
```

| Target | Description |
|---|---|
| `vm-up` | Boot the server and client VMs |
| `vm-deploy` | Sync, build, and start containers in the server VM |
| `vm-sync` | Rsync the codebase into the server VM |
| `vm-build` | Build the container image inside the server VM |
| `vm-start` | Start containers inside the server VM |
| `vm-stop` | Stop containers inside the server VM |
| `vm-logs` | Tail container logs inside the server VM |
| `vm-destroy` | Destroy all VMs |
| `vm-ssh` | SSH into the server VM |
| `vm-client-ssh` | SSH into the client VM |
| `vm-test` | Run `gh` CLI integration tests from the client VM |
| `vm-git-test` | Run git CLI integration tests from the client VM |
| `vm-gh` | Quick `gh repo list` from the client VM |

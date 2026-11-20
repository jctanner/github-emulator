# Configuration

This page lists every setting the emulator reads. Set each one as an
environment variable with the `GITHUB_EMULATOR_` prefix before you start the
server, for example `GITHUB_EMULATOR_SEED_DATA=false`. The source of truth is
[`src/app/config.py`](../../src/app/config.py).

## Ports and host names

- **Host port.** The Compose stack maps the HTTP API port from `${PORT:-8000}`.
  For example, `make CONTAINER_ENGINE=podman PORT=9000 up` exposes the service
  on port 9000 while the container continues listening on port 8000.
- **Response URLs.** The Compose file sets
  `GITHUB_EMULATOR_BASE_URL=https://ghemu.local`, so URLs in responses from
  the container use that host, for example
  `"avatar_url": "https://ghemu.local/avatars/admin"`. A server started from
  source uses `http://localhost:8000`.

## Settings

### Core

| Variable | Default | Description |
|---|---|---|
| `GITHUB_EMULATOR_BASE_URL` | `http://localhost:8000` | Base URL used in API response URLs. |
| `GITHUB_EMULATOR_DATA_DIR` | `src/data` in the checkout | Directory for bare git repos and the SQLite database. The container sets `/data`. |
| `GITHUB_EMULATOR_DATABASE_URL` | `sqlite+aiosqlite:///{DATA_DIR}/github_emulator.db` | SQLAlchemy database URL. |
| `GITHUB_EMULATOR_SECRET_KEY` | `change-me-in-production` | Secret for JWT and session signing. |
| `GITHUB_EMULATOR_HOSTNAME` | `ghemu.local` | Hostname for the Caddy TLS certificate and `gh` CLI integration. |
| `GITHUB_EMULATOR_HOST` | `0.0.0.0` | Defined in `config.py` but not read by the server; the `uvicorn --host` option sets the listen address. |
| `GITHUB_EMULATOR_PORT` | `8000` | Defined in `config.py` but not read by the server; the `uvicorn --port` option sets the listen port. |
| `GITHUB_EMULATOR_RATE_LIMIT` | `5000` | Requests per token per hour that the rate-limit headers and `GET /rate_limit` report. Nothing is refused at zero; raise it so a client that paces itself on the headers does not wait. |

### Seeded admin

| Variable | Default | Description |
|---|---|---|
| `GITHUB_EMULATOR_SEED_DATA` | `true` | Seed the admin user and default token at startup. |
| `GITHUB_EMULATOR_ADMIN_USERNAME` | `admin` | Admin user created on first startup. |
| `GITHUB_EMULATOR_ADMIN_PASSWORD` | `admin` | Admin user password. |
| `GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN` | `ghp_admin_default_token` | Default admin token seeded at startup. See the [default admin token guide](default-admin-pat.md). |

### SQLite

| Variable | Default | Description |
|---|---|---|
| `GITHUB_EMULATOR_SQLITE_BUSY_TIMEOUT_MS` | `5000` | How long SQLite waits for a lock before it fails a statement. |
| `GITHUB_EMULATOR_SQLITE_WRITE_RETRY_ATTEMPTS` | `2` | How many times a write that hit a locked database is tried. |
| `GITHUB_EMULATOR_SQLITE_WRITE_RETRY_DELAY_MS` | `100` | Wait between those write attempts. |

### Git over SSH

| Variable | Default | Description |
|---|---|---|
| `GITHUB_EMULATOR_SSH_ENABLED` | `true` | Turn the SSH transport on or off. |
| `GITHUB_EMULATOR_SSH_PORT` | `2222` | SSH server listen port. |
| `GITHUB_EMULATOR_SSH_HOST_KEY_PATH` | empty (`{DATA_DIR}/ssh_host_key`) | SSH host key file; generated when missing. |

### Raw content

| Variable | Default | Description |
|---|---|---|
| `GITHUB_EMULATOR_RAW_CONTENT_HOSTS` | `raw.githubusercontent.com` | Comma-separated host names whose requests use github.com's raw path shape (`/<owner>/<repo>/<ref>/<path>`). Only matters on a machine that resolves the name to the emulator and trusts its certificate. |
| `GITHUB_EMULATOR_RAW_CONTENT_UPSTREAM_ADDR` | empty | The real `raw.githubusercontent.com` IP address. When set, a raw request for a repository the emulator lacks is fetched from there. |
| `GITHUB_EMULATOR_RAW_CONTENT_UPSTREAM_MAX_BYTES` | `104857600` (100 MiB) | Largest body taken from that upstream fetch; a larger one gets `502`. |

### Actions

| Variable | Default | Description |
|---|---|---|
| `GITHUB_EMULATOR_ACTIONS_UPSTREAM_API_URL` | `https://api.github.com` | Where the emulator resolves `uses:` action refs for the runner. |
| `GITHUB_EMULATOR_ACTIONS_UPSTREAM_ARCHIVE_URL` | `https://codeload.github.com` | Where the emulator fetches action archives for the runner. |
| `GITHUB_EMULATOR_ACTIONS_UPSTREAM_TOKEN` | empty | Optional token for those upstream calls; only raises the unauthenticated rate limit. |
| `GITHUB_EMULATOR_RUNNER_CLAIM_ACK_SECONDS` | `120` | A claimed job whose runner starts no step within this time goes back to the queue. |
| `GITHUB_EMULATOR_RUNNER_STALE_THRESHOLD_SECONDS` | `120` | Jobs on a runner that stops heartbeating go back to the queue after this time. |
| `GITHUB_EMULATOR_ENTERPRISE_SLUG` | `breadboard` | The single enterprise used for enterprise-scoped runners. |
| `GITHUB_EMULATOR_OIDC_ISSUER` | empty (uses `BASE_URL`) | Issuer URL for the Actions OIDC tokens. |
| `GITHUB_EMULATOR_ACTIONS_OIDC_REQUEST_TOKEN` | `fullsend-action-request` | Defined in `config.py` but not read by the current code. |
| `GITHUB_EMULATOR_ARTIFACT_MAX_BYTES` | `10737418240` (10 GiB) | Largest artifact upload-artifact v4+ may send, and the most its zip may expand to. |
| `GITHUB_EMULATOR_ARTIFACT_MAX_BLOCKS` | `50000` | Most blocks in one upload's block list. |

### GitHub Apps

| Variable | Default | Description |
|---|---|---|
| `GITHUB_EMULATOR_APP_JWT_PERMISSIVE` | `true` | Skip App JWT signature verification; set `false` for strict verification. |

### Memory watchdog

| Variable | Default | Description |
|---|---|---|
| `GITHUB_EMULATOR_MEMORY_WATCH` | `true` | Report in-flight requests when memory use crosses a threshold, to the log and `{DATA_DIR}/memory-watch.log`. |
| `GITHUB_EMULATOR_MEMORY_WATCH_TRACE` | `false` | Also report the largest allocation sites (tracemalloc). Slow; turn on only for a bounded investigation. |
| `GITHUB_EMULATOR_MEMORY_WATCH_INTERVAL_SECONDS` | `2.0` | How often the watchdog checks memory use. |
| `GITHUB_EMULATOR_MEMORY_WATCH_THRESHOLDS_MIB` | `512,768,1024,1280` | Memory levels (MiB) that trigger a report. |
| `GITHUB_EMULATOR_MEMORY_WATCH_GROWTH_MIB` | `200` | Growth (MiB) past the last report that triggers another. |
| `GITHUB_EMULATOR_MEMORY_WATCH_FRAMES` | `12` | Stack frames kept per traced allocation site. |

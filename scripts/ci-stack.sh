#!/usr/bin/env bash
# Start the emulator and its Python runner as two plain processes: SQLite,
# plain HTTP, no Caddy, no container and no cluster. This is the smallest
# stack that executes a workflow run end to end, and it is what the CI
# workflow uses.
#
#   scripts/ci-stack.sh start   # start both, wait until the API answers
#   scripts/ci-stack.sh stop    # stop both
#
# Settings (all optional):
#   CI_STACK_DIR    state directory: SQLite file, git repos, logs, pids, and
#                   the site admin's token (admin-token) and password
#                   (admin-password), made per stack unless
#                   GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN or
#                   GITHUB_EMULATOR_ADMIN_PASSWORD is set
#   CI_STACK_PORT   API port (default 8000)
#   CI_STACK_PYTHON python with the locked dependencies installed (default
#                   python3), e.g. a `uv sync --frozen` .venv
#   CI_STACK_RUNNER_LABELS  labels the runner serves
#   CI_STACK_HOST   host name the emulator puts in its URLs (default
#                   127.0.0.1). A sandbox that cannot reach loopback needs a
#                   name that resolves to this machine, e.g. <ip>.nip.io.
#   CI_STACK_BIND   address uvicorn listens on (default 127.0.0.1; use
#                   0.0.0.0 with a non-loopback CI_STACK_HOST)
#   CI_STACK_RUNNERS  runner processes, i.e. jobs that run at once (default 1)
#   CI_STACK_RUNNER_KIND  python (src/runners/emulator/runner.py, default) or
#                   upstream (the real actions/runner, Linux x64)
#   CI_STACK_TLS    1 to serve https from uvicorn itself, with a CA and a
#                   certificate for CI_STACK_HOST made per run under
#                   $CI_STACK_DIR/tls. Clients must trust tls/ca.pem;
#                   tls/bundle.pem is the public roots plus it, for clients
#                   that also reach github.com. Runners get both. Needed when
#                   a client insists on https (gh, the OpenShell egress proxy).
#   CI_STACK_SYSTEM_TRUST  1 to add the CA to the system store with sudo
#                   (Debian/Ubuntu). actions/runner (.NET) reads only that.
#   CI_STACK_LOCALHOST_DNS  1 to make CI_STACK_HOST, which must end in
#                   .localhost, resolve to this machine's own address instead
#                   of loopback, for every client: the runner and its steps,
#                   and a host-network container that asks DNS directly (an
#                   OpenShell sandbox supervisor, whose policy DNS refuses
#                   loopback answers). systemd-resolved always answers
#                   *.localhost with loopback, so a dnsmasq on 127.0.0.2
#                   answers this one name (and its subdomains) and forwards
#                   everything else to the previous resolver;
#                   /etc/resolv.conf points at it. Deliberately no
#                   /etc/hosts line: podman copies that file into every
#                   container, and a sandbox must resolve the name through
#                   OpenShell's policy DNS, not from the file.
#                   Other names resolve as before. Linux only (sudo); a no-op
#                   elsewhere. Implies CI_STACK_BIND=0.0.0.0 unless that is set.
#                   `stop` does not undo it: the VM is discarded after the job.
#   CI_STACK_DNS_EXTRA  with CI_STACK_LOCALHOST_DNS: space-separated
#                   name=ip pairs for more services on this machine, e.g.
#                   "gw.ghemu.localhost=10.254.0.1" for a second https
#                   service that needs its own port 443. Each name must be a
#                   subdomain of CI_STACK_HOST and each ip a non-loopback
#                   IPv4 address; one not yet on this machine is added to lo.
#   CI_STACK_RAW_HOST  1 to answer raw.githubusercontent.com on this
#                   machine too, for a client that hard-codes github.com's
#                   raw host (Fullsend's URL-sourced harness). Needs
#                   CI_STACK_LOCALHOST_DNS=1, CI_STACK_TLS=1 and
#                   CI_STACK_PORT=443: dnsmasq answers the name with this
#                   machine's address and the certificate names it. Every
#                   raw.githubusercontent.com fetch on this machine then
#                   reaches the emulator. It serves its own repositories and
#                   fetches any other from the real host, at the address
#                   looked up before the switch (raw-upstream-addr).
#   CI_STACK_RAW_ADDR  with CI_STACK_RAW_HOST: answer raw.githubusercontent.com
#                   with this IPv4 address instead of this machine's, added
#                   to lo. For a client that refuses private addresses
#                   (Fullsend's fetch guard): give a public address the job
#                   never needs to reach, e.g. 185.199.108.200, unused in
#                   GitHub's raw-content block. It must not be loopback or
#                   the real host's address, which the fallback above uses.
#   GITHUB_EMULATOR_*  any emulator setting in this environment reaches the
#                   emulator, e.g. GITHUB_EMULATOR_RATE_LIMIT=1000000 for a
#                   suite whose client paces itself on X-RateLimit-Remaining.
#   CI_STACK_RUNNER_ENV  extra environment for every runner and the jobs it
#                   runs: NAME=VALUE lines, one per line (a value may hold
#                   commas and spaces), e.g. a gateway shared by the runners
#                   on this machine. Blank lines are skipped. The upstream
#                   runner gets them in its .env, so the outer-environment
#                   stripping below cannot drop them.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${CI_STACK_DIR:-$ROOT/.ci-stack}"
PORT="${CI_STACK_PORT:-8000}"
PY="${CI_STACK_PYTHON:-python3}"
LABELS="${CI_STACK_RUNNER_LABELS:-self-hosted,linux,ubuntu-latest,ubuntu-24.04}"
HOST="${CI_STACK_HOST:-127.0.0.1}"
LOCALHOST_DNS="${CI_STACK_LOCALHOST_DNS:-0}"
if [ "$LOCALHOST_DNS" = 1 ]; then
  BIND="${CI_STACK_BIND:-0.0.0.0}"
else
  BIND="${CI_STACK_BIND:-127.0.0.1}"
fi
RUNNERS="${CI_STACK_RUNNERS:-1}"
KIND="${CI_STACK_RUNNER_KIND:-python}"
UPSTREAM_VERSION="${CI_STACK_UPSTREAM_RUNNER_VERSION:-2.338.2}"
UPSTREAM_SHA256="${CI_STACK_UPSTREAM_RUNNER_SHA256:-baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40}"
TLS="${CI_STACK_TLS:-0}"
RAW_HOST="${CI_STACK_RAW_HOST:-0}"
RAW_NAME=raw.githubusercontent.com
RAW_ADDR="${CI_STACK_RAW_ADDR:-}"
if [ -n "$RAW_ADDR" ] && { [ "$RAW_HOST" != 1 ] || [ "$BIND" != 0.0.0.0 ] || ! [[ "$RAW_ADDR" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || [[ "$RAW_ADDR" == 127.* ]]; }; then
  echo "CI_STACK_RAW_ADDR=$RAW_ADDR needs CI_STACK_RAW_HOST=1, CI_STACK_BIND=0.0.0.0 and a non-loopback IPv4 address" >&2
  exit 2
fi
if [ "$RAW_HOST" = 1 ] && { [ "$LOCALHOST_DNS" != 1 ] || [ "$TLS" != 1 ] || [ "$PORT" != 443 ]; }; then
  echo "CI_STACK_RAW_HOST=1 needs CI_STACK_LOCALHOST_DNS=1, CI_STACK_TLS=1 and CI_STACK_PORT=443" >&2
  exit 2
fi
SCHEME=http
[ "$TLS" = 1 ] && SCHEME=https
# No default port in the URL, as GitHub writes its own: the OIDC issuer is
# compared exactly, and https://ghemu.localhost:443 is not
# https://ghemu.localhost.
case "$SCHEME:$PORT" in
  http:80 | https:443) BASE="$SCHEME://$HOST" ;;
  *) BASE="$SCHEME://$HOST:$PORT" ;;
esac
# Readiness is checked on loopback: CI_STACK_HOST may name an address this
# machine cannot reach itself (one inside a VM), and only clients use it.
LOCAL="$SCHEME://127.0.0.1:$PORT"
if [ "$BIND" != 0.0.0.0 ]; then LOCAL="$SCHEME://$BIND:$PORT"; fi
CA="$DIR/tls/ca.pem"

# The site admin's token and password for this stack, never the published
# defaults: with CI_STACK_LOCALHOST_DNS the emulator listens beyond loopback.
# A caller's GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN or GITHUB_EMULATOR_ADMIN_PASSWORD
# wins; otherwise each is made once per state directory and kept in
# $DIR/admin-token and $DIR/admin-password (mode 600), so a restart on the
# same database keeps working and clients read the token from the file.
admin_credentials() {
  if [ -z "${GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN:-}" ]; then
    if [ -s "$DIR/admin-token" ]; then
      GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN="$(cat "$DIR/admin-token")"
    else
      GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN="ghp_$(openssl rand -hex 20)"
    fi
  fi
  if [ -z "${GITHUB_EMULATOR_ADMIN_PASSWORD:-}" ]; then
    if [ -s "$DIR/admin-password" ]; then
      GITHUB_EMULATOR_ADMIN_PASSWORD="$(cat "$DIR/admin-password")"
    else
      GITHUB_EMULATOR_ADMIN_PASSWORD="$(openssl rand -hex 16)"
    fi
  fi
  (umask 077
   printf '%s\n' "$GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN" >"$DIR/admin-token"
   printf '%s\n' "$GITHUB_EMULATOR_ADMIN_PASSWORD" >"$DIR/admin-password")
  export GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN GITHUB_EMULATOR_ADMIN_PASSWORD
  ADMIN_TOKEN="$GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN"
}

# EXIT trap during start: a start that fails stops what it launched.
stop_after_failed_start() {
  local status=$?
  if [ "$status" != 0 ]; then
    echo "start failed; stopping what it launched" >&2
    stop
  fi
}

# A pid file whose process is still alive.
live_pid() {
  [ -s "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null
}

# A throwaway CA and a server certificate for HOST and loopback.
make_tls() {
  local t="$DIR/tls" san
  mkdir -p "$t"
  if [[ "$HOST" =~ ^[0-9.]+$ ]]; then san="IP:$HOST"; else san="DNS:$HOST"; fi
  san="$san,DNS:localhost,IP:127.0.0.1"
  if [ "$RAW_HOST" = 1 ]; then san="$san,DNS:$RAW_NAME"; fi
  openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=ci-stack CA" \
    -keyout "$t/ca-key.pem" -out "$CA" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=$HOST" \
    -keyout "$t/server-key.pem" -out "$t/server.csr" 2>/dev/null
  printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n' "$san" >"$t/ext.cnf"
  openssl x509 -req -in "$t/server.csr" -CA "$CA" -CAkey "$t/ca-key.pem" \
    -CAcreateserial -days 2 -extfile "$t/ext.cnf" -out "$t/server.pem" 2>/dev/null
  chmod 600 "$t"/*-key.pem
  # SSL_CERT_FILE and GIT_SSL_CAINFO replace the default roots rather than
  # adding to them, so give those the public roots plus this CA.
  cat "$("$PY" -c 'import certifi; print(certifi.where())')" "$CA" >"$t/bundle.pem"
  if [ "${CI_STACK_SYSTEM_TRUST:-0}" = 1 ]; then
    sudo cp "$CA" /usr/local/share/ca-certificates/ci-stack.crt
    sudo update-ca-certificates >/dev/null
  fi
}

# Resolve HOST (*.localhost) to this machine's own address for every client;
# see CI_STACK_LOCALHOST_DNS above. Idempotent.
localhost_dns() {
  [ "$LOCALHOST_DNS" = 1 ] || return 0
  if [ "$(uname -s)" != Linux ]; then
    echo "CI_STACK_LOCALHOST_DNS: Linux only; leaving $HOST to the system resolver" >&2
    return 0
  fi
  case "$HOST" in
    *.localhost) ;;
    *) echo "CI_STACK_LOCALHOST_DNS needs a CI_STACK_HOST ending in .localhost, got $HOST" >&2; exit 2 ;;
  esac
  local ip upstream
  ip="$(ip -4 route get 1.1.1.1 | awk '{for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit }}')"
  if [ -z "$ip" ]; then
    echo "CI_STACK_LOCALHOST_DNS: could not find this machine's address" >&2
    exit 1
  fi
  # No /etc/hosts line: podman copies the host's /etc/hosts into every
  # container, so an OpenShell sandbox would resolve HOST from it to this
  # machine's address and dial it directly, which its network policy
  # refuses ("connect: permission denied"), instead of asking policy DNS
  # and going through the proxy. glibc ("files dns") still gets HOST from
  # the dnsmasq below. Remove a line an earlier version left.
  sudo sed -i -E "/[[:space:]]$HOST([[:space:]]|\$)/d" /etc/hosts
  # Everything that asks DNS. Keep the first resolver seen as the
  # upstream, so a second start does not forward dnsmasq to itself.
  if [ ! -f "$DIR/resolv.conf.orig" ]; then
    cp -L /etc/resolv.conf "$DIR/resolv.conf.orig"
  fi
  upstream="$(awk '$1 == "nameserver" { print $2; exit }' "$DIR/resolv.conf.orig")"
  command -v dnsmasq >/dev/null || sudo apt-get -qq install -y dnsmasq-base >/dev/null
  sudo ip addr add 127.0.0.2/8 dev lo 2>/dev/null || true
  if [ -f "$DIR/dnsmasq.pid" ] && sudo kill -0 "$(cat "$DIR/dnsmasq.pid")" 2>/dev/null; then
    sudo kill "$(cat "$DIR/dnsmasq.pid")"
    sleep 0.5
  fi
  # Extra names (CI_STACK_DNS_EXTRA): subdomains of HOST only, so the knob
  # cannot redirect an unrelated name, and never loopback, which an
  # OpenShell supervisor refuses. dnsmasq answers the most specific match.
  local extra=() pair name addr
  for pair in ${CI_STACK_DNS_EXTRA:-}; do
    name="${pair%%=*}" addr="${pair#*=}"
    case "$name" in
      *."$HOST") ;;
      *) echo "CI_STACK_DNS_EXTRA: $name is not a subdomain of $HOST" >&2; exit 2 ;;
    esac
    if ! [[ "$addr" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || [[ "$addr" == 127.* ]]; then
      echo "CI_STACK_DNS_EXTRA: $addr is not a non-loopback IPv4 address" >&2
      exit 2
    fi
    ip -4 -o addr show | grep -q " $addr/" || sudo ip addr add "$addr/32" dev lo
    extra+=(--address="/$name/$addr")
    echo "$name -> $addr"
  done
  if [ "$RAW_HOST" = 1 ]; then
    # The real address, looked up while the name still resolves to it, so
    # the emulator can fetch repositories it does not have. Kept across
    # restarts: by then the name resolves here.
    if [ ! -s "$DIR/raw-upstream-addr" ]; then
      getent ahostsv4 "$RAW_NAME" | awk 'NR == 1 { print $1 }' >"$DIR/raw-upstream-addr" || true
    fi
    if [ ! -s "$DIR/raw-upstream-addr" ] || [ "$(cat "$DIR/raw-upstream-addr")" = "$ip" ]; then
      echo "CI_STACK_RAW_HOST: could not look up the real $RAW_NAME; other repositories will 404" >&2
      rm -f "$DIR/raw-upstream-addr"
    fi
    local raw_ip="${RAW_ADDR:-$ip}"
    if [ -s "$DIR/raw-upstream-addr" ] && [ "$(cat "$DIR/raw-upstream-addr")" = "$raw_ip" ]; then
      echo "CI_STACK_RAW_ADDR: $raw_ip is the real $RAW_NAME; pick another address" >&2
      exit 2
    fi
    ip -4 -o addr show | grep -q " $raw_ip/" || sudo ip addr add "$raw_ip/32" dev lo
    extra+=(--address="/$RAW_NAME/$raw_ip")
    echo "$RAW_NAME -> $raw_ip"
  fi
  sudo dnsmasq --listen-address=127.0.0.2 --bind-interfaces --port=53 --no-resolv --no-hosts \
    --server="$upstream" --address="/$HOST/$ip" ${extra[@]+"${extra[@]}"} --pid-file="$DIR/dnsmasq.pid"
  # /etc/resolv.conf is a symlink into systemd-resolved's runtime directory on
  # Ubuntu; replace the link, not the file resolved owns.
  sudo rm -f /etc/resolv.conf
  printf 'nameserver 127.0.0.2\noptions edns0 trust-ad\n' | sudo tee /etc/resolv.conf >/dev/null
  echo "$HOST -> $ip (hosts file and dnsmasq on 127.0.0.2; other names via $upstream)"
}

# What a runner and the steps it starts need to trust the emulator.
export_trust() {
  if [ "$TLS" = 1 ]; then
    export SSL_CERT_FILE="$DIR/tls/bundle.pem" GIT_SSL_CAINFO="$DIR/tls/bundle.pem" \
      NODE_EXTRA_CA_CERTS="$CA"
  fi
}

# CI_STACK_RUNNER_ENV, checked once before anything starts.
RUNNER_ENV=()
read_runner_env() {
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [ -n "${line//[[:space:]]/}" ] || continue
    if ! [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      # Name only: a value may be a secret.
      echo "CI_STACK_RUNNER_ENV: each line must be NAME=VALUE; got one starting '${line%%=*}'" >&2
      exit 2
    fi
    RUNNER_ENV+=("$line")
  done <<<"${CI_STACK_RUNNER_ENV:-}"
}

start() {
  read_runner_env
  mkdir -p "$DIR/data" "$DIR/runner"
  # Refuse before touching any pid file: overwriting a live stack's pids
  # would leave `stop` unable to reach it.
  local f
  for f in $(pidfiles); do
    if live_pid "$f"; then
      echo "a stack is already running in $DIR ($(basename "$f" .pid) pid $(cat "$f")); run '$0 stop' first" >&2
      exit 1
    fi
    rm -f "$f"
  done
  # Something else on the port would answer the readiness check for us.
  if curl -s -k -m 2 -o /dev/null "$LOCAL/api/v3"; then
    echo "$LOCAL already answers; another server holds port $PORT" >&2
    exit 1
  fi
  admin_credentials
  # From here on, a failed start stops what it launched.
  trap stop_after_failed_start EXIT
  localhost_dns
  local tls_args=() curl_ca=()
  if [ "$TLS" = 1 ]; then
    make_tls
    tls_args=(--ssl-certfile "$DIR/tls/server.pem" --ssl-keyfile "$DIR/tls/server-key.pem")
    curl_ca=(--cacert "$CA")
  fi
  # Job tokens and sessions are signed with SECRET_KEY, so never run on the
  # published default; and App JWTs are verified, not just decoded.
  GITHUB_EMULATOR_SECRET_KEY="${GITHUB_EMULATOR_SECRET_KEY:-$(openssl rand -hex 32)}" \
  GITHUB_EMULATOR_APP_JWT_PERMISSIVE="${GITHUB_EMULATOR_APP_JWT_PERMISSIVE:-false}" \
  GITHUB_EMULATOR_DATA_DIR="$DIR/data" \
  GITHUB_EMULATOR_DATABASE_URL="sqlite+aiosqlite:///$DIR/data/github_emulator.db" \
  GITHUB_EMULATOR_BASE_URL="$BASE" \
  GITHUB_EMULATOR_HOSTNAME="$HOST" \
  GITHUB_EMULATOR_RAW_CONTENT_UPSTREAM_ADDR="$(cat "$DIR/raw-upstream-addr" 2>/dev/null || true)" \
  GITHUB_EMULATOR_SSH_ENABLED=false \
  PYTHONPATH="$ROOT/src" \
    nohup "$PY" -m uvicorn app.main:app --host "$BIND" --port "$PORT" \
      ${tls_args[@]+"${tls_args[@]}"} >"$DIR/emulator.log" 2>&1 &
  echo $! >"$DIR/emulator.pid"

  # Ready when this emulator answers: it must still be alive, so a server
  # that failed to bind cannot pass on another one's answer.
  local ready=0
  for _ in $(seq 1 60); do
    if ! live_pid "$DIR/emulator.pid"; then break; fi
    if curl -sf -m 2 ${curl_ca[@]+"${curl_ca[@]}"} "$LOCAL/api/v3" >/dev/null; then ready=1; break; fi
    sleep 0.5
  done
  if [ "$ready" != 1 ] || ! live_pid "$DIR/emulator.pid"; then
    echo "the emulator did not start; last lines of $DIR/emulator.log:" >&2
    tail -50 "$DIR/emulator.log" >&2
    exit 1
  fi

  # One runner executes one job at a time, so job concurrency is the number
  # of runner processes. Each gets its own work directory.
  case "$KIND" in
    python)
      for i in $(seq 1 "$RUNNERS"); do start_python_runner "$i"; done ;;
    upstream)
      # Registering a real runner takes seconds (config.sh is a .NET
      # program), and nothing orders one runner after another, so register
      # them all at once and fail if any of them fails.
      fetch_upstream_runner
      local pids=() failed=0
      for i in $(seq 1 "$RUNNERS"); do
        start_upstream_runner "$i" &
        pids+=($!)
      done
      for i in "${!pids[@]}"; do
        if ! wait "${pids[$i]}"; then
          echo "runner $((i + 1)) did not start; see $DIR/runner-$((i + 1)).log" >&2
          failed=1
        fi
      done
      [ "$failed" = 0 ] || exit 1 ;;
    *) echo "CI_STACK_RUNNER_KIND must be python or upstream, got $KIND" >&2; exit 2 ;;
  esac
  trap - EXIT
  echo "emulator $BASE (pid $(cat "$DIR/emulator.pid")), $RUNNERS $KIND runner(s)"
  echo "admin token in $DIR/admin-token"
  if [ "$TLS" = 1 ]; then
    echo "trust $CA (CURL_CA_BUNDLE/NODE_EXTRA_CA_CERTS) or $DIR/tls/bundle.pem (SSL_CERT_FILE/GIT_SSL_CAINFO)"
  fi
}

start_python_runner() {
  local i="$1"
  export_trust
  GITHUB_EMULATOR_URL="$BASE" \
  GITHUB_EMULATOR_TOKEN="$ADMIN_TOKEN" \
  RUNNER_SCOPE=site \
  RUNNER_NAME="ci-runner-$i" \
  RUNNER_LABELS="$LABELS" \
  RUNNER_WORKDIR="$DIR/runner-$i/work" \
    nohup env ${RUNNER_ENV[@]+"${RUNNER_ENV[@]}"} "$PY" "$ROOT/src/runners/emulator/runner.py" \
      >"$DIR/runner-$i.log" 2>&1 &
  echo $! >"$DIR/runner-$i.pid"
}

# Download and unpack the real actions/runner once; each runner copies it.
fetch_upstream_runner() {
  local tarball="$DIR/actions-runner-$UPSTREAM_VERSION.tar.gz"
  local dist="$DIR/actions-runner-$UPSTREAM_VERSION"
  if [ ! -f "$tarball" ]; then
    curl -fsSL -o "$tarball.partial" \
      "https://github.com/actions/runner/releases/download/v$UPSTREAM_VERSION/actions-runner-linux-x64-$UPSTREAM_VERSION.tar.gz"
    # The digest GitHub publishes for the release asset. Keep it in step with
    # the version: a new version needs its own CI_STACK_UPSTREAM_RUNNER_SHA256.
    if ! echo "$UPSTREAM_SHA256  $tarball.partial" | sha256sum -c --quiet -; then
      echo "actions/runner $UPSTREAM_VERSION does not match CI_STACK_UPSTREAM_RUNNER_SHA256" >&2
      rm -f "$tarball.partial"
      exit 1
    fi
    mv "$tarball.partial" "$tarball"
  fi
  if [ ! -d "$dist" ]; then
    rm -rf "$dist.partial"
    mkdir -p "$dist.partial"
    tar -xzf "$tarball" -C "$dist.partial"
    mv "$dist.partial" "$dist"
  fi
}

# The real actions/runner, registered at enterprise scope the way
# src/runners/upstream/entrypoint.sh does it. Linux x64 only. Runs in the
# background, one per runner, after fetch_upstream_runner.
start_upstream_runner() {
  local i="$1"
  local home="$DIR/runner-$i" enterprise="${CI_STACK_ENTERPRISE:-breadboard}"
  mkdir -p "$home"
  cp -a "$DIR/actions-runner-$UPSTREAM_VERSION/." "$home/"
  export_trust
  local reg
  reg="$(curl -sf -X POST -H "Authorization: token $ADMIN_TOKEN" \
    "$BASE/api/v3/enterprises/$enterprise/actions/runners/registration-token" | jq -r .token)"
  # Inside a GitHub-hosted job the environment describes that job, and some
  # of it changes how a runner behaves: ACTIONS_RUNNER_RETURN_JOB_RESULT_FOR_HOSTED
  # makes runner 2.33x run one job and exit. Start this runner without the
  # outer job's ACTIONS_*, GITHUB_* and RUNNER_* variables.
  local outer=()
  while IFS= read -r name; do outer+=(-u "$name"); done < <(
    env | cut -d= -f1 | grep -E '^(ACTIONS|GITHUB|RUNNER)_[A-Za-z0-9_]*$')
  (cd "$home" && env ${outer[@]+"${outer[@]}"} ./config.sh --unattended --replace \
    --url "$BASE/enterprises/$enterprise" --token "$reg" \
    --name "ci-runner-$i" --labels "$LABELS" --work _work) >"$DIR/runner-$i.log" 2>&1
  # A GitHub-hosted job starts on a fresh machine; this runner reuses one
  # work directory, so a job saw what the previous one left, such as a
  # sparse checkout that a later full checkout of the same commit kept.
  # Empty the workspace and temp directory before every job. Set in the
  # runner's .env (written by config.sh above), which run.sh reads, so the
  # outer-environment stripping cannot drop it.
  cat >"$home/job-started.sh" <<'HOOK'
#!/usr/bin/env bash
# ACTIONS_RUNNER_HOOK_JOB_STARTED: start every job with an empty workspace.
set -euo pipefail
work="$(cd "$(dirname "$0")/_work" && pwd -P)"
for dir in "${GITHUB_WORKSPACE:-}" "${RUNNER_TEMP:-}"; do
  [ -n "$dir" ] && [ -d "$dir" ] || continue
  real="$(cd "$dir" && pwd -P)"
  case "$real/" in
    "$work"/?*) find "$real" -mindepth 1 -maxdepth 1 -exec rm -rf {} + ;;
    *) echo "job-started hook: not cleaning $real, outside $work" >&2 ;;
  esac
done
HOOK
  chmod +x "$home/job-started.sh"
  echo "ACTIONS_RUNNER_HOOK_JOB_STARTED=$home/job-started.sh" >>"$home/.env"
  if [ ${#RUNNER_ENV[@]} -gt 0 ]; then
    printf '%s\n' "${RUNNER_ENV[@]}" >>"$home/.env"
  fi
  (cd "$home" && env ${outer[@]+"${outer[@]}"} nohup ./run.sh >>"$DIR/runner-$i.log" 2>&1 &
   echo $! >"$DIR/runner-$i.pid")
}

pidfiles() {
  ls "$DIR"/runner-*.pid "$DIR/emulator.pid" 2>/dev/null || true
}

stop() {
  local f pid
  for f in $(pidfiles); do
    pid="$(cat "$f")"
    kill "$pid" 2>/dev/null || true
    # uvicorn can outlive TERM; give it 10 seconds, then KILL.
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$f"
  done
  # run.sh leaves Runner.Listener and Runner.Worker behind.
  pkill -f "$DIR/runner-" 2>/dev/null || true
}

# Print each process's peak resident memory (VmHWM) and CPU seconds so far,
# read from /proc, so Linux only. Job steps run as the runner's children and
# are not counted: their cost belongs to the workflow, not the stack.
stats() {
  local ticks
  ticks="$(getconf CLK_TCK)"
  for f in $(pidfiles); do
    local pid name
    pid="$(cat "$f")"
    name="$(basename "$f" .pid)"
    awk -v n="$name" '/^VmHWM/ {printf "%s peak_rss_mib=%.0f ", n, $2/1024}' "/proc/$pid/status"
    awk -v t="$ticks" '{printf "cpu_s=%.1f\n", ($14+$15)/t}' "/proc/$pid/stat"
  done
  # The upstream runner's pid file names run.sh; its .NET listener and worker
  # are children. Report their current resident memory as a group.
  ps -eo rss=,args= | awk -v d="$DIR/runner-" 'index($0, d) {s += $1} END {printf "all runner processes now rss_mib=%.0f\n", s/1024}'
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  stats) stats ;;
  *) echo "usage: $0 start|stop|stats" >&2; exit 2 ;;
esac

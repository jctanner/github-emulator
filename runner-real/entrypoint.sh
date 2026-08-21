#!/usr/bin/env bash
set -euo pipefail

EMULATOR_URL="${GITHUB_EMULATOR_URL:-http://github-emulator:8000}"
API_BASE="${GITHUB_EMULATOR_API_URL:-${EMULATOR_URL%/}/api/v3}"
ADMIN_TOKEN="${GITHUB_EMULATOR_TOKEN:-${GITHUB_EMULATOR_RUNNER_TOKEN:-}}"
RUNNER_REPO_VALUE="${RUNNER_REPO:-admin/test-repo}"
RUNNER_NAME_VALUE="${RUNNER_NAME:-real-runner-1}"
RUNNER_LABELS_VALUE="${RUNNER_LABELS:-self-hosted,linux}"

if [[ -n "${GITHUB_EMULATOR_PORT80_PROXY_TARGET:-}" ]]; then
  echo "Starting local port-80 proxy to $GITHUB_EMULATOR_PORT80_PROXY_TARGET ..."
  python3 /port80_proxy.py &
fi

if [[ -z "$ADMIN_TOKEN" ]]; then
  echo "GITHUB_EMULATOR_TOKEN is required for registration-token creation" >&2
  exit 2
fi

if [[ "$RUNNER_REPO_VALUE" != */* ]]; then
  echo "RUNNER_REPO must be owner/repo, got: $RUNNER_REPO_VALUE" >&2
  exit 2
fi

echo "Requesting registration token for $RUNNER_REPO_VALUE from $API_BASE ..."
REG_TOKEN=""
for delay in 1 2 4 8 16; do
  response="$(
    curl -skf -X POST "$API_BASE/repos/$RUNNER_REPO_VALUE/actions/runners/registration-token" \
      -H "Authorization: token $ADMIN_TOKEN" || true
  )"
  REG_TOKEN="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token", ""))' 2>/dev/null || true)"
  if [[ -n "$REG_TOKEN" ]]; then
    break
  fi
  echo "Registration token endpoint not ready; retrying in ${delay}s ..." >&2
  sleep "$delay"
done
if [[ -z "$REG_TOKEN" ]]; then
  echo "Failed to obtain a runner registration token" >&2
  exit 1
fi

cleanup() {
  ./config.sh remove --unattended --token "$REG_TOKEN" || true
}
trap cleanup EXIT

if [[ ! -f .runner ]]; then
  ./config.sh \
    --unattended \
    --replace \
    --url "${EMULATOR_URL%/}/$RUNNER_REPO_VALUE" \
    --token "$REG_TOKEN" \
    --name "$RUNNER_NAME_VALUE" \
    --labels "$RUNNER_LABELS_VALUE"
fi

exec ./run.sh

#!/usr/bin/env bash
set -euo pipefail

EMULATOR_URL="${GITHUB_EMULATOR_URL:-http://github-emulator:8000}"
API_BASE="${GITHUB_EMULATOR_API_URL:-${EMULATOR_URL%/}/api/v3}"
ADMIN_TOKEN="${GITHUB_EMULATOR_TOKEN:-${GITHUB_EMULATOR_RUNNER_TOKEN:-}}"
RUNNER_REPO_VALUE="${RUNNER_REPO:-admin/test-repo}"
RUNNER_SCOPE_VALUE="${RUNNER_SCOPE:-repository}"
RUNNER_ENTERPRISE_VALUE="${RUNNER_ENTERPRISE:-breadboard}"
RUNNER_NAME_VALUE="${RUNNER_NAME:-real-runner-1}"
RUNNER_LABELS_VALUE="${RUNNER_LABELS:-self-hosted,linux}"
RUNNER_WORKDIR_VALUE="${RUNNER_WORKDIR:-_work}"

if [[ -n "${GITHUB_EMULATOR_PORT80_PROXY_TARGET:-}" ]]; then
  echo "Starting local port-80 proxy to $GITHUB_EMULATOR_PORT80_PROXY_TARGET ..."
  python3 /port80_proxy.py &
fi

if [[ -z "$ADMIN_TOKEN" ]]; then
  echo "GITHUB_EMULATOR_TOKEN is required for registration-token creation" >&2
  exit 2
fi

if [[ "$RUNNER_SCOPE_VALUE" != "repository" && "$RUNNER_SCOPE_VALUE" != "enterprise" ]]; then
  echo "RUNNER_SCOPE must be repository or enterprise, got: $RUNNER_SCOPE_VALUE" >&2
  exit 2
fi

if [[ "$RUNNER_SCOPE_VALUE" == "repository" && "$RUNNER_REPO_VALUE" != */* ]]; then
  echo "RUNNER_REPO must be owner/repo, got: $RUNNER_REPO_VALUE" >&2
  exit 2
fi

if [[ "$RUNNER_SCOPE_VALUE" == "enterprise" ]]; then
  REGISTRATION_PATH="enterprises/$RUNNER_ENTERPRISE_VALUE/actions/runners"
  CONFIG_URL="${EMULATOR_URL%/}/enterprises/$RUNNER_ENTERPRISE_VALUE"
  SCOPE_DESCRIPTION="enterprise $RUNNER_ENTERPRISE_VALUE"
else
  REGISTRATION_PATH="repos/$RUNNER_REPO_VALUE/actions/runners"
  CONFIG_URL="${EMULATOR_URL%/}/$RUNNER_REPO_VALUE"
  SCOPE_DESCRIPTION="repository $RUNNER_REPO_VALUE"
fi

echo "Requesting registration token for $SCOPE_DESCRIPTION from $API_BASE ..."
REG_TOKEN=""
for delay in 1 2 4 8 16; do
  response="$(
    curl -skf -X POST "$API_BASE/$REGISTRATION_PATH/registration-token" \
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
  response="$(
    curl -skf -X POST "$API_BASE/$REGISTRATION_PATH/remove-token" \
      -H "Authorization: token $ADMIN_TOKEN" || true
  )"
  remove_token="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token", ""))' 2>/dev/null || true)"
  if [[ -n "$remove_token" ]]; then
    ./config.sh remove --unattended --token "$remove_token" || true
  fi
}
trap cleanup EXIT

if [[ ! -f .runner ]]; then
  ./config.sh \
    --unattended \
    --replace \
    --url "$CONFIG_URL" \
    --token "$REG_TOKEN" \
    --name "$RUNNER_NAME_VALUE" \
    --labels "$RUNNER_LABELS_VALUE" \
    --work "$RUNNER_WORKDIR_VALUE"
fi

exec ./run.sh

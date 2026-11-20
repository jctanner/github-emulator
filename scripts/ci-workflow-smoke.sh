#!/usr/bin/env bash
# End-to-end check that a running emulator executes a real workflow run:
# create an org and a repo, push a workflow over git smart HTTP, dispatch it,
# wait for the run, and assert its conclusion and the job's log output.
#
#   scripts/ci-workflow-smoke.sh            # against http://127.0.0.1:8000
#   EMU=http://127.0.0.1:18301 scripts/ci-workflow-smoke.sh
set -euo pipefail

EMU="${EMU:-http://127.0.0.1:8000}"
API="$EMU/api/v3"
TOKEN="${GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN:-ghp_admin_default_token}"
# With CI_STACK_TLS=1, point EMU_CA at the stack's tls/ca.pem.
if [ -n "${EMU_CA:-}" ]; then export CURL_CA_BUNDLE="$EMU_CA" GIT_SSL_CAINFO="$EMU_CA"; fi
GIT_BASE="${EMU%%://*}://admin:$TOKEN@${EMU#*://}"
ORG="${SMOKE_ORG:-smoke-org}"
REPO="${SMOKE_REPO:-smoke-repo}"
MARKER="smoke-marker-$$-$RANDOM"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

api() {
  local method="$1" path="$2"
  shift 2
  curl -sS -X "$method" -H "Authorization: token $TOKEN" \
    -H "Accept: application/vnd.github+json" "$API$path" "$@"
}

echo "== create org $ORG and repo $ORG/$REPO"
api POST /admin/organizations -H 'Content-Type: application/json' \
  -d "{\"login\":\"$ORG\",\"admin\":\"admin\"}" -o /dev/null -w '%{http_code}\n'
api POST "/orgs/$ORG/repos" -H 'Content-Type: application/json' \
  -d "{\"name\":\"$REPO\",\"auto_init\":true}" -o /dev/null -w '%{http_code}\n'

echo "== push the workflow over git"
git -c credential.helper= clone -q "$GIT_BASE/$ORG/$REPO.git" "$WORK/repo"
mkdir -p "$WORK/repo/.github/workflows"
cat >"$WORK/repo/.github/workflows/smoke.yml" <<EOF
name: smoke
on:
  workflow_dispatch:
    inputs:
      marker:
        required: true
jobs:
  produce:
    runs-on: ubuntu-latest
    outputs:
      value: \${{ steps.out.outputs.value }}
    steps:
      - uses: actions/checkout@v4
      - id: out
        run: echo "value=\${{ inputs.marker }}" >> "\$GITHUB_OUTPUT"
  consume:
    needs: produce
    runs-on: ubuntu-latest
    steps:
      - run: echo "got \${{ needs.produce.outputs.value }} on \$RUNNER_OS"
EOF
git -C "$WORK/repo" add .github
git -C "$WORK/repo" -c user.name=smoke -c user.email=smoke@example.com \
  -c commit.gpgsign=false commit -q -m "add smoke workflow"
git -C "$WORK/repo" -c credential.helper= push -q origin HEAD

echo "== dispatch"
api POST "/repos/$ORG/$REPO/actions/workflows/smoke.yml/dispatches" \
  -H 'Content-Type: application/json' \
  -d "{\"ref\":\"main\",\"inputs\":{\"marker\":\"$MARKER\"}}" -o /dev/null -w '%{http_code}\n'

echo "== wait for the run"
run_id=""
for _ in $(seq 1 120); do
  run="$(api GET "/repos/$ORG/$REPO/actions/runs?event=workflow_dispatch" | jq -c '.workflow_runs[0] // empty')"
  if [ -n "$run" ]; then
    run_id="$(jq -r .id <<<"$run")"
    status="$(jq -r .status <<<"$run")"
    if [ "$status" = completed ]; then break; fi
  fi
  sleep 1
done
conclusion="$(api GET "/repos/$ORG/$REPO/actions/runs/$run_id" | jq -r .conclusion)"
echo "run $run_id: $conclusion"

jobs="$(api GET "/repos/$ORG/$REPO/actions/runs/$run_id/jobs")"
jq -r '.jobs[] | "  job \(.name): \(.conclusion)"' <<<"$jobs"
consume_id="$(jq -r '.jobs[] | select(.name=="consume") | .id' <<<"$jobs")"
log="$(api GET "/repos/$ORG/$REPO/actions/jobs/$consume_id/logs" -L)"

if [ "$conclusion" != success ]; then
  echo "FAIL: run concluded $conclusion" >&2
  exit 1
fi
if ! grep -q "got $MARKER on " <<<"$log"; then
  echo "FAIL: consume job log lacks 'got $MARKER on '" >&2
  echo "$log" >&2
  exit 1
fi
echo "PASS: dispatch input crossed a job output into the consume job's log"

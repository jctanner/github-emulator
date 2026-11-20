#!/usr/bin/env bash
# Probe which workflow features the attached runner executes. Each feature is
# its own workflow, so one failure does not hide another. A feature passes when
# its run concludes success AND its log carries the feature's marker.
#
#   EMU=http://127.0.0.1:8000 scripts/ci-capability-probe.sh [report.md]
#
# Prints a markdown table; writes it to the optional report path too.
set -uo pipefail

EMU="${EMU:-http://127.0.0.1:8000}"
API="$EMU/api/v3"
TOKEN="${GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN:-ghp_admin_default_token}"
# With CI_STACK_TLS=1, point EMU_CA at the stack's tls/ca.pem.
if [ -n "${EMU_CA:-}" ]; then export CURL_CA_BUNDLE="$EMU_CA" GIT_SSL_CAINFO="$EMU_CA"; fi
GIT_BASE="${EMU%%://*}://admin:$TOKEN@${EMU#*://}"
ORG="${PROBE_ORG:-probe-org}"
REPO="probe"
REPORT="${1:-}"
WAIT_SECONDS="${PROBE_WAIT_SECONDS:-240}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

api() {
  local method="$1" path="$2"
  shift 2
  curl -sS -X "$method" -H "Authorization: token $TOKEN" \
    -H "Accept: application/vnd.github+json" "$API$path" "$@"
}

git_push_tree() {
  local name="$1" src="$2"
  git -c credential.helper= clone -q "$GIT_BASE/$ORG/$name.git" "$WORK/$name"
  cp -R "$src"/. "$WORK/$name/"
  git -C "$WORK/$name" add -A
  git -C "$WORK/$name" -c user.name=probe -c user.email=probe@example.com \
    -c commit.gpgsign=false commit -q -m "probe content"
  git -C "$WORK/$name" -c credential.helper= push -q origin HEAD
}

api POST /admin/organizations -H 'Content-Type: application/json' \
  -d "{\"login\":\"$ORG\",\"admin\":\"admin\"}" -o /dev/null
for r in "$REPO" probe-actions; do
  api POST "/orgs/$ORG/repos" -H 'Content-Type: application/json' \
    -d "{\"name\":\"$r\",\"auto_init\":true}" -o /dev/null
done

# An action published from another repository on the emulator itself.
mkdir -p "$WORK/src-actions"
cat >"$WORK/src-actions/action.yml" <<'EOF'
name: emulator-hosted
inputs:
  word: {required: true}
runs:
  using: composite
  steps:
    - shell: bash
      run: echo "MARK-emuhost-${{ inputs.word }}"
EOF
git_push_tree probe-actions "$WORK/src-actions"

S="$WORK/src"
mkdir -p "$S/.github/workflows" "$S/.github/actions/hello"
cat >"$S/.github/actions/hello/action.yml" <<'EOF'
name: hello
inputs:
  word: {required: true}
outputs:
  echoed: {value: "${{ steps.e.outputs.v }}"}
runs:
  using: composite
  steps:
    - id: e
      shell: bash
      run: echo "v=${{ inputs.word }}" >> "$GITHUB_OUTPUT"
EOF

wf() { cat >"$S/.github/workflows/$1.yml"; }

wf composite <<'EOF'
on: workflow_dispatch
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - id: h
        uses: ./.github/actions/hello
        with: {word: composite}
      - run: echo "MARK-${{ steps.h.outputs.echoed }}"
EOF

wf callee <<'EOF'
on:
  workflow_call:
    inputs:
      word: {type: string, required: true}
    outputs:
      got: {value: "${{ jobs.inner.outputs.got }}"}
jobs:
  inner:
    runs-on: ubuntu-latest
    outputs:
      got: ${{ steps.s.outputs.got }}
    steps:
      - id: s
        run: echo "got=${{ inputs.word }}" >> "$GITHUB_OUTPUT"
EOF

wf reusable <<'EOF'
on: workflow_dispatch
jobs:
  call:
    uses: ./.github/workflows/callee.yml
    with: {word: reusable}
  after:
    needs: call
    runs-on: ubuntu-latest
    steps:
      - run: echo "MARK-${{ needs.call.outputs.got }}"
EOF

wf emuhost <<EOF
on: workflow_dispatch
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - uses: $ORG/probe-actions@main
        with: {word: ok}
EOF

wf githubcom <<'EOF'
on: workflow_dispatch
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/github-script@v7
        with:
          script: console.log("MARK-githubcom")
EOF

# gh with the job token reaches the emulator, not api.github.com: the repo
# exists only on the emulator. gh speaks only https, so this needs TLS.
wf gh-cli <<'EOF'
on: workflow_dispatch
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - env:
          GH_TOKEN: ${{ github.token }}
        run: |
          name=$(gh api "repos/$GITHUB_REPOSITORY" --jq .full_name)
          [ "$name" = "$GITHUB_REPOSITORY" ] && echo "MARK-gh-cli"
EOF

# Each job starts with an empty workspace, as on a fresh hosted machine:
# nothing job a leaves behind is there for job b.
wf clean-workspace <<'EOF'
on: workflow_dispatch
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - run: echo left > leftover-from-a
  b:
    needs: a
    runs-on: ubuntu-latest
    steps:
      - run: '[ ! -e leftover-from-a ] && echo "MARK-clean-workspace"'
EOF

# A cross-repository call to a repository the emulator does not hold.
wf reusable-missing <<'EOF'
on: workflow_dispatch
jobs:
  call:
    uses: nowhere-org/nothing/.github/workflows/missing.yml@main
EOF

wf container <<'EOF'
on: workflow_dispatch
jobs:
  j:
    runs-on: ubuntu-latest
    container: alpine:3.20
    steps:
      - run: grep -q Alpine /etc/os-release && echo "MARK-container"
EOF

wf oidc <<'EOF'
on: workflow_dispatch
permissions:
  id-token: write
  contents: read
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - run: |
          tok=$(curl -sf -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
            "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=probe-aud" | jq -r .value)
          claims=$(python3 -c 'import base64,sys; p=sys.argv[1].split(".")[1]; print(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)).decode())' "$tok")
          echo "$claims" | jq -c '{aud, repository, ref, iss}'
          echo "$claims" | jq -e '.aud == "probe-aud"' >/dev/null && echo "MARK-oidc"
EOF

wf artifacts <<'EOF'
on: workflow_dispatch
jobs:
  up:
    runs-on: ubuntu-latest
    steps:
      - run: mkdir -p out && echo artifact-payload > out/a.txt
      - uses: actions/upload-artifact@v4
        with: {name: probe-art, path: out}
  down:
    needs: up
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with: {name: probe-art, path: got}
      - run: grep -q artifact-payload got/a.txt && echo "MARK-artifacts"
EOF

# The versions fullsend pins today, by commit: upload-artifact v7.0.2 and
# download-artifact v7.0.0.
wf artifacts-pinned <<'EOF'
on: workflow_dispatch
jobs:
  up:
    runs-on: ubuntu-latest
    steps:
      - run: mkdir -p out/logs && echo pinned-payload > out/logs/a.txt
      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9
        with: {name: probe-pinned, path: out}
  down:
    needs: up
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131
        with: {name: probe-pinned, path: got}
      - run: grep -q pinned-payload got/logs/a.txt && echo "MARK-artifacts-pinned"
EOF

wf concurrency <<'EOF'
on: workflow_dispatch
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - run: sleep 10 && echo "MARK-concurrency"
  b:
    runs-on: ubuntu-latest
    steps:
      - run: sleep 10 && echo "MARK-concurrency"
EOF

git_push_tree "$REPO" "$S"

FEATURES=(composite reusable reusable-missing emuhost githubcom gh-cli clean-workspace container oidc artifacts artifacts-pinned concurrency)

# Features the emulator refuses on purpose, and the message that says so. A
# run that fails with it is the intended result, not a gap.
refusal_for() {
  case "$1" in
    container) echo "not supported by this emulator's runners" ;;
    reusable-missing) echo "was not found on this emulator" ;;
  esac
}

# Dispatch the named workflows and wait until they complete or time out.
dispatch_and_wait() {
  local f st pending deadline
  for f in "$@"; do
    api POST "/repos/$ORG/$REPO/actions/workflows/$f.yml/dispatches" \
      -H 'Content-Type: application/json' -d '{"ref":"main"}' -o /dev/null
  done
  deadline=$(( $(date +%s) + WAIT_SECONDS ))
  while :; do
    runs="$(api GET "/repos/$ORG/$REPO/actions/runs?per_page=50")"
    pending=0
    for f in "$@"; do
      st="$(jq -r --arg p ".github/workflows/$f.yml" \
        '[.workflow_runs[] | select(.path == $p)][0].status // "missing"' <<<"$runs")"
      [ "$st" = completed ] || pending=$((pending + 1))
    done
    [ "$pending" -eq 0 ] && break
    [ "$(date +%s)" -ge "$deadline" ] && break
    sleep 2
  done
}

# Everything but the concurrency check runs together. The concurrency check
# runs alone afterwards, so a busy runner cannot pass for a serial scheduler.
dispatch_and_wait "${FEATURES[@]:0:${#FEATURES[@]}-1}"
dispatch_and_wait concurrency

row() { echo "| $1 | $2 | $3 |"; }
{
  echo "| feature | result | evidence |"
  echo "| --- | --- | --- |"
  for f in "${FEATURES[@]}"; do
    run="$(jq -c --arg p ".github/workflows/$f.yml" \
      '[.workflow_runs[] | select(.path == $p)][0] // empty' <<<"$runs")"
    if [ -z "$run" ]; then row "$f" "fails" "no run was created"; continue; fi
    id="$(jq -r .id <<<"$run")"
    status="$(jq -r .status <<<"$run")"
    conclusion="$(jq -r .conclusion <<<"$run")"
    jobs="$(api GET "/repos/$ORG/$REPO/actions/runs/$id/jobs")"
    logs=""
    for jid in $(jq -r '.jobs[].id' <<<"$jobs"); do
      logs+="$(api GET "/repos/$ORG/$REPO/actions/jobs/$jid/logs" -L)"$'\n'
    done
    if [ -n "${PROBE_LOG_DIR:-}" ]; then
      mkdir -p "$PROBE_LOG_DIR"
      printf '%s\n' "$logs" >"$PROBE_LOG_DIR/$f.log"
      jq '[.jobs[] | {name, runner_name, status, conclusion, started_at, completed_at}]' \
        <<<"$jobs" >"$PROBE_LOG_DIR/$f.jobs.json"
    fi
    if [ "$status" != completed ]; then
      row "$f" "fails" "run $id still $status after ${WAIT_SECONDS}s"
      continue
    fi
    extra=""
    if [ "$f" = concurrency ]; then
      # Overlap: did job b start before job a finished?
      extra="$(jq -r '[.jobs[] | {s: .started_at, c: .completed_at}] | sort_by(.s) |
        if length == 2 and .[1].s < .[0].c then "jobs overlapped" else "jobs ran one after the other" end' <<<"$jobs")"
    fi
    if [ "$conclusion" = success ] && grep -q "MARK-" <<<"$logs"; then
      if [ "$f" = concurrency ] && [ "$extra" != "jobs overlapped" ]; then
        row "$f" "fails" "both jobs passed but $extra"
      else
        row "$f" "works" "run $id success${extra:+, $extra}"
      fi
    else
      # GitHub's log viewer acts on "##[" anywhere in a line, so defuse it.
      why="$(grep -v 'Process completed with exit code' <<<"$logs" |
        grep -m1 -E 'Unsupported action|Error|error|not supported|failed|not found' |
        tr '|' '/' | sed 's/##\[/[/g' | cut -c1-160)"
      refusal="$(refusal_for "$f")"
      if [ -n "$refusal" ] && [ "$conclusion" = failure ] && grep -q "$refusal" <<<"$logs"; then
        row "$f" "refused" "run $id failed as designed: ${why:-$refusal}"
      else
        row "$f" "fails" "run $id $conclusion: ${why:-no marker in log}"
      fi
    fi
  done
} | tee ${REPORT:+"$REPORT"}

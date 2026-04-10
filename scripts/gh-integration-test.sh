#!/usr/bin/env bash
# gh CLI integration test — runs inside the client VM against ghemu.local
set -uo pipefail

GH="/srv/bin/gh"
API="https://ghemu.local/api/v3"
export GH_INSECURE=1
export GH_HOST=ghemu.local

PASS=0
FAIL=0
ERRORS=""

# ── helpers ──────────────────────────────────────────────────────────────────

pass() { PASS=$((PASS + 1)); printf "  \033[32mPASS\033[0m  %s\n" "$1"; }
fail() { FAIL=$((FAIL + 1)); ERRORS="${ERRORS}\n  - $1"; printf "  \033[31mFAIL\033[0m  %s\n" "$1"; }

run_test() {
    local name="$1"; shift
    if output=$("$@" 2>&1); then
        pass "$name"
    else
        fail "$name: $output"
    fi
}

section() { printf "\n\033[1m── %s ──\033[0m\n" "$1"; }

# ── setup ────────────────────────────────────────────────────────────────────

section "Setup"

echo "Creating token..."
TOKEN=$(curl -sk "$API/admin/tokens" \
    -X POST -H "Content-Type: application/json" \
    -d '{"login":"admin","name":"integration-test","scopes":["repo","user","admin:org"]}' \
    | jq -r .token)

if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
    echo "FATAL: could not create token"
    exit 1
fi

mkdir -p ~/.config/gh
printf "ghemu.local:\n  oauth_token: %s\n  user: admin\n" "$TOKEN" > ~/.config/gh/hosts.yml
pass "Token created"

# Allow self-signed certs for git operations
git config --global http.sslVerify false

# Clean up any repos from previous runs
echo "Cleaning up previous test data..."
curl -sk -X DELETE -H "Authorization: token $TOKEN" "$API/repos/admin/test-repo" > /dev/null 2>&1 || true
curl -sk -X DELETE -H "Authorization: token $TOKEN" "$API/repos/admin/private-repo" > /dev/null 2>&1 || true

# ── auth ─────────────────────────────────────────────────────────────────────

section "Auth"

run_test "gh auth status" $GH auth status

output=$($GH auth token 2>&1) || true
if [ -n "$output" ]; then pass "gh auth token"; else fail "gh auth token: empty"; fi

# ── repos ────────────────────────────────────────────────────────────────────

section "Repositories"

run_test "gh repo list (initial)" $GH repo list

run_test "gh repo create test-repo" \
    $GH repo create test-repo --public --description "Integration test repo"

output=$($GH repo list 2>&1) || true
if echo "$output" | grep -q "test-repo"; then
    pass "gh repo list (shows test-repo)"
else
    fail "gh repo list (missing test-repo): $output"
fi

output=$($GH repo view admin/test-repo 2>&1) || true
if echo "$output" | grep -q "test-repo"; then
    pass "gh repo view admin/test-repo"
else
    fail "gh repo view: $output"
fi

run_test "gh repo create private-repo" \
    $GH repo create private-repo --private --description "Private repo"

output=$($GH repo list 2>&1) || true
if echo "$output" | grep -q "private-repo"; then
    pass "gh repo list (shows private-repo)"
else
    fail "gh repo list (missing private-repo): $output"
fi

# ── issues ───────────────────────────────────────────────────────────────────

section "Issues"

run_test "gh issue create" \
    $GH issue create -R admin/test-repo --title "Bug report" --body "Something is broken"

run_test "gh issue create (second)" \
    $GH issue create -R admin/test-repo --title "Feature request" --body "Add something"

output=$($GH issue list -R admin/test-repo 2>&1) || true
if echo "$output" | grep -q "Bug report"; then
    pass "gh issue list (shows issues)"
else
    fail "gh issue list: $output"
fi

output=$($GH issue view 1 -R admin/test-repo 2>&1) || true
if echo "$output" | grep -q "Bug report"; then
    pass "gh issue view 1"
else
    fail "gh issue view: $output"
fi

run_test "gh issue close 1" \
    $GH issue close 1 -R admin/test-repo

output=$($GH issue list -R admin/test-repo --state closed 2>&1) || true
if echo "$output" | grep -q "Bug report"; then
    pass "gh issue list --state closed"
else
    fail "gh issue list --state closed: $output"
fi

run_test "gh issue reopen 1" \
    $GH issue reopen 1 -R admin/test-repo

run_test "gh issue comment" \
    $GH issue comment 1 -R admin/test-repo --body "This is a comment"

# ── labels ───────────────────────────────────────────────────────────────────

section "Labels"

run_test "gh label create" \
    $GH label create bug -R admin/test-repo --description "Something is wrong" --color E11D48

output=$($GH label list -R admin/test-repo 2>&1) || true
if echo "$output" | grep -q "bug"; then
    pass "gh label list (shows bug)"
else
    fail "gh label list: $output"
fi

# ── api (raw) ────────────────────────────────────────────────────────────────

section "Raw API"

output=$($GH api user 2>&1) || true
if echo "$output" | jq -e .login > /dev/null 2>&1; then
    pass "gh api user"
else
    fail "gh api user: $output"
fi

output=$($GH api repos/admin/test-repo 2>&1) || true
if echo "$output" | jq -e .full_name > /dev/null 2>&1; then
    pass "gh api repos/admin/test-repo"
else
    fail "gh api repos/admin/test-repo: $output"
fi

output=$($GH api repos/admin/test-repo/issues 2>&1) || true
if echo "$output" | jq -e '.[0].title' > /dev/null 2>&1; then
    pass "gh api repos/admin/test-repo/issues"
else
    fail "gh api repos/admin/test-repo/issues: $output"
fi

# ── search ───────────────────────────────────────────────────────────────────

section "Search"

output=$($GH search repos test-repo 2>&1) || true
if echo "$output" | grep -q "test-repo"; then
    pass "gh search repos"
else
    fail "gh search repos: $output"
fi

# ── git clone + push ────────────────────────────────────────────────────────

section "Git operations"

CLONE_DIR=$(mktemp -d)
# Clone via HTTPS with token auth
output=$(git clone "https://admin:${TOKEN}@ghemu.local/admin/test-repo.git" "$CLONE_DIR/test-repo" 2>&1) || true
if [ -d "$CLONE_DIR/test-repo/.git" ]; then
    pass "git clone"
else
    fail "git clone: $output"
fi

if [ -d "$CLONE_DIR/test-repo" ]; then
    cd "$CLONE_DIR/test-repo"
    echo "# Test Repo" > README.md
    git add README.md
    git -c user.name="Test" -c user.email="test@test.com" -c commit.gpgsign=false \
        commit -m "initial commit" > /dev/null 2>&1 || true
    output=$(git -c commit.gpgsign=false push origin HEAD:main 2>&1) || true
    if echo "$output" | grep -qE "(->|done|new branch)"; then
        pass "git push"
    else
        fail "git push: $output"
    fi
    cd /
fi

VERIFY_DIR=$(mktemp -d)
git clone "https://admin:${TOKEN}@ghemu.local/admin/test-repo.git" "$VERIFY_DIR/test-repo" 2>/dev/null || true
if [ -f "$VERIFY_DIR/test-repo/README.md" ]; then
    pass "git clone (verify push)"
else
    fail "git clone (verify push): README.md not found"
fi

rm -rf "$CLONE_DIR" "$VERIFY_DIR"

# ── cleanup info ─────────────────────────────────────────────────────────────

section "Summary"

TOTAL=$((PASS + FAIL))
printf "\n  %d tests: \033[32m%d passed\033[0m" "$TOTAL" "$PASS"
if [ "$FAIL" -gt 0 ]; then
    printf ", \033[31m%d failed\033[0m" "$FAIL"
    printf "\n\n  Failures:%b\n" "$ERRORS"
fi
printf "\n"

exit "$FAIL"

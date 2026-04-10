#!/usr/bin/env bash
# git CLI integration test — runs inside the client VM against ghemu.local
set -uo pipefail

API="https://ghemu.local/api/v3"
REPO_NAME="test-git-repo"
REPO_FULL="admin/${REPO_NAME}"

PASS=0
FAIL=0
ERRORS=""
TMPDIRS=()

# ── helpers ──────────────────────────────────────────────────────────────────

pass() { PASS=$((PASS + 1)); printf "  \033[32mPASS\033[0m  %s\n" "$1"; }
fail() { FAIL=$((FAIL + 1)); ERRORS="${ERRORS}\n  - $1"; printf "  \033[31mFAIL\033[0m  %s\n" "$1"; }

section() { printf "\n\033[1m── %s ──\033[0m\n" "$1"; }

mktmp() { local d; d=$(mktemp -d); TMPDIRS+=("$d"); echo "$d"; }

cleanup() {
    for d in "${TMPDIRS[@]}"; do rm -rf "$d"; done
}
trap cleanup EXIT

REPO_URL=""   # set after token is created

# ── setup ────────────────────────────────────────────────────────────────────

section "Setup"

echo "Creating token..."
TOKEN=$(curl -sk "$API/admin/tokens" \
    -X POST -H "Content-Type: application/json" \
    -d '{"login":"admin","name":"git-integration-test","scopes":["repo","user","admin:org"]}' \
    | jq -r .token)

if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
    echo "FATAL: could not create token"
    exit 1
fi
pass "Token created"

REPO_URL="https://admin:${TOKEN}@ghemu.local/${REPO_FULL}.git"
AUTH_HEADER="Authorization: token ${TOKEN}"

# Configure git globals
git config --global http.sslVerify false
git config --global user.name "Git Test"
git config --global user.email "git-test@example.com"
git config --global commit.gpgsign false

# Clean up repos from previous runs
echo "Cleaning up previous test data..."
curl -sk -X DELETE -H "$AUTH_HEADER" "$API/repos/$REPO_FULL" > /dev/null 2>&1 || true

# Create test repo via REST API
echo "Creating test repo..."
CREATE_OUTPUT=$(curl -sk -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
    -d "{\"name\":\"${REPO_NAME}\",\"description\":\"Git integration test repo\",\"auto_init\":false}" \
    "$API/user/repos" 2>&1)

if echo "$CREATE_OUTPUT" | jq -e .full_name > /dev/null 2>&1; then
    pass "Repo created via REST API"
else
    fail "Repo creation: $CREATE_OUTPUT"
    echo "FATAL: could not create test repo"
    exit 1
fi

# ── 1. Clone empty repo ─────────────────────────────────────────────────────

section "1. Clone empty repo"

CLONE1=$(mktmp)
output=$(git clone "$REPO_URL" "$CLONE1/repo" 2>&1) || true
if [ -d "$CLONE1/repo/.git" ]; then
    pass "git clone empty repo"
else
    fail "git clone empty repo: $output"
fi

# ── 2. Initial commit + push ────────────────────────────────────────────────

section "2. Initial commit + push"

WORK=$(mktmp)
git clone "$REPO_URL" "$WORK/repo" 2>/dev/null || true
cd "$WORK/repo"

# Handle both empty and non-empty repos
git checkout -b main 2>/dev/null || true

echo "# Test Git Repo" > README.md
echo "" >> README.md
echo "This repo is used for git integration testing." >> README.md
git add README.md
git commit -m "Initial commit: add README" > /dev/null 2>&1

output=$(git push -u origin main 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "git push origin main"
else
    fail "git push origin main (rc=$rc): $output"
fi
cd /

# ── 3. Clone and verify content ─────────────────────────────────────────────

section "3. Clone and verify content"

VERIFY=$(mktmp)
git clone "$REPO_URL" "$VERIFY/repo" 2>/dev/null
if [ -f "$VERIFY/repo/README.md" ]; then
    content=$(cat "$VERIFY/repo/README.md")
    if echo "$content" | grep -q "Test Git Repo"; then
        pass "Clone contains correct README.md content"
    else
        fail "README.md content mismatch: $content"
    fi
else
    fail "Clone missing README.md"
fi

# ── 4. Multiple commits ─────────────────────────────────────────────────────

section "4. Multiple commits"

MULTI=$(mktmp)
git clone "$REPO_URL" "$MULTI/repo" 2>/dev/null
cd "$MULTI/repo"

echo "file one" > file1.txt
git add file1.txt
git commit -m "Add file1.txt" > /dev/null 2>&1

echo "file two" > file2.txt
git add file2.txt
git commit -m "Add file2.txt" > /dev/null 2>&1

echo "file three" > file3.txt
git add file3.txt
git commit -m "Add file3.txt" > /dev/null 2>&1

output=$(git push origin main 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "Push multiple commits"
else
    fail "Push multiple commits (rc=$rc): $output"
fi

# Verify log count (initial commit + 3 new = 4)
log_count=$(git log --oneline | wc -l)
if [ "$log_count" -ge 4 ]; then
    pass "git log shows $log_count commits (expected >= 4)"
else
    fail "git log shows $log_count commits (expected >= 4)"
fi
cd /

# ── 5. Branching ────────────────────────────────────────────────────────────

section "5. Branching"

BRANCH=$(mktmp)
git clone "$REPO_URL" "$BRANCH/repo" 2>/dev/null
cd "$BRANCH/repo"

git checkout -b feature 2>/dev/null
echo "feature work" > feature.txt
git add feature.txt
git commit -m "Add feature.txt on feature branch" > /dev/null 2>&1

output=$(git push -u origin feature 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "Push feature branch"
else
    fail "Push feature branch (rc=$rc): $output"
fi

# Verify via ls-remote
ls_output=$(git ls-remote --heads origin 2>&1)
if echo "$ls_output" | grep -q "refs/heads/feature"; then
    pass "ls-remote shows feature branch"
else
    fail "ls-remote missing feature branch: $ls_output"
fi
cd /

# ── 6. Fetch + merge ────────────────────────────────────────────────────────

section "6. Fetch + merge"

FETCH=$(mktmp)
git clone "$REPO_URL" "$FETCH/repo" 2>/dev/null
cd "$FETCH/repo"

git fetch origin feature 2>/dev/null
output=$(git merge origin/feature --no-edit 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "Merge origin/feature"
else
    fail "Merge origin/feature (rc=$rc): $output"
fi

if [ -f feature.txt ]; then
    pass "feature.txt present after merge"
else
    fail "feature.txt missing after merge"
fi

if [ -f file1.txt ] && [ -f file2.txt ] && [ -f file3.txt ]; then
    pass "All files present after merge"
else
    fail "Some files missing after merge"
fi
cd /

# ── 7. Lightweight tags ─────────────────────────────────────────────────────

section "7. Lightweight tags"

TAG=$(mktmp)
git clone "$REPO_URL" "$TAG/repo" 2>/dev/null
cd "$TAG/repo"

git tag v1.0
output=$(git push origin v1.0 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "Push lightweight tag v1.0"
else
    fail "Push lightweight tag v1.0 (rc=$rc): $output"
fi

ls_tags=$(git ls-remote --tags origin 2>&1)
if echo "$ls_tags" | grep -q "refs/tags/v1.0"; then
    pass "ls-remote shows tag v1.0"
else
    fail "ls-remote missing tag v1.0: $ls_tags"
fi
cd /

# ── 8. Annotated tags ───────────────────────────────────────────────────────

section "8. Annotated tags"

ATAG=$(mktmp)
git clone "$REPO_URL" "$ATAG/repo" 2>/dev/null
cd "$ATAG/repo"

git tag -a v2.0 -m "Release v2.0"
output=$(git push origin v2.0 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "Push annotated tag v2.0"
else
    fail "Push annotated tag v2.0 (rc=$rc): $output"
fi

ls_tags=$(git ls-remote --tags origin 2>&1)
if echo "$ls_tags" | grep -q "refs/tags/v2.0"; then
    pass "ls-remote shows annotated tag v2.0"
else
    fail "ls-remote missing tag v2.0: $ls_tags"
fi
cd /

# ── 9. REST API content verification ────────────────────────────────────────

section "9. REST API content verification"

api_output=$(curl -sk -H "$AUTH_HEADER" "$API/repos/$REPO_FULL/contents/README.md" 2>&1)
if echo "$api_output" | jq -e .content > /dev/null 2>&1; then
    pass "Contents API returns README.md"
    # Decode base64 and verify content
    api_content=$(echo "$api_output" | jq -r .content | tr -d '\n' | base64 -d 2>/dev/null)
    if echo "$api_content" | grep -q "Test Git Repo"; then
        pass "Contents API base64 content matches"
    else
        fail "Contents API content mismatch: $api_content"
    fi
else
    fail "Contents API error: $api_output"
fi

# ── 10. REST API branch list ────────────────────────────────────────────────

section "10. REST API branch list"

branches_output=$(curl -sk -H "$AUTH_HEADER" "$API/repos/$REPO_FULL/branches" 2>&1)
if echo "$branches_output" | jq -e '.' > /dev/null 2>&1; then
    if echo "$branches_output" | jq -e '.[] | select(.name == "main")' > /dev/null 2>&1; then
        pass "Branches API shows main"
    else
        fail "Branches API missing main: $branches_output"
    fi
    if echo "$branches_output" | jq -e '.[] | select(.name == "feature")' > /dev/null 2>&1; then
        pass "Branches API shows feature"
    else
        fail "Branches API missing feature: $branches_output"
    fi
else
    fail "Branches API error: $branches_output"
fi

# ── 11. REST API commits ────────────────────────────────────────────────────

section "11. REST API commits"

commits_output=$(curl -sk -H "$AUTH_HEADER" "$API/repos/$REPO_FULL/commits" 2>&1)
if echo "$commits_output" | jq -e '.' > /dev/null 2>&1; then
    commit_count=$(echo "$commits_output" | jq 'length')
    if [ "$commit_count" -ge 4 ]; then
        pass "Commits API shows $commit_count commits (expected >= 4)"
    else
        fail "Commits API shows $commit_count commits (expected >= 4)"
    fi

    # Verify commit messages include our known messages
    if echo "$commits_output" | jq -r '.[].commit.message' | grep -q "Add file1.txt"; then
        pass "Commits API contains expected commit message"
    else
        fail "Commits API missing expected commit message"
    fi
else
    fail "Commits API error: $commits_output"
fi

# ── 12. Force push ──────────────────────────────────────────────────────────

section "12. Force push"

FORCE=$(mktmp)
git clone "$REPO_URL" "$FORCE/repo" 2>/dev/null
cd "$FORCE/repo"

# Record the current HEAD
old_head=$(git rev-parse HEAD)

# Amend the last commit
git commit --amend -m "Amended: Add file3.txt" --no-edit --allow-empty > /dev/null 2>&1
new_head=$(git rev-parse HEAD)

output=$(git push --force origin main 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "Force push"
else
    fail "Force push (rc=$rc): $output"
fi

# Verify ref changed
if [ "$old_head" != "$new_head" ]; then
    pass "Force push changed HEAD ($old_head -> $new_head)"
else
    fail "Force push did not change HEAD"
fi
cd /

# ── 13. Delete remote branch ────────────────────────────────────────────────

section "13. Delete remote branch"

DEL=$(mktmp)
git clone "$REPO_URL" "$DEL/repo" 2>/dev/null
cd "$DEL/repo"

output=$(git push origin --delete feature 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "Delete remote branch feature"
else
    fail "Delete remote branch feature (rc=$rc): $output"
fi

# Verify branch is gone
ls_output=$(git ls-remote --heads origin 2>&1)
if echo "$ls_output" | grep -q "refs/heads/feature"; then
    fail "Feature branch still present after delete"
else
    pass "Feature branch removed from ls-remote"
fi
cd /

# ══════════════════════════════════════════════════════════════════════════════
# SSH Transport Tests
# ══════════════════════════════════════════════════════════════════════════════

SSH_REPO_NAME="test-git-ssh-repo"
SSH_REPO_FULL="admin/${SSH_REPO_NAME}"
SSH_REPO_URL="git@ghemu.local:${SSH_REPO_FULL}.git"

section "SSH setup"

# Generate an SSH keypair for the test
SSH_KEY_DIR=$(mktmp)
ssh-keygen -t ed25519 -f "$SSH_KEY_DIR/id_test" -N "" -q
SSH_PUBKEY=$(cat "$SSH_KEY_DIR/id_test.pub")
pass "SSH keypair generated"

# Upload the public key via REST API
key_output=$(curl -sk -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
    -d "{\"title\":\"git-integration-test\",\"key\":\"${SSH_PUBKEY}\"}" \
    "$API/user/keys" 2>&1)
if echo "$key_output" | jq -e .id > /dev/null 2>&1; then
    pass "SSH public key uploaded via API"
else
    fail "SSH public key upload: $key_output"
fi

# Configure SSH for ghemu.local — port 2222, skip host key check, use our key
mkdir -p ~/.ssh
chmod 700 ~/.ssh
cat > ~/.ssh/config <<SSHEOF
Host ghemu.local
    Port 2222
    User git
    IdentityFile ${SSH_KEY_DIR}/id_test
    IdentitiesOnly yes
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
SSHEOF
chmod 600 ~/.ssh/config
pass "SSH config written"

# Clean up and create SSH test repo
curl -sk -X DELETE -H "$AUTH_HEADER" "$API/repos/$SSH_REPO_FULL" > /dev/null 2>&1 || true
CREATE_OUTPUT=$(curl -sk -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
    -d "{\"name\":\"${SSH_REPO_NAME}\",\"description\":\"SSH git integration test repo\",\"auto_init\":false}" \
    "$API/user/repos" 2>&1)
if echo "$CREATE_OUTPUT" | jq -e .full_name > /dev/null 2>&1; then
    pass "SSH test repo created"
else
    fail "SSH test repo creation: $CREATE_OUTPUT"
fi

# ── 14. SSH clone empty repo ────────────────────────────────────────────────

section "14. SSH clone empty repo"

SCLONE=$(mktmp)
output=$(git clone "$SSH_REPO_URL" "$SCLONE/repo" 2>&1) || true
if [ -d "$SCLONE/repo/.git" ]; then
    pass "SSH clone empty repo"
else
    fail "SSH clone empty repo: $output"
fi

# ── 15. SSH initial commit + push ───────────────────────────────────────────

section "15. SSH initial commit + push"

SWORK=$(mktmp)
git clone "$SSH_REPO_URL" "$SWORK/repo" 2>/dev/null || true
cd "$SWORK/repo"

git checkout -b main 2>/dev/null || true

echo "# SSH Test Repo" > README.md
git add README.md
git commit -m "Initial commit via SSH" > /dev/null 2>&1

output=$(git push -u origin main 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "SSH push origin main"
else
    fail "SSH push origin main (rc=$rc): $output"
fi
cd /

# ── 16. SSH clone and verify content ────────────────────────────────────────

section "16. SSH clone and verify content"

SVERIFY=$(mktmp)
git clone "$SSH_REPO_URL" "$SVERIFY/repo" 2>/dev/null
if [ -f "$SVERIFY/repo/README.md" ]; then
    content=$(cat "$SVERIFY/repo/README.md")
    if echo "$content" | grep -q "SSH Test Repo"; then
        pass "SSH clone contains correct README.md"
    else
        fail "SSH README.md content mismatch: $content"
    fi
else
    fail "SSH clone missing README.md"
fi

# ── 17. SSH branching ───────────────────────────────────────────────────────

section "17. SSH branching"

SBRANCH=$(mktmp)
git clone "$SSH_REPO_URL" "$SBRANCH/repo" 2>/dev/null
cd "$SBRANCH/repo"

git checkout -b ssh-feature 2>/dev/null
echo "ssh feature work" > ssh-feature.txt
git add ssh-feature.txt
git commit -m "Add ssh-feature.txt" > /dev/null 2>&1

output=$(git push -u origin ssh-feature 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "SSH push feature branch"
else
    fail "SSH push feature branch (rc=$rc): $output"
fi

ls_output=$(git ls-remote --heads origin 2>&1)
if echo "$ls_output" | grep -q "refs/heads/ssh-feature"; then
    pass "SSH ls-remote shows ssh-feature branch"
else
    fail "SSH ls-remote missing ssh-feature: $ls_output"
fi
cd /

# ── 18. SSH fetch + merge ───────────────────────────────────────────────────

section "18. SSH fetch + merge"

SFETCH=$(mktmp)
git clone "$SSH_REPO_URL" "$SFETCH/repo" 2>/dev/null
cd "$SFETCH/repo"

git fetch origin ssh-feature 2>/dev/null
output=$(git merge origin/ssh-feature --no-edit 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "SSH merge origin/ssh-feature"
else
    fail "SSH merge origin/ssh-feature (rc=$rc): $output"
fi

if [ -f ssh-feature.txt ]; then
    pass "SSH ssh-feature.txt present after merge"
else
    fail "SSH ssh-feature.txt missing after merge"
fi
cd /

# ── 19. SSH tags ────────────────────────────────────────────────────────────

section "19. SSH tags"

STAG=$(mktmp)
git clone "$SSH_REPO_URL" "$STAG/repo" 2>/dev/null
cd "$STAG/repo"

git tag ssh-v1.0
output=$(git push origin ssh-v1.0 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "SSH push tag ssh-v1.0"
else
    fail "SSH push tag ssh-v1.0 (rc=$rc): $output"
fi

ls_tags=$(git ls-remote --tags origin 2>&1)
if echo "$ls_tags" | grep -q "refs/tags/ssh-v1.0"; then
    pass "SSH ls-remote shows tag ssh-v1.0"
else
    fail "SSH ls-remote missing tag ssh-v1.0: $ls_tags"
fi
cd /

# ── 20. SSH force push ──────────────────────────────────────────────────────

section "20. SSH force push"

SFORCE=$(mktmp)
git clone "$SSH_REPO_URL" "$SFORCE/repo" 2>/dev/null
cd "$SFORCE/repo"

old_head=$(git rev-parse HEAD)
git commit --amend -m "Amended via SSH" --allow-empty > /dev/null 2>&1
new_head=$(git rev-parse HEAD)

output=$(git push --force origin main 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "SSH force push"
else
    fail "SSH force push (rc=$rc): $output"
fi

if [ "$old_head" != "$new_head" ]; then
    pass "SSH force push changed HEAD"
else
    fail "SSH force push did not change HEAD"
fi
cd /

# ── 21. SSH delete remote branch ────────────────────────────────────────────

section "21. SSH delete remote branch"

SDEL=$(mktmp)
git clone "$SSH_REPO_URL" "$SDEL/repo" 2>/dev/null
cd "$SDEL/repo"

output=$(git push origin --delete ssh-feature 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    pass "SSH delete remote branch ssh-feature"
else
    fail "SSH delete remote branch ssh-feature (rc=$rc): $output"
fi

ls_output=$(git ls-remote --heads origin 2>&1)
if echo "$ls_output" | grep -q "refs/heads/ssh-feature"; then
    fail "SSH ssh-feature branch still present after delete"
else
    pass "SSH ssh-feature branch removed from ls-remote"
fi
cd /

# ── 22. Cross-protocol: HTTPS clone of SSH-pushed repo ─────────────────────

section "22. Cross-protocol verification"

HTTPS_SSH_URL="https://admin:${TOKEN}@ghemu.local/${SSH_REPO_FULL}.git"
XCROSS=$(mktmp)
git clone "$HTTPS_SSH_URL" "$XCROSS/repo" 2>/dev/null
if [ -f "$XCROSS/repo/README.md" ]; then
    content=$(cat "$XCROSS/repo/README.md")
    if echo "$content" | grep -q "SSH Test Repo"; then
        pass "HTTPS clone of SSH-pushed repo has correct content"
    else
        fail "Cross-protocol content mismatch: $content"
    fi
else
    fail "Cross-protocol clone missing README.md"
fi

# ── Summary ──────────────────────────────────────────────────────────────────

section "Summary"

TOTAL=$((PASS + FAIL))
printf "\n  %d tests: \033[32m%d passed\033[0m" "$TOTAL" "$PASS"
if [ "$FAIL" -gt 0 ]; then
    printf ", \033[31m%d failed\033[0m" "$FAIL"
    printf "\n\n  Failures:%b\n" "$ERRORS"
fi
printf "\n"

exit "$FAIL"

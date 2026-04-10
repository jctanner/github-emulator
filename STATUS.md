# GitHub Emulator — Project Status

Last updated: 2026-04-08

## Phase 1: Skeleton + Auth + Repo CRUD + Git Smart HTTP (MVP)

### Complete
- [x] 1.1 Project skeleton (pyproject.toml, main.py, config.py, database.py)
- [x] 1.2 Core database models (User, PersonalAccessToken, Repository)
- [x] 1.3 Authentication middleware (token/Bearer/Basic auth, SHA-256 hashing)
- [x] 1.4 User & token API (GET/PATCH /user, GET /users/{username}, admin bootstrap endpoints)
- [x] 1.5 Repository API (full CRUD + listing)
- [x] 1.6 Repository JSON response (all 30+ URL template fields, permissions object)
- [x] 1.7 Git Smart HTTP (info/refs, upload-pack, receive-pack; with/without .git suffix)
- [x] 1.8 GitHub-compatible error responses (401/403/404/422 JSON format)
- [x] 1.9 Response headers (X-RateLimit-*, X-GitHub-Media-Type, X-GitHub-Api-Version)
- [x] 1.10 Container setup (Dockerfile, supervisord, docker-compose)
- [x] 1.11 Verification (clone/push/pull verified working in container, pytest passing)

### Bugs found & fixed
- WWW-Authenticate header was stripped by error handler middleware, breaking git push auth (fixed)
- pyproject.toml had invalid build-backend (fixed)
- PR endpoints had MissingGreenlet errors from lazy-loading in async context (fixed with selectinload)
- GraphQL `from __future__ import annotations` incompatible with Strawberry type resolution (fixed)
- Admin user password hashed with SHA-256 instead of bcrypt, causing passlib UnknownHashError on login (fixed)
- passlib + bcrypt>=4.1 incompatibility causing ValueError on startup (fixed by pinning bcrypt<4.1)
- Jinja2 TemplateResponse API change in Starlette 1.0 — `request` must be keyword arg (fixed)

---

## Phase 2: Core REST API (Issues, PRs, Labels, Milestones, Comments)

### Complete
- [x] 2.1 Models (Issue, Label, Milestone, IssueComment, PullRequest, IssueLabel, IssueAssignee)
- [x] 2.2 Endpoints — Issues CRUD (list, create, get, update)
- [x] 2.2 Endpoints — Labels CRUD + issue label management
- [x] 2.2 Endpoints — Milestones CRUD
- [x] 2.2 Endpoints — Issue comments (list, create, get, update, delete)
- [x] 2.2 Endpoints — Pull requests (list, create, get, update, merge, list commits, list files)
- [x] 2.2 Endpoints — PR reviews (list, create, get, submit, dismiss)
- [x] 2.2 Endpoints — PR review comments (list, create, get, update, delete, list per review)
- [x] 2.3 Issue/PR numbering (shared auto-increment per repo via next_issue_number)
- [x] 2.4 PR merge logic — merge endpoint performs real git merge/squash/rebase in bare repo via temp clone

---

## Phase 3: Additional REST API

### Complete
- [x] Branches — list, get, get protection
- [x] Contents API — get/create/update/delete file contents via git commands, auto_init support
- [x] Git Data API — refs, commits, trees, blobs, tags
- [x] Commit statuses — create, get, combined status
- [x] Search — repos, issues, users, code (DB-indexed), commits (DB-indexed)
- [x] Collaborators — list, check, add, remove, get permissions
- [x] Organizations — CRUD, members
- [x] Teams — CRUD, members, repos
- [x] Events API — public, repo, user events
- [x] Webhooks — CRUD, delivery listing
- [x] Forks — create fork (copies bare repo), list forks
- [x] Starring — star/unstar, list stargazers
- [x] Releases & assets
- [x] Commits listing, compare
- [x] Check runs & suites
- [x] Deploy keys
- [x] Notifications
- [x] Reactions
- [x] Gists
- [x] SSH keys — DB-backed (SSHKey model), full CRUD + public listing
- [x] GPG keys — DB-backed (GPGKey model), full CRUD
- [x] Pagination (page/per_page + Link headers)
- [x] Webhook delivery (HMAC signing, delivery records)
- [x] Search indexing — FileContent and CommitMetadata models, automatic indexing on push

### Runtime-verified
68 endpoints tested against running container — all passing. Bugs found and fixed during verification:
- Contents API 500 in container (git identity env vars missing) — fixed
- Contents API PUT returning 200 instead of 201 for new files — fixed
- POST /orgs returning 404 (endpoint was missing) — fixed
- POST reviews returning 200 instead of 201 — fixed

---

## Phase 4: GraphQL API

### Complete
- [x] 4.1 Strawberry GraphQL mounted at /graphql with context_getter (uses FastAPI DI for test compatibility)
- [x] 4.2 Core types (Repository, Issue, PullRequest, User)
- [x] 4.3 Relay connections (generic Connection[T], cursor-based pagination)
- [x] 4.4 Root queries (repository, user, viewer, organization, node, search)
- [x] 4.5 Mutations (createIssue, updateIssue, closeIssue, reopenIssue, addComment, createPullRequest, mergePullRequest, addReaction, createRepository)
- [x] 4.6 Test-verified — 9 tests covering queries, mutations, variables, and auth

---

## Phase 5: Admin Frontend (Jinja2)

### Complete
- [x] Dashboard (system stats: users, repos, issues, PRs, tokens, recent events)
- [x] Users (list, create, edit, delete, reset passwords)
- [x] Tokens (create/revoke PATs, view scopes)
- [x] Repositories (browse, view details, delete)
- [x] Session-based admin auth (JWS-signed cookie)
- [x] PicoCSS styling
- [x] Organizations page (create/manage orgs, edit, delete)
- [x] Issues/PRs browse page (read-only admin view)
- [x] Test-verified — 8 tests covering login, auth, pages, logout

---

## Phase 6: GitHub Actions API (Surface Only)

### Complete
- [x] 6.1 Models (Workflow, WorkflowRun, WorkflowJob, Secret, Variable)
- [x] 6.2 Endpoints (workflows, runs, jobs, secrets, variables)

---

## Phase 7: Refinements

### Complete
- [x] OAuth web flow stubs (/login/oauth/authorize, /login/oauth/access_token)
- [x] Rate limiting (in-memory counters per token/IP, X-RateLimit-* headers)
- [x] node_id generation (base64-encoded "Type:id")
- [x] ETag middleware (computes SHA-1 of response body, honours If-None-Match with 304)
- [x] Git SSH transport (asyncssh-based, public key auth via SSHKey table, git-upload-pack/git-receive-pack)

---

## Phase 8: Gap Fixes

### Complete
- [x] PR review comments API — 6 endpoints (list, create, get, update, delete, list per review)
- [x] SSH/GPG keys — DB-backed models (SSHKey, GPGKey), full CRUD, public key listing
- [x] Search code/commits — DB-indexed (FileContent, CommitMetadata), qualifier parsing (repo:, language:, path:, author:), automatic indexing on push
- [x] Git SSH transport — asyncssh server, public key auth, git-upload-pack/git-receive-pack, auto-generated host key
- [x] GraphQL context fix — uses FastAPI dependency injection, works with test DB overrides
- [x] ETag middleware fix — reads body from StreamingResponse, ETag headers now work correctly
- [x] Repository auto_init — creates initial commit when auto_init=true
- [x] 12 new test files covering all previously untested areas

---

## Infrastructure & Tooling

### Complete
- [x] Dockerfile (Python 3.12-slim, git, supervisord, exposes 8000 + 2222)
- [x] docker-compose.yml (single service, volume mount, HTTP + SSH ports)
- [x] supervisord.conf (uvicorn on port 8000)
- [x] pyproject.toml with dependencies
- [x] requirements.txt (with bcrypt<4.1 pin, asyncssh>=2.14.0)
- [x] Makefile (build, up, down, restart, logs, test, smoke, clean)
- [x] Tests — 219 passing across 22 test files
- [x] Alembic migrations (alembic.ini, env.py, script.py.mako)
- [x] README.md

---

## Test Coverage Summary

| File | Tests | What's covered |
|------|-------|----------------|
| test_auth.py | 7 | token/Bearer/Basic auth, public user, 401 on invalid |
| test_repos_api.py | 9 | repo CRUD, listing, auth required, duplicate, response format |
| test_issues_api.py | 6 | issue CRUD, numbering, state filtering |
| test_pulls_api.py | 15 | PR CRUD, merge, draft, state filtering, shared numbering |
| test_git_http.py | 11 | info/refs, upload-pack, receive-pack, auth, cache headers |
| test_labels_api.py | 15 | label CRUD, issue label management, duplicates |
| test_comments_api.py | 13 | comment CRUD, auth, response shape |
| test_search_api.py | 11 | search repos/issues/users, pagination, response shape |
| test_branches_api.py | 8 | branch listing, retrieval, protection, 404s |
| test_misc_api.py | 21 | emojis, gitignore, licenses, markdown, meta, root, rate_limit |
| test_milestones_api.py | 10 | milestone CRUD, numbering, due dates, state filtering |
| test_contents_api.py | 11 | file get/create/update/delete, base64 encoding, README |
| test_webhooks_api.py | 8 | webhook CRUD, config shape, URL validation |
| test_collaborators_api.py | 8 | add/list/check/remove collaborator, permissions |
| test_forks_api.py | 6 | create fork, list forks, custom name, duplicates |
| test_orgs_api.py | 10 | org CRUD, members, user orgs, response format |
| test_graphql.py | 9 | viewer, repository, user, search, issues, variables |
| test_admin.py | 8 | login page, auth, users, repos, logout |
| test_git_integration.py | 6 | info/refs, upload-pack, auth, 404s |
| test_etag.py | 5 | ETag header, If-None-Match 304, consistency |
| test_review_comments_api.py | 9 | review comment CRUD, validation, response format |
| test_user_keys_api.py | 13 | SSH/GPG key CRUD, public endpoint |
| **Total** | **219** | |

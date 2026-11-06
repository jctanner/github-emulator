"""GraphQL review threads, resolveReviewThread, and minimizeComment.

A review bot re-reviewing a pull request reads its review threads, resolves
the ones it opened that are fixed or outdated, and hides its stale reviews and
summaries. Fullsend's review agent does all three through GraphQL, which is the
only place GitHub exposes threads (github-emulator#20). The emulator's schema
had none of it.
"""

import base64
import hashlib
import secrets

import pytest

from app.models.token import PersonalAccessToken
from app.models.user import User
from tests.conftest import API, auth_headers

pytestmark = pytest.mark.asyncio

REPO = "threads-repo"
ORIGINAL = b"one\ntwo\nthree\n"

THREADS = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        totalCount
        nodes {
          id isResolved isOutdated viewerCanResolve viewerCanUnresolve
          path diffSide line startDiffSide startLine originalLine originalStartLine
          resolvedBy { login __typename }
          comments(first: 100) {
            pageInfo { hasNextPage }
            nodes {
              id databaseId author { login __typename } body createdAt
              outdated viewerDidAuthor url isMinimized minimizedReason
            }
          }
        }
      }
    }
  }
}"""

RESOLVE = "mutation($t: ID!) { resolveReviewThread(input: {threadId: $t}) { thread { id isResolved resolvedBy { login } } } }"
UNRESOLVE = "mutation($t: ID!) { unresolveReviewThread(input: {threadId: $t}) { thread { isResolved resolvedBy { login } } } }"
MINIMIZE = (
    "mutation($id: ID!, $reason: ReportedContentClassifiers!) { minimizeComment(input: {subjectId: $id, classifier: $reason}) "
    "{ minimizedComment { isMinimized minimizedReason viewerCanMinimize } } }"
)
UNMINIMIZE = "mutation($id: ID!) { unminimizeComment(input: {subjectId: $id}) { unminimizedComment { isMinimized minimizedReason } } }"


async def _gql(client, token, query, variables=None):
    response = await client.post(
        "/api/graphql", json={"query": query, "variables": variables or {}}, headers=auth_headers(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _put_file(client, token, content, message, sha=None):
    body = {"message": message, "content": base64.b64encode(content).decode(), "branch": "feature"}
    if sha:
        body["sha"] = sha
    response = await client.put(
        f"{API}/repos/testuser/{REPO}/contents/app.py", json=body, headers=auth_headers(token)
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


async def _pull_request(client, token):
    """A pull request whose head adds app.py, with two threads and a reply."""
    headers = auth_headers(token)
    created = await client.post(f"{API}/user/repos", json={"name": REPO, "auto_init": True}, headers=headers)
    assert created.status_code == 201, created.text
    main = (await client.get(f"{API}/repos/testuser/{REPO}/branches/main", headers=headers)).json()["commit"]["sha"]
    ref = await client.post(
        f"{API}/repos/testuser/{REPO}/git/refs", json={"ref": "refs/heads/feature", "sha": main}, headers=headers
    )
    assert ref.status_code == 201, ref.text
    put = await _put_file(client, token, ORIGINAL, "Add app.py")
    pr = await client.post(
        f"{API}/repos/testuser/{REPO}/pulls",
        json={"title": "Add app", "head": "feature", "base": "main"}, headers=headers,
    )
    assert pr.status_code == 201, pr.text
    number, head = pr.json()["number"], pr.json()["head"]["sha"]
    assert head == put["commit"]["sha"]

    async def comment(**body):
        response = await client.post(
            f"{API}/repos/testuser/{REPO}/pulls/{number}/comments",
            json={"path": "app.py", "commit_id": head, "side": "RIGHT", **body}, headers=headers,
        )
        assert response.status_code == 201, response.text
        return response.json()

    on_two = await comment(body="line two", line=2)
    on_three = await comment(body="line three", line=3)
    reply = await comment(body="a reply", line=2, in_reply_to_id=on_two["id"])
    return number, put["content"]["sha"], on_two, on_three, reply


async def _threads(client, token, number):
    data = await _gql(client, token, THREADS, {"owner": "testuser", "name": REPO, "number": number})
    assert "errors" not in data, data
    return data["data"]["repository"]["pullRequest"]["reviewThreads"]


async def _outsider_token(db_session) -> str:
    user = User(login="outsider", email="outsider@example.com", hashed_password="x", site_admin=False)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    raw = f"ghp_{secrets.token_hex(20)}"
    db_session.add(PersonalAccessToken(
        user_id=user.id, name="outsider", token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        token_prefix=raw[:8], scopes=["repo"],
    ))
    await db_session.commit()
    return raw


async def test_review_threads_group_replies_with_their_root(client, test_user, test_token):
    number, _sha, on_two, on_three, reply = await _pull_request(client, test_token)

    threads = await _threads(client, test_token, number)
    assert threads["totalCount"] == 2
    assert threads["pageInfo"]["hasNextPage"] is False
    first, second = threads["nodes"]
    assert [c["databaseId"] for c in first["comments"]["nodes"]] == [on_two["id"], reply["id"]]
    assert [c["databaseId"] for c in second["comments"]["nodes"]] == [on_three["id"]]
    assert first["path"] == "app.py"
    assert first["diffSide"] == "RIGHT"
    assert (first["line"], first["originalLine"]) == (2, 2)
    assert first["startLine"] is None and first["startDiffSide"] is None
    assert first["isResolved"] is False and first["isOutdated"] is False
    assert first["resolvedBy"] is None
    assert first["viewerCanResolve"] is True and first["viewerCanUnresolve"] is False
    comment = first["comments"]["nodes"][0]
    assert comment["author"] == {"login": "testuser", "__typename": "User"}
    assert comment["body"] == "line two"
    assert comment["viewerDidAuthor"] is True
    assert comment["outdated"] is False
    assert comment["url"].endswith(f"/testuser/{REPO}/pull/{number}#discussion_r{on_two['id']}")
    assert comment["id"] == on_two["node_id"]


async def test_review_threads_paginate(client, test_user, test_token):
    number, *_ = await _pull_request(client, test_token)
    query = THREADS.replace("reviewThreads(first: 100", "reviewThreads(first: 1")

    page = (await _gql(client, test_token, query, {"owner": "testuser", "name": REPO, "number": number}))["data"]
    page = page["repository"]["pullRequest"]["reviewThreads"]
    assert page["pageInfo"]["hasNextPage"] is True
    rest = await _gql(client, test_token, query, {
        "owner": "testuser", "name": REPO, "number": number, "cursor": page["pageInfo"]["endCursor"],
    })
    rest = rest["data"]["repository"]["pullRequest"]["reviewThreads"]
    assert rest["pageInfo"]["hasNextPage"] is False
    assert {page["nodes"][0]["id"], rest["nodes"][0]["id"]} == {t["id"] for t in (await _threads(client, test_token, number))["nodes"]}


async def test_a_thread_is_outdated_when_its_line_changed(client, test_user, test_token):
    number, sha, *_ = await _pull_request(client, test_token)
    # Change line three only; the thread on line two still applies.
    changed = await _put_file(client, test_token, b"one\ntwo\nTHREE\n", "Change line three", sha=sha)
    # A contents-API commit moves the branch but not the open pull request's
    # head (docs/bugs/open/contents-commit-leaves-pull-request-head-stale.md);
    # a push, modelled here by the refs API, does.
    moved = await client.patch(
        f"{API}/repos/testuser/{REPO}/git/refs/heads/feature",
        json={"sha": changed["commit"]["sha"]}, headers=auth_headers(test_token),
    )
    assert moved.status_code == 200, moved.text

    on_two, on_three = (await _threads(client, test_token, number))["nodes"]
    assert on_two["isOutdated"] is False
    assert on_two["line"] == 2
    assert on_three["isOutdated"] is True
    assert on_three["line"] is None
    assert on_three["originalLine"] == 3
    assert on_three["comments"]["nodes"][0]["outdated"] is True


async def test_resolve_and_unresolve_a_thread(client, test_user, test_token):
    number, *_ = await _pull_request(client, test_token)
    thread_id = (await _threads(client, test_token, number))["nodes"][0]["id"]

    resolved = await _gql(client, test_token, RESOLVE, {"t": thread_id})
    assert "errors" not in resolved, resolved
    thread = resolved["data"]["resolveReviewThread"]["thread"]
    assert thread == {"id": thread_id, "isResolved": True, "resolvedBy": {"login": "testuser"}}
    stored = (await _threads(client, test_token, number))["nodes"][0]
    assert stored["isResolved"] is True
    assert stored["viewerCanResolve"] is False and stored["viewerCanUnresolve"] is True

    unresolved = await _gql(client, test_token, UNRESOLVE, {"t": thread_id})
    assert unresolved["data"]["unresolveReviewThread"]["thread"] == {"isResolved": False, "resolvedBy": None}


async def test_resolving_needs_write_access_or_authorship(client, test_user, test_token, db_session):
    number, *_ = await _pull_request(client, test_token)
    thread_id = (await _threads(client, test_token, number))["nodes"][0]["id"]
    outsider = await _outsider_token(db_session)

    assert (await _threads(client, outsider, number))["nodes"][0]["viewerCanResolve"] is False
    denied = await _gql(client, outsider, RESOLVE, {"t": thread_id})
    assert denied["data"] is None or denied["data"]["resolveReviewThread"] is None
    assert denied["errors"][0]["type"] == "FORBIDDEN"
    assert (await _threads(client, test_token, number))["nodes"][0]["isResolved"] is False

    missing = await _gql(client, test_token, RESOLVE, {"t": "UHVsbFJlcXVlc3RSZXZpZXdUaHJlYWQ6OTk5OTk="})
    assert missing["errors"][0]["type"] == "NOT_FOUND"


async def test_a_reply_id_is_not_a_thread(client, test_user, test_token):
    number, _sha, _on_two, _on_three, reply = await _pull_request(client, test_token)
    reply_as_thread = base64.b64encode(f"PullRequestReviewThread:{reply['id']}".encode()).decode()

    response = await _gql(client, test_token, RESOLVE, {"t": reply_as_thread})
    assert response["errors"][0]["type"] == "NOT_FOUND"


async def test_minimize_review_comment_issue_comment_and_review(client, test_user, test_token):
    number, _sha, on_two, *_ = await _pull_request(client, test_token)
    headers = auth_headers(test_token)
    issue_comment = await client.post(
        f"{API}/repos/testuser/{REPO}/issues/{number}/comments", json={"body": "summary"}, headers=headers
    )
    review = await client.post(
        f"{API}/repos/testuser/{REPO}/pulls/{number}/reviews", json={"body": "old review", "event": "COMMENT"},
        headers=headers,
    )
    assert issue_comment.status_code == 201 and review.status_code in (200, 201), (issue_comment.text, review.text)

    # Fullsend passes the REST ``node_id`` straight to the mutation.
    for node_id in (on_two["node_id"], issue_comment.json()["node_id"], review.json()["node_id"]):
        minimized = await _gql(client, test_token, MINIMIZE, {"id": node_id, "reason": "OUTDATED"})
        assert "errors" not in minimized, minimized
        assert minimized["data"]["minimizeComment"]["minimizedComment"] == {
            "isMinimized": True, "minimizedReason": "outdated", "viewerCanMinimize": True,
        }

    comment = (await _threads(client, test_token, number))["nodes"][0]["comments"]["nodes"][0]
    assert (comment["isMinimized"], comment["minimizedReason"]) == (True, "outdated")
    conversation = await _gql(client, test_token, """
        query($n: Int!) { repository(owner: "testuser", name: "threads-repo") { pullRequest(number: $n) {
          comments(first: 10) { nodes { isMinimized minimizedReason } }
          reviews(first: 10) { nodes { isMinimized minimizedReason } } } } }""", {"n": number})
    pull = conversation["data"]["repository"]["pullRequest"]
    assert pull["comments"]["nodes"] == [{"isMinimized": True, "minimizedReason": "outdated"}]
    assert pull["reviews"]["nodes"] == [{"isMinimized": True, "minimizedReason": "outdated"}]

    off_topic = await _gql(client, test_token, MINIMIZE, {"id": on_two["node_id"], "reason": "OFF_TOPIC"})
    assert off_topic["data"]["minimizeComment"]["minimizedComment"]["minimizedReason"] == "off-topic"
    shown = await _gql(client, test_token, UNMINIMIZE, {"id": on_two["node_id"]})
    assert shown["data"]["unminimizeComment"]["unminimizedComment"] == {"isMinimized": False, "minimizedReason": None}


async def test_minimizing_needs_write_access(client, test_user, test_token, db_session):
    number, _sha, on_two, *_ = await _pull_request(client, test_token)
    outsider = await _outsider_token(db_session)

    denied = await _gql(client, outsider, MINIMIZE, {"id": on_two["node_id"], "reason": "SPAM"})
    assert denied["errors"][0]["type"] == "FORBIDDEN"
    comment = (await _threads(client, test_token, number))["nodes"][0]["comments"]["nodes"][0]
    assert comment["isMinimized"] is False

    unknown = await _gql(client, test_token, MINIMIZE, {"id": "bm90LWEtbm9kZQ==", "reason": "SPAM"})
    assert unknown["errors"][0]["type"] == "NOT_FOUND"


# --- Review findings: privacy, credential scope, line mapping, payloads, deletion ---


async def _installation_token(client, admin_token, app_id, permissions, repositories=None, mint=None):
    """An installation token for an App installed on ``testuser``."""
    import time

    from jose import jwt

    created = await client.post(
        f"{API}/admin/apps", headers=auth_headers(admin_token),
        json={"app_id": app_id, "name": f"app-{app_id}", "slug": f"app-{app_id}", "permissions": permissions},
    )
    assert created.status_code == 201, created.text
    installation = await client.post(
        f"{API}/admin/apps/{app_id}/installations", headers=auth_headers(admin_token),
        json={"account_login": "testuser", "account_type": "User",
              "repositories": repositories or [], "permissions": permissions},
    )
    assert installation.status_code == 201, installation.text
    now = int(time.time())
    app_jwt = jwt.encode({"iss": app_id, "iat": now - 10, "exp": now + 60}, created.json()["private_key"], algorithm="RS256")
    token = await client.post(
        f"{API}/app/installations/{installation.json()['id']}/access_tokens",
        headers={"Authorization": f"Bearer {app_jwt}"}, json=mint or {},
    )
    assert token.status_code == 201, token.text
    return token.json()["token"]


async def test_private_repository_threads_are_hidden(client, test_user, test_token, db_session):
    headers = auth_headers(test_token)
    created = await client.post(f"{API}/user/repos", json={"name": REPO, "auto_init": True, "private": True}, headers=headers)
    assert created.status_code == 201, created.text
    pr = await client.post(
        f"{API}/repos/testuser/{REPO}/pulls",
        json={"title": "Secret", "head": "main", "base": "main"}, headers=headers,
    )
    number = pr.json()["number"]
    comment = await client.post(
        f"{API}/repos/testuser/{REPO}/pulls/{number}/comments",
        json={"path": "README.md", "body": "secret review", "line": 1}, headers=headers,
    )
    assert comment.status_code == 201, comment.text
    assert (await _threads(client, test_token, number))["totalCount"] == 1

    outsider = await _outsider_token(db_session)
    query = {"query": THREADS, "variables": {"owner": "testuser", "name": REPO, "number": number}}
    for credential in (outsider, None):
        response = await client.post("/api/graphql", json=query, headers=auth_headers(credential) if credential else {})
        pull = ((response.json().get("data") or {}).get("repository") or {}).get("pullRequest")
        assert pull is None or pull["reviewThreads"]["totalCount"] == 0, response.text


async def test_installation_token_needs_pull_requests_write(client, test_user, test_token, admin_token):
    """Fullsend's review App has contents: read and pull_requests: write."""
    number, _sha, on_two, *_ = await _pull_request(client, test_token)
    thread_id = (await _threads(client, test_token, number))["nodes"][0]["id"]
    review_app = await _installation_token(
        client, admin_token, "3001", {"contents": "read", "pull_requests": "write", "issues": "write", "metadata": "read"}
    )
    issues_only = await _installation_token(client, admin_token, "3002", {"contents": "write", "issues": "write"})
    elsewhere = await _installation_token(
        client, admin_token, "3003", {"pull_requests": "write"}, repositories=["testuser/some-other-repo"]
    )

    assert (await _threads(client, review_app, number))["nodes"][0]["viewerCanResolve"] is True
    resolved = await _gql(client, review_app, RESOLVE, {"t": thread_id})
    assert "errors" not in resolved, resolved
    minimized = await _gql(client, review_app, MINIMIZE, {"id": on_two["node_id"], "reason": "OUTDATED"})
    assert "errors" not in minimized, minimized

    # contents: write is not the permission that governs review conversations.
    assert (await _gql(client, issues_only, UNRESOLVE, {"t": thread_id}))["errors"][0]["type"] == "FORBIDDEN"
    assert (await _gql(client, issues_only, UNMINIMIZE, {"id": on_two["node_id"]}))["errors"][0]["type"] == "FORBIDDEN"
    # A token minted for another repository does not see this one.
    assert (await _gql(client, elsewhere, UNRESOLVE, {"t": thread_id}))["errors"][0]["type"] == "NOT_FOUND"
    assert (await _threads(client, elsewhere, number))["totalCount"] == 0


async def test_job_token_cannot_minimize_beyond_its_job(client, db_session, test_user, test_token):
    """The run's actor owns the repository, but the token is the job's.

    Job tokens cannot use GraphQL mutations at all yet
    (docs/bugs/open/job-tokens-cannot-use-graphql.md); this pins that a job
    declaring pull-requests: read never acts with its actor's access.
    """
    from app.models.actions import Workflow, WorkflowJob, WorkflowRun
    from app.services.job_token_service import issue_job_token

    number, _sha, on_two, *_ = await _pull_request(client, test_token)
    repo_id = (await client.get(f"{API}/repos/testuser/{REPO}", headers=auth_headers(test_token))).json()["id"]
    workflow = Workflow(repo_id=repo_id, name="review", path=".github/workflows/review.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(workflow_id=workflow.id, repo_id=repo_id, head_sha="x", head_branch="feature",
                      event="pull_request", status="in_progress", run_number=1, actor_id=test_user.id)
    db_session.add(run)
    await db_session.flush()
    readonly = WorkflowJob(run_id=run.id, name="read", status="in_progress", permissions={"pull-requests": "read"})
    db_session.add(readonly)
    await db_session.commit()

    denied = await _gql(client, issue_job_token(readonly), MINIMIZE, {"id": on_two["node_id"], "reason": "SPAM"})
    assert denied.get("errors"), denied
    comment = (await _threads(client, test_token, number))["nodes"][0]["comments"]["nodes"][0]
    assert comment["isMinimized"] is False


async def test_viewer_can_unminimize(client, test_user, test_token, db_session):
    number, _sha, on_two, *_ = await _pull_request(client, test_token)
    query = """query($n: Int!) { repository(owner: "testuser", name: "threads-repo") { pullRequest(number: $n) {
      reviewThreads(first: 10) { nodes { comments(first: 10) { nodes { viewerCanMinimize viewerCanUnminimize } } } } } } }"""

    def first_comment(data):
        return data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]["comments"]["nodes"][0]

    assert first_comment(await _gql(client, test_token, query, {"n": number})) == {
        "viewerCanMinimize": True, "viewerCanUnminimize": False,
    }
    await _gql(client, test_token, MINIMIZE, {"id": on_two["node_id"], "reason": "OUTDATED"})
    assert first_comment(await _gql(client, test_token, query, {"n": number}))["viewerCanUnminimize"] is True
    outsider = await _outsider_token(db_session)
    assert first_comment(await _gql(client, outsider, query, {"n": number})) == {
        "viewerCanMinimize": False, "viewerCanUnminimize": False,
    }


async def test_a_line_that_moved_is_not_outdated(client, test_user, test_token):
    number, sha, *_ = await _pull_request(client, test_token)
    # Insert a line above both commented lines; their text is unchanged.
    changed = await _put_file(client, test_token, b"zero\none\ntwo\nthree\n", "Insert a line", sha=sha)
    moved = await client.patch(
        f"{API}/repos/testuser/{REPO}/git/refs/heads/feature",
        json={"sha": changed["commit"]["sha"]}, headers=auth_headers(test_token),
    )
    assert moved.status_code == 200, moved.text

    on_two, on_three = (await _threads(client, test_token, number))["nodes"]
    assert (on_two["isOutdated"], on_two["line"], on_two["originalLine"]) == (False, 3, 2)
    assert (on_three["isOutdated"], on_three["line"], on_three["originalLine"]) == (False, 4, 3)


async def test_minimize_payload_reports_the_comment_as_threads_do(client, test_user, test_token):
    number, _sha, on_two, *_ = await _pull_request(client, test_token)
    mutation = (
        "mutation($id: ID!) { minimizeComment(input: {subjectId: $id, classifier: OUTDATED}) { minimizedComment {"
        " ... on PullRequestReviewComment { viewerDidAuthor url outdated line } } } }"
    )
    payload = await _gql(client, test_token, mutation, {"id": on_two["node_id"]})
    assert "errors" not in payload, payload
    comment = payload["data"]["minimizeComment"]["minimizedComment"]
    assert comment["viewerDidAuthor"] is True
    assert comment["url"].endswith(f"#discussion_r{on_two['id']}")
    assert (comment["outdated"], comment["line"]) == (False, 2)


async def test_deleting_a_root_comment_keeps_its_thread(client, test_user, test_token):
    number, _sha, on_two, _on_three, reply = await _pull_request(client, test_token)
    second_reply = await client.post(
        f"{API}/repos/testuser/{REPO}/pulls/{number}/comments",
        json={"path": "app.py", "line": 2, "body": "second reply", "in_reply_to_id": on_two["id"]},
        headers=auth_headers(test_token),
    )
    thread_id = (await _threads(client, test_token, number))["nodes"][0]["id"]
    await _gql(client, test_token, RESOLVE, {"t": thread_id})

    deleted = await client.delete(
        f"{API}/repos/testuser/{REPO}/pulls/comments/{on_two['id']}", headers=auth_headers(test_token)
    )
    assert deleted.status_code == 204

    threads = (await _threads(client, test_token, number))["nodes"]
    assert len(threads) == 2
    survivor = next(t for t in threads if t["comments"]["nodes"][0]["databaseId"] == reply["id"])
    assert [c["databaseId"] for c in survivor["comments"]["nodes"]] == [reply["id"], second_reply.json()["id"]]
    assert survivor["isResolved"] is True
    unresolved = await _gql(client, test_token, UNRESOLVE, {"t": survivor["id"]})
    assert "errors" not in unresolved, unresolved


async def test_installation_token_on_a_private_repository(client, test_user, test_token, admin_token):
    """An installed App sees a private repository's threads if it may read pull requests."""
    headers = auth_headers(test_token)
    created = await client.post(f"{API}/user/repos", json={"name": REPO, "auto_init": True, "private": True}, headers=headers)
    assert created.status_code == 201, created.text
    pr = await client.post(f"{API}/repos/testuser/{REPO}/pulls", json={"title": "P", "head": "main", "base": "main"}, headers=headers)
    number = pr.json()["number"]
    comment = await client.post(
        f"{API}/repos/testuser/{REPO}/pulls/{number}/comments",
        json={"path": "README.md", "body": "private review", "line": 1}, headers=headers,
    )
    assert comment.status_code == 201, comment.text
    reader = await _installation_token(client, admin_token, "3101", {"pull_requests": "read", "metadata": "read"})
    writer = await _installation_token(client, admin_token, "3102", {"pull_requests": "write", "metadata": "read"})
    metadata_only = await _installation_token(client, admin_token, "3103", {"metadata": "read", "issues": "write"})

    # REST agrees: the installation covers the private repository.
    rest = await client.get(f"{API}/repos/testuser/{REPO}", headers=auth_headers(metadata_only))
    assert rest.status_code == 200, rest.text
    readable = await _threads(client, reader, number)
    assert readable["totalCount"] == 1
    assert readable["nodes"][0]["viewerCanResolve"] is False
    assert (await _gql(client, reader, RESOLVE, {"t": readable["nodes"][0]["id"]}))["errors"][0]["type"] == "FORBIDDEN"
    resolved = await _gql(client, writer, RESOLVE, {"t": readable["nodes"][0]["id"]})
    assert "errors" not in resolved, resolved
    # Covered, but without a pull request permission: no review threads.
    assert (await _threads(client, metadata_only, number))["totalCount"] == 0
    assert (await _gql(client, metadata_only, UNRESOLVE, {"t": readable["nodes"][0]["id"]}))["errors"][0]["type"] == "FORBIDDEN"


async def test_promoted_root_keeps_the_threads_location(client, test_user, test_token):
    number, sha, on_two, _on_three, _reply = await _pull_request(client, test_token)
    # The line is changed, so the thread is outdated at the new head.
    changed = await _put_file(client, test_token, b"one\nTWO\nthree\n", "Change line two", sha=sha)
    await client.patch(
        f"{API}/repos/testuser/{REPO}/git/refs/heads/feature",
        json={"sha": changed["commit"]["sha"]}, headers=auth_headers(test_token),
    )
    # A reply that omits its location defaults to the new head commit.
    late = await client.post(
        f"{API}/repos/testuser/{REPO}/pulls/{number}/comments",
        json={"path": "app.py", "body": "late reply", "in_reply_to_id": on_two["id"]},
        headers=auth_headers(test_token),
    )
    assert late.status_code == 201, late.text
    before = next(t for t in (await _threads(client, test_token, number))["nodes"] if t["originalLine"] == 2)
    assert before["isOutdated"] is True

    await client.delete(f"{API}/repos/testuser/{REPO}/pulls/comments/{on_two['id']}", headers=auth_headers(test_token))
    after = next(t for t in (await _threads(client, test_token, number))["nodes"] if t["path"] == "app.py" and t["originalLine"] == 2)
    assert after["isOutdated"] is True
    assert after["line"] is None

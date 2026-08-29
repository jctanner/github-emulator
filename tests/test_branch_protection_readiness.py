"""Branch-protection and shared merge-readiness regression tests."""

import hashlib
import secrets

import pytest
from sqlalchemy import select

from app.models.branch import Branch
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.token import PersonalAccessToken
from app.models.user import User
from tests.conftest import auth_headers


API = "/api/v3"


async def _graphql(client, token, query, variables):
    response = await client.post(
        "/graphql",
        json={"query": query, "variables": variables},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    payload = response.json()
    assert "errors" not in payload, payload.get("errors")
    return payload["data"]


async def _readiness(client, token, repo_name="protected-repo"):
    data = await _graphql(
        client,
        token,
        """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $number) {
              merged
              mergeStateStatus
              reviewDecision
              autoMergeRequest { mergeMethod }
            }
          }
        }
        """,
        {"owner": "testuser", "repo": repo_name, "number": 1},
    )
    return data["repository"]["pullRequest"]


async def _create_reviewer(db_session):
    reviewer = User(
        login="reviewer",
        hashed_password=hashlib.sha256(b"password").hexdigest(),
        name="Reviewer",
        email="reviewer@test.com",
    )
    db_session.add(reviewer)
    await db_session.flush()
    raw_token = f"ghp_{secrets.token_hex(20)}"
    db_session.add(
        PersonalAccessToken(
            user_id=reviewer.id,
            name="reviewer-token",
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            token_prefix=raw_token[:8],
            scopes=["repo", "user"],
        )
    )
    await db_session.commit()
    return reviewer, raw_token


async def _create_protected_pr(client, db_session, test_token, repo_name="protected-repo"):
    response = await client.post(
        f"{API}/user/repos",
        json={"name": repo_name},
        headers=auth_headers(test_token),
    )
    assert response.status_code == 201
    repository = (
        await db_session.execute(
            select(Repository).where(Repository.full_name == f"testuser/{repo_name}")
        )
    ).scalar_one()
    db_session.add_all(
        [
            Branch(repo_id=repository.id, name="main", sha="b" * 40),
            Branch(repo_id=repository.id, name="feature", sha="h" * 40),
        ]
    )
    await db_session.commit()

    response = await client.post(
        f"{API}/repos/testuser/{repo_name}/pulls",
        json={"title": "Protected change", "head": "feature", "base": "main"},
        headers=auth_headers(test_token),
    )
    assert response.status_code == 201
    pull_request = (
        await db_session.execute(
            select(PullRequest)
            .join(Issue, PullRequest.issue_id == Issue.id)
            .where(PullRequest.repo_id == repository.id, Issue.number == 1)
        )
    ).scalar_one()
    pull_request.head_sha = "h" * 40
    pull_request.base_sha = "b" * 40
    pull_request.mergeable = True
    await db_session.commit()
    return repository, pull_request, response.json()


def _protection_body(**overrides):
    body = {
        "required_status_checks": {"strict": False, "contexts": ["ci/test"]},
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "required_approving_review_count": 1,
            "require_last_push_approval": False,
        },
        "restrictions": None,
        "required_linear_history": False,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_branch_protection_can_be_configured_inspected_and_removed(
    client, db_session, test_user, test_token
):
    await _create_protected_pr(client, db_session, test_token)
    response = await client.put(
        f"{API}/repos/testuser/protected-repo/branches/main/protection",
        json=_protection_body(required_linear_history=True, allow_force_pushes=True),
        headers=auth_headers(test_token),
    )
    assert response.status_code == 200
    protection = response.json()
    assert protection["required_status_checks"]["contexts"] == ["ci/test"]
    assert protection["required_pull_request_reviews"]["dismiss_stale_reviews"] is True
    assert protection["required_linear_history"] == {"enabled": True}
    assert protection["allow_force_pushes"] == {"enabled": True}

    branch = await client.get(
        f"{API}/repos/testuser/protected-repo/branches/main",
        headers=auth_headers(test_token),
    )
    assert branch.status_code == 200
    assert branch.json()["protected"] is True

    unsupported = await client.put(
        f"{API}/repos/testuser/protected-repo/branches/main/protection",
        json=_protection_body(required_conversation_resolution=True),
        headers=auth_headers(test_token),
    )
    assert unsupported.status_code == 422
    assert "conversation resolution" in unsupported.json()["message"]

    deleted = await client.delete(
        f"{API}/repos/testuser/protected-repo/branches/main/protection",
        headers=auth_headers(test_token),
    )
    assert deleted.status_code == 204
    missing = await client.get(
        f"{API}/repos/testuser/protected-repo/branches/main/protection",
        headers=auth_headers(test_token),
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_reviews_and_status_checks_drive_merge_state_and_rest_merge(
    client, db_session, test_user, test_token
):
    await _create_protected_pr(client, db_session, test_token)
    _, reviewer_token = await _create_reviewer(db_session)
    configured = await client.put(
        f"{API}/repos/testuser/protected-repo/branches/main/protection",
        json=_protection_body(),
        headers=auth_headers(test_token),
    )
    assert configured.status_code == 200

    assert await _readiness(client, test_token) == {
        "merged": False,
        "mergeStateStatus": "BLOCKED",
        "reviewDecision": "REVIEW_REQUIRED",
        "autoMergeRequest": None,
    }
    blocked = await client.put(
        f"{API}/repos/testuser/protected-repo/pulls/1/merge",
        json={"merge_method": "squash"},
        headers=auth_headers(test_token),
    )
    assert blocked.status_code == 405
    assert blocked.json()["merged"] is False

    status = await client.post(
        f"{API}/repos/testuser/protected-repo/statuses/{'h' * 40}",
        json={"state": "success", "context": "ci/test"},
        headers=auth_headers(test_token),
    )
    assert status.status_code == 201
    review = await client.post(
        f"{API}/repos/testuser/protected-repo/pulls/1/reviews",
        json={"event": "APPROVE", "body": "ready"},
        headers=auth_headers(reviewer_token),
    )
    assert review.status_code == 201
    readiness = await _readiness(client, test_token)
    assert readiness["mergeStateStatus"] == "CLEAN"
    assert readiness["reviewDecision"] == "APPROVED"

    merged = await client.put(
        f"{API}/repos/testuser/protected-repo/pulls/1/merge",
        json={"merge_method": "squash"},
        headers=auth_headers(test_token),
    )
    assert merged.status_code == 200
    assert merged.json()["merged"] is True


@pytest.mark.asyncio
async def test_auto_merge_remains_queued_until_policy_requirements_pass(
    client, db_session, test_user, test_token
):
    _, _, pr_json = await _create_protected_pr(client, db_session, test_token)
    _, reviewer_token = await _create_reviewer(db_session)
    await client.put(
        f"{API}/repos/testuser/protected-repo/branches/main/protection",
        json=_protection_body(),
        headers=auth_headers(test_token),
    )

    queued = await _graphql(
        client,
        test_token,
        """
        mutation($input: EnablePullRequestAutoMergeInput!) {
          enablePullRequestAutoMerge(input: $input) {
            pullRequest {
              merged
              mergeStateStatus
              autoMergeRequest {
                mergeMethod
                authorEmail
                enabledBy { login }
              }
              commits(last: 1) { nodes { commit { oid } } }
            }
          }
        }
        """,
        {"input": {"pullRequestId": pr_json["node_id"], "mergeMethod": "SQUASH"}},
    )
    assert queued["enablePullRequestAutoMerge"]["pullRequest"] == {
        "merged": False,
        "mergeStateStatus": "BLOCKED",
        "autoMergeRequest": {
            "mergeMethod": "SQUASH",
            "authorEmail": test_user.email,
            "enabledBy": {"login": test_user.login},
        },
        "commits": {"nodes": [{"commit": {"oid": "h" * 40}}]},
    }

    await client.post(
        f"{API}/repos/testuser/protected-repo/statuses/{'h' * 40}",
        json={"state": "success", "context": "ci/test"},
        headers=auth_headers(test_token),
    )
    still_queued = await _readiness(client, test_token)
    assert still_queued["merged"] is False
    assert still_queued["autoMergeRequest"] == {"mergeMethod": "SQUASH"}

    await client.post(
        f"{API}/repos/testuser/protected-repo/pulls/1/reviews",
        json={"event": "APPROVE"},
        headers=auth_headers(reviewer_token),
    )
    completed = await _readiness(client, test_token)
    assert completed["merged"] is True
    assert completed["autoMergeRequest"] is None


@pytest.mark.asyncio
async def test_stale_reviews_strict_base_and_conflicts_have_distinct_states(
    client, db_session, test_user, test_token
):
    repository, pull_request, _ = await _create_protected_pr(
        client, db_session, test_token
    )
    _, reviewer_token = await _create_reviewer(db_session)
    await client.put(
        f"{API}/repos/testuser/protected-repo/branches/main/protection",
        json=_protection_body(
            required_status_checks={"strict": True, "contexts": []}
        ),
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/repos/testuser/protected-repo/pulls/1/reviews",
        json={"event": "APPROVE"},
        headers=auth_headers(reviewer_token),
    )
    assert (await _readiness(client, test_token))["mergeStateStatus"] == "CLEAN"

    pull_request.head_sha = "n" * 40
    pull_request.last_push_by_id = test_user.id
    await db_session.commit()
    stale = await _readiness(client, test_token)
    assert stale["mergeStateStatus"] == "BLOCKED"
    assert stale["reviewDecision"] == "REVIEW_REQUIRED"

    await client.post(
        f"{API}/repos/testuser/protected-repo/pulls/1/reviews",
        json={"event": "APPROVE"},
        headers=auth_headers(reviewer_token),
    )
    assert (await _readiness(client, test_token))["mergeStateStatus"] == "CLEAN"

    base = (
        await db_session.execute(
            select(Branch).where(Branch.repo_id == repository.id, Branch.name == "main")
        )
    ).scalar_one()
    base.sha = "c" * 40
    await db_session.commit()
    assert (await _readiness(client, test_token))["mergeStateStatus"] == "BEHIND"

    pull_request.mergeable = False
    await db_session.commit()
    assert (await _readiness(client, test_token))["mergeStateStatus"] == "DIRTY"


@pytest.mark.asyncio
async def test_protected_branch_rejects_forced_ref_updates_and_deletion(
    client, test_user, test_token, test_repo_with_init
):
    configured = await client.put(
        f"{API}/repos/testuser/init-repo/branches/main/protection",
        json=_protection_body(
            required_status_checks=None,
            required_pull_request_reviews=None,
        ),
        headers=auth_headers(test_token),
    )
    assert configured.status_code == 200
    ref = await client.get(
        f"{API}/repos/testuser/init-repo/git/ref/heads/main",
        headers=auth_headers(test_token),
    )
    assert ref.status_code == 200
    sha = ref.json()["object"]["sha"]

    forced = await client.patch(
        f"{API}/repos/testuser/init-repo/git/refs/heads/main",
        json={"sha": sha, "force": True},
        headers=auth_headers(test_token),
    )
    assert forced.status_code == 422
    assert "force-update" in forced.json()["message"]

    deleted = await client.delete(
        f"{API}/repos/testuser/init-repo/git/refs/heads/main",
        headers=auth_headers(test_token),
    )
    assert deleted.status_code == 422
    assert "delete" in deleted.json()["message"]


@pytest.mark.asyncio
async def test_admin_enforcement_last_push_approval_and_linear_history(
    client, db_session, test_user, test_token
):
    await _create_protected_pr(client, db_session, test_token)
    _, reviewer_token = await _create_reviewer(db_session)
    configured = await client.put(
        f"{API}/repos/testuser/protected-repo/branches/main/protection",
        json=_protection_body(
            required_status_checks=None,
            enforce_admins=False,
            required_linear_history=True,
            required_pull_request_reviews={
                "required_approving_review_count": 1,
                "require_last_push_approval": True,
            },
        ),
        headers=auth_headers(test_token),
    )
    assert configured.status_code == 200

    # Repository administrators bypass review requirements until enforcement
    # is enabled, while ordinary viewers still see the policy block.
    assert (await _readiness(client, test_token))["mergeStateStatus"] == "CLEAN"
    reviewer_view = await _readiness(client, reviewer_token)
    assert reviewer_view["mergeStateStatus"] == "BLOCKED"
    assert reviewer_view["reviewDecision"] == "REVIEW_REQUIRED"

    enabled = await client.post(
        f"{API}/repos/testuser/protected-repo/branches/main/protection/enforce_admins",
        headers=auth_headers(test_token),
    )
    assert enabled.status_code == 200
    assert (await _readiness(client, test_token))["mergeStateStatus"] == "BLOCKED"

    approved = await client.post(
        f"{API}/repos/testuser/protected-repo/pulls/1/reviews",
        json={"event": "APPROVE"},
        headers=auth_headers(reviewer_token),
    )
    assert approved.status_code == 201

    merge_commit = await client.put(
        f"{API}/repos/testuser/protected-repo/pulls/1/merge",
        json={"merge_method": "merge"},
        headers=auth_headers(test_token),
    )
    assert merge_commit.status_code == 405
    assert "linear history" in merge_commit.json()["message"]

    squash = await client.put(
        f"{API}/repos/testuser/protected-repo/pulls/1/merge",
        json={"merge_method": "squash"},
        headers=auth_headers(test_token),
    )
    assert squash.status_code == 200
    assert squash.json()["merged"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("required_conversation_resolution", True, "conversation resolution"),
        ("required_signatures", True, "signed commits"),
        ("required_deployments", ["production"], "deployments"),
        ("required_merge_queue", {"enabled": True}, "merge queues"),
        ("block_creations", True, "branch creation"),
        ("lock_branch", True, "locking branches"),
        ("allow_fork_syncing", True, "fork branch syncing"),
    ],
)
async def test_unimplemented_rules_are_rejected_explicitly(
    client, db_session, test_user, test_token, field, value, message
):
    await _create_protected_pr(client, db_session, test_token)
    response = await client.put(
        f"{API}/repos/testuser/protected-repo/branches/main/protection",
        json=_protection_body(**{field: value}),
        headers=auth_headers(test_token),
    )
    assert response.status_code == 422
    assert message in response.json()["message"]

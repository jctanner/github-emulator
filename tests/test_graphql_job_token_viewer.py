"""A job token's GraphQL viewer is github-actions[bot], as on GitHub.

GitHub's GITHUB_TOKEN is an installation token of the GitHub Actions App, so
`{ viewer { login } }` answers the `github-actions[bot]` Bot. The emulator
answered the run's actor, the user who triggered the run, with that user's
login, id and repositories (docs/bugs/fixed/graphql-job-token-viewer-is-the-run-actor.md).
"""

import pytest
from sqlalchemy import func, select

from app.models.actions import Workflow, WorkflowJob, WorkflowRun
from app.models.repository import Repository
from app.models.user import User
from app.services.job_token_service import issue_job_token
from tests.conftest import API, auth_headers

pytestmark = pytest.mark.asyncio

VIEWER = "{ viewer { __typename login databaseId name repositories(first: 10) { totalCount nodes { name } } } }"


async def _gql(client, token, query):
    response = await client.post("/api/graphql", json={"query": query}, headers=auth_headers(token))
    assert response.status_code == 200, response.text
    return response.json()


async def _job_token(client, db_session, test_user, test_token, permissions=None):
    """A job token for a run test_user triggered in test_user's repository."""
    created = await client.post(f"{API}/user/repos", json={"name": "jobviewer"}, headers=auth_headers(test_token))
    assert created.status_code == 201, created.text
    repo = (await db_session.execute(select(Repository).where(Repository.name == "jobviewer"))).scalar_one()
    workflow = Workflow(repo_id=repo.id, name="W", path=".github/workflows/w.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id, repo_id=repo.id, head_sha="abc", head_branch="main",
        event="workflow_dispatch", run_number=1, actor_id=test_user.id,
    )
    db_session.add(run)
    await db_session.flush()
    job = WorkflowJob(run_id=run.id, name="j", permissions=permissions)
    db_session.add(job)
    await db_session.commit()
    return issue_job_token(job)


async def test_a_job_tokens_viewer_is_the_github_actions_bot(client, db_session, test_user, test_token):
    token = await _job_token(client, db_session, test_user, test_token)

    body = await _gql(client, token, "{ viewer { login __typename } }")
    assert "errors" not in body, body
    assert body["data"]["viewer"] == {"login": "github-actions[bot]", "__typename": "Bot"}

    # The account is created once and reused.
    one = await _gql(client, token, "{ viewer { databaseId } }")
    two = await _gql(client, token, "{ viewer { databaseId } }")
    assert one["data"]["viewer"]["databaseId"] == two["data"]["viewer"]["databaseId"]
    count = (await db_session.execute(
        select(func.count()).select_from(User).where(User.login == "github-actions[bot]")
    )).scalar_one()
    assert count == 1


async def test_a_job_tokens_viewer_id_resolves_through_node_with_the_same_login(
    client, db_session, test_user, test_token
):
    token = await _job_token(client, db_session, test_user, test_token)

    body = await _gql(client, token, "{ viewer { id login } }")
    assert "errors" not in body, body
    viewer = body["data"]["viewer"]
    assert viewer["login"] == "github-actions[bot]"

    node = await client.post(
        "/api/graphql",
        json={
            "query": "query($id: ID!) { node(id: $id) { __typename id ... on Bot { login } } }",
            "variables": {"id": viewer["id"]},
        },
        headers=auth_headers(token),
    )
    result = node.json()
    assert "errors" not in result, result
    assert result["data"]["node"] == {
        "__typename": "Bot", "id": viewer["id"], "login": "github-actions[bot]",
    }


async def test_a_job_tokens_viewer_shows_nothing_of_the_runs_actor(client, db_session, test_user, test_token):
    token = await _job_token(client, db_session, test_user, test_token)

    body = await _gql(client, token, VIEWER)
    assert "errors" not in body, body
    viewer = body["data"]["viewer"]
    assert viewer["login"] == "github-actions[bot]"
    assert viewer["databaseId"] != test_user.id
    assert viewer["name"] == "GitHub Actions"
    # test_user owns jobviewer; the bot owns nothing.
    assert viewer["repositories"] == {"totalCount": 0, "nodes": []}


async def test_a_user_tokens_viewer_is_unchanged(client, test_user, test_token):
    created = await client.post(f"{API}/user/repos", json={"name": "mine"}, headers=auth_headers(test_token))
    assert created.status_code == 201, created.text

    body = await _gql(client, test_token, VIEWER)
    assert "errors" not in body, body
    assert body["data"]["viewer"] == {
        "__typename": "User", "login": "testuser", "databaseId": test_user.id, "name": test_user.name,
        "repositories": {"totalCount": 1, "nodes": [{"name": "mine"}]},
    }
    fields = await _gql(client, test_token, "{ viewer { id email isSiteAdmin createdAt ... on User { id } } }")
    assert "errors" not in fields, fields


async def test_a_job_token_with_a_permissions_block_is_unchanged(client, db_session, test_user, test_token):
    """Refused GraphQL at the chokepoint, so it runs anonymously
    (docs/bugs/open/job-tokens-cannot-use-graphql.md)."""
    token = await _job_token(client, db_session, test_user, test_token, permissions={"contents": "read"})

    body = await _gql(client, token, "{ viewer { login } }")
    assert body.get("errors") and not body.get("data")

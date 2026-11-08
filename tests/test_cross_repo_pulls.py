"""Pull requests from a fork (BT-E5).

A pull request's head may be a branch of another repository in the base's
fork network. GitHub opens one with GraphQL ``createPullRequest`` and
``headRepositoryId`` (Fullsend's behaviour suite does, for a fork in the same
organization) or REST ``head: "owner:branch"``. The base repository carries
the head as ``refs/pull/N/head``; a push to the fork's branch moves the pull
request and raises ``synchronize`` in the base repository; payloads name the
fork as ``head.repo``, which is how workflows tell a fork apart.
"""

import base64

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.actions import WorkflowRun
from tests.conftest import API, auth_headers
from tests.test_update_refs_graphql import WORKFLOW

ORG = "fork-org"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


@pytest_asyncio.fixture
async def base(client, test_user, test_token):
    headers = auth_headers(test_token)
    assert (await client.post(f"{API}/orgs", json={"login": ORG}, headers=headers)).status_code == 201
    created = await client.post(f"{API}/orgs/{ORG}/repos", json={"name": "base", "auto_init": True}, headers=headers)
    assert created.status_code == 201, created.text
    put = await client.put(
        f"{API}/repos/{ORG}/base/contents/.github/workflows/sync.yml", headers=headers,
        json={"message": "add workflow", "content": _b64(WORKFLOW), "branch": "main"},
    )
    assert put.status_code in (200, 201), put.text
    return created.json()


async def _fork(client, token, name="base-fork", organization=ORG):
    body = {"name": name}
    if organization:
        body["organization"] = organization
    return await client.post(f"{API}/repos/{ORG}/base/forks", json=body, headers=auth_headers(token))


async def _branch_with_file(client, token, repo_full, branch, path="fork.txt", text="from the fork\n"):
    headers = auth_headers(token)
    main = (await client.get(f"{API}/repos/{repo_full}/git/ref/heads/main", headers=headers)).json()["object"]["sha"]
    ref = await client.post(f"{API}/repos/{repo_full}/git/refs", headers=headers,
                            json={"ref": f"refs/heads/{branch}", "sha": main})
    assert ref.status_code == 201, ref.text
    put = await client.put(f"{API}/repos/{repo_full}/contents/{path}", headers=headers,
                           json={"message": f"add {path}", "content": _b64(text), "branch": branch})
    assert put.status_code in (200, 201), put.text
    return put.json()["commit"]["sha"]


async def _graphql_pull(client, token, base_node, head_node, branch):
    query = """mutation($input: CreatePullRequestInput!) {
      createPullRequest(input: $input) { pullRequest { number } } }"""
    return await client.post("/api/graphql", headers=auth_headers(token), json={
        "query": query,
        "variables": {"input": {"repositoryId": base_node, "headRepositoryId": head_node,
                                "headRefName": branch, "baseRefName": "main", "title": "from a fork"}},
    })


@pytest.mark.asyncio
async def test_a_fork_into_an_organization_is_the_organizations(client, base, test_token):
    fork = await _fork(client, test_token)
    assert fork.status_code == 202, fork.text
    body = fork.json()
    assert body["full_name"] == f"{ORG}/base-fork"
    assert body["owner"]["login"] == ORG and body["owner"]["type"] == "Organization"
    assert body["fork"] is True
    # Asking again returns the same fork.
    again = await _fork(client, test_token)
    assert again.status_code == 202 and again.json()["id"] == body["id"]


@pytest.mark.asyncio
async def test_only_a_member_forks_into_an_organization(client, base, db_session):
    from tests.test_org_memberships_api import _make_user

    outsider = await _make_user(db_session, "outsider")
    resp = await _fork(client, outsider, name="sneaky")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_graphql_opens_a_pull_request_from_a_fork_in_the_same_organization(client, base, test_token, db_session):
    headers = auth_headers(test_token)
    fork = (await _fork(client, test_token)).json()
    head_sha = await _branch_with_file(client, test_token, fork["full_name"], "topic")

    created = await _graphql_pull(client, test_token, base["node_id"], fork["node_id"], "topic")
    assert "errors" not in created.json(), created.json()
    number = created.json()["data"]["createPullRequest"]["pullRequest"]["number"]

    pull = (await client.get(f"{API}/repos/{ORG}/base/pulls/{number}", headers=headers)).json()
    assert pull["head"]["repo"]["full_name"] == fork["full_name"]
    assert pull["head"]["repo"]["fork"] is True
    assert pull["base"]["repo"]["full_name"] == f"{ORG}/base"
    assert pull["head"]["ref"] == "topic"
    assert pull["head"]["sha"] == head_sha
    assert pull["head"]["label"] == f"{ORG}:topic"
    # The base repository holds the head: diff and files work from it.
    files = (await client.get(f"{API}/repos/{ORG}/base/pulls/{number}/files", headers=headers)).json()
    assert [item["filename"] for item in files] == ["fork.txt"]
    assert pull["commits"] == 1
    # The opened event's payload names the fork as the head repository.
    run = (await db_session.execute(
        select(WorkflowRun).where(WorkflowRun.event == "pull_request_target")
    )).scalars().all()
    assert run == []  # the fixture's workflow listens for synchronize only


@pytest.mark.asyncio
async def test_a_push_to_the_forks_branch_synchronizes_the_pull_request(client, base, test_token, db_session):
    headers = auth_headers(test_token)
    fork = (await _fork(client, test_token)).json()
    await _branch_with_file(client, test_token, fork["full_name"], "topic")
    created = await _graphql_pull(client, test_token, base["node_id"], fork["node_id"], "topic")
    number = created.json()["data"]["createPullRequest"]["pullRequest"]["number"]

    put = await client.put(f"{API}/repos/{fork['full_name']}/contents/more.txt", headers=headers,
                           json={"message": "more", "content": _b64("more\n"), "branch": "topic"})
    second = put.json()["commit"]["sha"]

    pull = (await client.get(f"{API}/repos/{ORG}/base/pulls/{number}", headers=headers)).json()
    assert pull["head"]["sha"] == second
    assert pull["commits"] == 2
    runs = (await db_session.execute(
        select(WorkflowRun).where(WorkflowRun.event == "pull_request_target")
    )).scalars().all()
    assert len(runs) == 1
    payload = runs[0].trigger_payload
    assert payload["pull_request"]["head"]["repo"]["full_name"] == fork["full_name"]
    assert payload["pull_request"]["base"]["repo"]["full_name"] == f"{ORG}/base"
    assert payload["repository"]["owner"]["login"] == ORG


@pytest.mark.asyncio
async def test_a_base_branch_named_like_the_forks_does_not_move_the_forks_pull_request(client, base, test_token):
    headers = auth_headers(test_token)
    fork = (await _fork(client, test_token)).json()
    head_sha = await _branch_with_file(client, test_token, fork["full_name"], "topic")
    created = await _graphql_pull(client, test_token, base["node_id"], fork["node_id"], "topic")
    number = created.json()["data"]["createPullRequest"]["pullRequest"]["number"]

    await _branch_with_file(client, test_token, f"{ORG}/base", "topic", path="base.txt")
    pull = (await client.get(f"{API}/repos/{ORG}/base/pulls/{number}", headers=headers)).json()
    assert pull["head"]["sha"] == head_sha


@pytest.mark.asyncio
async def test_rest_opens_a_pull_request_from_a_users_fork(client, base, test_token):
    headers = auth_headers(test_token)
    fork = await _fork(client, test_token, name="mine", organization=None)
    assert fork.status_code == 202, fork.text
    assert fork.json()["full_name"] == "testuser/mine"
    head_sha = await _branch_with_file(client, test_token, "testuser/mine", "patch")

    pull = await client.post(f"{API}/repos/{ORG}/base/pulls", headers=headers,
                             json={"title": "user fork", "head": "testuser:patch", "base": "main"})
    assert pull.status_code == 201, pull.text
    body = pull.json()
    assert body["head"]["repo"]["full_name"] == "testuser/mine"
    assert body["head"]["sha"] == head_sha
    assert body["head"]["label"] == "testuser:patch"

    # A head no repository of the network holds is refused.
    bad = await client.post(f"{API}/repos/{ORG}/base/pulls", headers=headers,
                            json={"title": "nope", "head": "nobody:patch", "base": "main"})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_a_repository_outside_the_network_is_refused(client, base, test_token):
    headers = auth_headers(test_token)
    other = await client.post(f"{API}/orgs/{ORG}/repos", json={"name": "unrelated", "auto_init": True}, headers=headers)
    await _branch_with_file(client, test_token, f"{ORG}/unrelated", "topic")
    created = await _graphql_pull(client, test_token, base["node_id"], other.json()["node_id"], "topic")
    assert "not in the same network" in created.json()["errors"][0]["message"]


@pytest.mark.asyncio
async def test_a_same_repository_pull_request_still_reports_its_repository(client, base, test_token):
    headers = auth_headers(test_token)
    await _branch_with_file(client, test_token, f"{ORG}/base", "local")
    pull = await client.post(f"{API}/repos/{ORG}/base/pulls", headers=headers,
                             json={"title": "local", "head": "local", "base": "main"})
    body = pull.json()
    assert body["head"]["repo"]["full_name"] == body["base"]["repo"]["full_name"] == f"{ORG}/base"


@pytest.mark.asyncio
async def test_a_fork_pull_request_merges(client, base, test_token):
    headers = auth_headers(test_token)
    fork = (await _fork(client, test_token)).json()
    await _branch_with_file(client, test_token, fork["full_name"], "topic")
    created = await _graphql_pull(client, test_token, base["node_id"], fork["node_id"], "topic")
    number = created.json()["data"]["createPullRequest"]["pullRequest"]["number"]
    for method in ("merge",):
        merged = await client.put(f"{API}/repos/{ORG}/base/pulls/{number}/merge", headers=headers,
                                  json={"merge_method": method})
        assert merged.status_code == 200, merged.text
        assert merged.json()["merged"] is True
    content = await client.get(f"{API}/repos/{ORG}/base/contents/fork.txt", headers=headers)
    assert content.status_code == 200


@pytest.mark.asyncio
async def test_an_installation_token_cannot_borrow_a_head_from_another_account(client, base, test_token, admin_token):
    """An App installed on two accounts, with a token for one, cannot open a
    pull request from a fork on the other: the token's own scope decides."""
    headers = auth_headers(test_token)
    assert (await client.post(f"{API}/orgs", json={"login": "other-org"}, headers=headers)).status_code == 201
    fork = (await _fork(client, test_token, name="elsewhere", organization="other-org")).json()
    await _branch_with_file(client, test_token, fork["full_name"], "topic")
    created = await client.post(f"{API}/admin/apps", headers=auth_headers(admin_token), json={
        "app_id": "3001", "name": "two-orgs", "slug": "two-orgs",
        "permissions": {"contents": "write", "pull_requests": "write", "metadata": "read"}})
    assert created.status_code == 201
    ids = {}
    for account in (ORG, "other-org"):
        inst = await client.post(f"{API}/admin/apps/3001/installations", headers=auth_headers(admin_token),
                                 json={"account_login": account, "account_type": "Organization", "repositories": []})
        ids[account] = inst.json()["id"]
    token = (await client.post(f"{API}/admin/apps/3001/installations/{ids[ORG]}/access_tokens",
                               headers=auth_headers(admin_token), json={})).json()["token"]
    bot = {"Authorization": f"token {token}"}
    rest = await client.post(f"{API}/repos/{ORG}/base/pulls", headers=bot,
                             json={"title": "x", "head": "other-org:topic", "head_repo": "elsewhere", "base": "main"})
    assert rest.status_code == 422, rest.text
    query = """mutation($input: CreatePullRequestInput!) {
      createPullRequest(input: $input) { pullRequest { number } } }"""
    graphql = await client.post("/api/graphql", headers=bot, json={"query": query, "variables": {"input": {
        "repositoryId": base["node_id"], "headRepositoryId": fork["node_id"],
        "headRefName": "topic", "baseRefName": "main", "title": "x"}}})
    assert graphql.json().get("errors"), graphql.json()

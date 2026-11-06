"""GraphQL hides private repositories from callers who cannot read them.

REST refuses a private repository to a caller who may not read it, and holds
an installation token to the repositories it was minted for. GraphQL's root
fields did neither: an anonymous query returned a private repository's issue
titles (docs/bugs/fixed/graphql-repository-visibility.md). A hidden repository
now resolves like one that does not exist, wherever GraphQL reaches it.
"""

import base64
import hashlib
import secrets
import time

import pytest
from jose import jwt

from app.models.token import PersonalAccessToken
from app.models.user import User
from tests.conftest import API, auth_headers

pytestmark = pytest.mark.asyncio


def _node(kind: str, db_id: int) -> str:
    return base64.b64encode(f"{kind}:{db_id}".encode()).decode()


async def _gql(client, query, token=None, variables=None):
    headers = auth_headers(token) if token else {}
    response = await client.post("/api/graphql", json={"query": query, "variables": variables or {}}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _user_token(db_session, login: str) -> tuple[User, str]:
    user = User(login=login, email=f"{login}@example.com", hashed_password="x", site_admin=False)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    raw = f"ghp_{secrets.token_hex(20)}"
    db_session.add(PersonalAccessToken(
        user_id=user.id, name=login, token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        token_prefix=raw[:8], scopes=["repo"],
    ))
    await db_session.commit()
    return user, raw


async def _fixture(client, token):
    """A private repository with an issue and a pull request, and a public one."""
    headers = auth_headers(token)
    for name, private in (("hidden", True), ("shown", False)):
        created = await client.post(
            f"{API}/user/repos", json={"name": name, "auto_init": True, "private": private}, headers=headers
        )
        assert created.status_code == 201, created.text
    issue = await client.post(f"{API}/repos/testuser/hidden/issues", json={"title": "secret needle"}, headers=headers)
    pull = await client.post(
        f"{API}/repos/testuser/hidden/pulls", json={"title": "secret pull", "head": "main", "base": "main"}, headers=headers
    )
    repo = await client.get(f"{API}/repos/testuser/hidden", headers=headers)
    assert issue.status_code == 201 and pull.status_code == 201, (issue.text, pull.text)
    return repo.json()["id"], issue.json()["id"], pull.json()["id"]


async def _readers(client, db_session, token):
    """(label, credential, may see the private repository) for each kind of caller."""
    _outsider, outsider = await _user_token(db_session, "outsider")
    _collaborator, collaborator = await _user_token(db_session, "collaborator")
    added = await client.put(
        f"{API}/repos/testuser/hidden/collaborators/collaborator", json={"permission": "pull"},
        headers=auth_headers(token),
    )
    assert added.status_code in (201, 204), added.text
    return [("anonymous", None, False), ("outsider", outsider, False),
            ("collaborator", collaborator, True), ("owner", token, True)]


async def test_repository_and_its_issues(client, test_user, test_token, db_session):
    await _fixture(client, test_token)
    query = '{ repository(owner: "testuser", name: "hidden") { name issues(first: 5) { nodes { title } } } }'

    for label, credential, may_see in await _readers(client, db_session, test_token):
        data = await _gql(client, query, credential)
        repository = data["data"]["repository"]
        if may_see:
            assert {"title": "secret needle"} in repository["issues"]["nodes"], label
        else:
            # Exactly what a repository that does not exist returns.
            assert repository is None, (label, data)
    missing = await _gql(client, '{ repository(owner: "testuser", name: "no-such-repo") { name } }')
    assert missing == await _gql(client, '{ repository(owner: "testuser", name: "hidden") { name } }')


async def test_node_lookups(client, test_user, test_token, db_session):
    repo_id, issue_id, pull_id = await _fixture(client, test_token)
    from sqlalchemy import select

    from app.models.issue import Issue
    from app.models.pull_request import PullRequest

    issue_db_id = (await db_session.execute(select(Issue.id).where(Issue.title == "secret needle"))).scalar_one()
    pull_db_id = (await db_session.execute(select(PullRequest.id))).scalars().first()
    query = """query($r: ID!, $i: ID!, $p: ID!) {
      r: node(id: $r) { ... on Repository { name } }
      i: node(id: $i) { ... on Issue { title } }
      p: node(id: $p) { ... on PullRequest { title } } }"""
    variables = {"r": _node("Repository", repo_id), "i": _node("Issue", issue_db_id), "p": _node("PullRequest", pull_db_id)}

    for label, credential, may_see in await _readers(client, db_session, test_token):
        data = (await _gql(client, query, credential, variables))["data"]
        if may_see:
            assert (data["r"], data["i"], data["p"]) == (
                {"name": "hidden"}, {"title": "secret needle"}, {"title": "secret pull"}
            ), label
        else:
            assert (data["r"], data["i"], data["p"]) == (None, None, None), label


async def test_search(client, test_user, test_token, db_session):
    await _fixture(client, test_token)
    repos = '{ search(query: "hidden", type: REPOSITORY, first: 10) { repositoryCount nodes { ... on Repository { name } } } }'
    issues = '{ search(query: "secret", type: ISSUE, first: 10) { issueCount nodes { ... on Issue { title } ... on PullRequest { title } } } }'

    for label, credential, may_see in await _readers(client, db_session, test_token):
        found_repos = (await _gql(client, repos, credential))["data"]["search"]
        found_issues = (await _gql(client, issues, credential))["data"]["search"]
        if may_see:
            assert {"name": "hidden"} in found_repos["nodes"], label
            assert {n["title"] for n in found_issues["nodes"]} == {"secret needle", "secret pull"}, label
        else:
            assert found_repos["nodes"] == [] and found_repos["repositoryCount"] == 0, label
            assert found_issues["nodes"] == [] and found_issues["issueCount"] == 0, label


async def test_owner_repository_lists(client, test_user, test_token, db_session):
    await _fixture(client, test_token)
    query = '{ user(login: "testuser") { repositories(first: 10) { totalCount nodes { name } } } }'

    for label, credential, may_see in await _readers(client, db_session, test_token):
        listed = (await _gql(client, query, credential))["data"]["user"]["repositories"]
        names = {node["name"] for node in listed["nodes"]}
        assert "shown" in names, label
        assert ("hidden" in names) is may_see, label
        assert listed["totalCount"] == len(names), label


async def test_organization_repository_list(client, test_user, test_token, db_session):
    headers = auth_headers(test_token)
    assert (await client.post(f"{API}/orgs", json={"login": "vis-org"}, headers=headers)).status_code == 201
    for name, private in (("org-hidden", True), ("org-shown", False)):
        created = await client.post(f"{API}/orgs/vis-org/repos", json={"name": name, "private": private}, headers=headers)
        assert created.status_code == 201, created.text
    _outsider, outsider = await _user_token(db_session, "outsider")
    query = '{ organization(login: "vis-org") { repositories(first: 10) { nodes { name } } } }'

    def names(data):
        return {node["name"] for node in data["data"]["organization"]["repositories"]["nodes"]}

    # The organization's creator is a member.
    assert names(await _gql(client, query, test_token)) == {"org-hidden", "org-shown"}
    assert names(await _gql(client, query, outsider)) == {"org-shown"}
    assert names(await _gql(client, query)) == {"org-shown"}


async def test_installation_token_sees_only_what_it_was_minted_for(client, test_user, test_token, admin_token):
    await _fixture(client, test_token)
    tokens = {}
    for app_id, repositories in (("4001", ["testuser/hidden"]), ("4002", ["testuser/shown"])):
        created = await client.post(
            f"{API}/admin/apps", headers=auth_headers(admin_token),
            json={"app_id": app_id, "name": f"vis-{app_id}", "slug": f"vis-{app_id}", "permissions": {"issues": "read"}},
        )
        installation = await client.post(
            f"{API}/admin/apps/{app_id}/installations", headers=auth_headers(admin_token),
            json={"account_login": "testuser", "account_type": "User", "repositories": repositories,
                  "permissions": {"issues": "read"}},
        )
        now = int(time.time())
        app_jwt = jwt.encode({"iss": app_id, "iat": now - 10, "exp": now + 60}, created.json()["private_key"], algorithm="RS256")
        minted = await client.post(
            f"{API}/app/installations/{installation.json()['id']}/access_tokens",
            headers={"Authorization": f"Bearer {app_jwt}"}, json={},
        )
        assert minted.status_code == 201, minted.text
        tokens[app_id] = minted.json()["token"]
    query = '{ repository(owner: "testuser", name: "hidden") { name } }'

    assert (await _gql(client, query, tokens["4001"]))["data"]["repository"] == {"name": "hidden"}
    assert (await _gql(client, query, tokens["4002"]))["data"]["repository"] is None


async def test_closing_reference_to_a_private_issue(client, test_user, test_token, db_session):
    await _fixture(client, test_token)
    headers = auth_headers(test_token)
    issue_number = (await client.get(f"{API}/repos/testuser/hidden/issues/1", headers=headers)).json()["number"]
    pull = await client.post(
        f"{API}/repos/testuser/shown/pulls",
        json={"title": "public pull", "head": "main", "base": "main", "body": f"Fixes testuser/hidden#{issue_number}"},
        headers=headers,
    )
    assert pull.status_code == 201, pull.text
    query = """query($n: Int!) { repository(owner: "testuser", name: "shown") { pullRequest(number: $n) {
      closingIssuesReferences(first: 5) { nodes { title } } } } }"""
    variables = {"n": pull.json()["number"]}
    _outsider, outsider = await _user_token(db_session, "outsider")

    def titles(data):
        return [node["title"] for node in data["data"]["repository"]["pullRequest"]["closingIssuesReferences"]["nodes"]]

    assert titles(await _gql(client, query, test_token, variables)) == ["secret needle"]
    assert titles(await _gql(client, query, outsider, variables)) == []


# --- Mutations and nested fields reach hidden repositories too ---


async def _issue_node(db_session, title: str) -> str:
    from sqlalchemy import select

    from app.models.issue import Issue

    return _node("Issue", (await db_session.execute(select(Issue.id).where(Issue.title == title))).scalar_one())


async def test_mutations_treat_a_hidden_target_as_missing(client, test_user, test_token, db_session):
    await _fixture(client, test_token)
    _outsider, outsider = await _user_token(db_session, "outsider")
    issue_id = await _issue_node(db_session, "secret needle")
    missing_id = _node("Issue", 999999)
    repo_id = _node("Repository", (await client.get(f"{API}/repos/testuser/hidden", headers=auth_headers(test_token))).json()["id"])

    cases = [
        ("mutation($id: ID!) { updateIssue(input: {id: $id}) { issue { title body } } }", {}),
        ("mutation($id: ID!) { closeIssue(input: {issueId: $id}) { issue { title } } }", {}),
        ("mutation($id: ID!) { addComment(input: {subjectId: $id, body: \"x\"}) { subject { id } } }", {}),
        ("mutation($id: ID!) { addReaction(input: {subjectId: $id, content: \"HEART\"}) { subjectId } }", {}),
    ]
    for mutation, extra in cases:
        hidden = await _gql(client, mutation, outsider, {"id": issue_id, **extra})
        absent = await _gql(client, mutation, outsider, {"id": missing_id, **extra})
        assert hidden.get("errors"), (mutation, hidden)
        assert "secret" not in str(hidden), (mutation, hidden)
        # The same failure as an id that names nothing, apart from the id.
        assert hidden["errors"][0]["message"].replace(issue_id, "?").split(" with id ")[0] == \
            absent["errors"][0]["message"].split(" with id ")[0], (mutation, hidden, absent)
    created = await _gql(
        client, "mutation($r: ID!) { createIssue(input: {repositoryId: $r, title: \"probe\"}) { issue { url } } }",
        outsider, {"r": repo_id},
    )
    assert created.get("errors") and "hidden" not in str(created), created

    # Nothing changed.
    state = await client.get(f"{API}/repos/testuser/hidden/issues/1", headers=auth_headers(test_token))
    assert state.json()["state"] == "open" and state.json()["comments"] == 0
    assert (await client.get(f"{API}/repos/testuser/hidden/issues/2", headers=auth_headers(test_token))).json()["title"] != "probe"


async def test_labels_and_milestones_must_belong_to_the_issues_repository(client, test_user, test_token, db_session):
    await _fixture(client, test_token)
    headers = auth_headers(test_token)
    secret_label = await client.post(f"{API}/repos/testuser/hidden/labels", json={"name": "secret-label"}, headers=headers)
    secret_milestone = await client.post(f"{API}/repos/testuser/hidden/milestones", json={"title": "secret milestone"}, headers=headers)
    assert secret_label.status_code == 201 and secret_milestone.status_code == 201
    _outsider, outsider = await _user_token(db_session, "outsider")
    repo = (await client.get(f"{API}/repos/testuser/shown", headers=headers)).json()
    own = await _gql(
        client, "mutation($r: ID!) { createIssue(input: {repositoryId: $r, title: \"mine\"}) { issue { id } } }",
        outsider, {"r": _node("Repository", repo["id"])},
    )
    issue_id = own["data"]["createIssue"]["issue"]["id"]
    label_id = _node("Label", secret_label.json()["id"])
    milestone_id = _node("Milestone", secret_milestone.json()["id"])

    for mutation in (
        "mutation($i: ID!, $l: ID!) { updateIssue(input: {id: $i, labelIds: [$l]}) { issue { labels(first: 5) { nodes { name } } } } }",
        "mutation($i: ID!, $l: ID!) { updateIssue(input: {id: $i, milestoneId: $l}) { issue { milestone { title } } } }",
    ):
        target = label_id if "labelIds" in mutation else milestone_id
        response = await _gql(client, mutation, outsider, {"i": issue_id, "l": target})
        assert response.get("errors"), response
        assert "secret" not in str(response), response
    created = await _gql(
        client, "mutation($r: ID!, $l: ID!) { createIssue(input: {repositoryId: $r, title: \"x\", labelIds: [$l]}) { issue { id } } }",
        outsider, {"r": _node("Repository", repo["id"]), "l": label_id},
    )
    assert created.get("errors"), created


async def test_head_repository_hidden_on_a_visible_pull_request(client, test_user, test_token, db_session):
    from sqlalchemy import select

    from app.models.pull_request import PullRequest

    await _fixture(client, test_token)
    headers = auth_headers(test_token)
    pull = await client.post(
        f"{API}/repos/testuser/shown/pulls", json={"title": "from a private head", "head": "main", "base": "main"}, headers=headers
    )
    hidden_id = (await client.get(f"{API}/repos/testuser/hidden", headers=headers)).json()["id"]
    row = (await db_session.execute(select(PullRequest).where(PullRequest.id == pull.json()["id"]))).scalar_one()
    row.head_repo_id = hidden_id
    await db_session.commit()
    _outsider, outsider = await _user_token(db_session, "outsider")
    query = """query($n: Int!) { repository(owner: "testuser", name: "shown") { pullRequest(number: $n) {
      headRepository { name } headRepositoryOwner { login } } } }"""

    seen = (await _gql(client, query, test_token, {"n": pull.json()["number"]}))["data"]["repository"]["pullRequest"]
    assert seen == {"headRepository": {"name": "hidden"}, "headRepositoryOwner": {"login": "testuser"}}
    hidden = (await _gql(client, query, outsider, {"n": pull.json()["number"]}))["data"]["repository"]["pullRequest"]
    assert hidden == {"headRepository": None, "headRepositoryOwner": None}


async def test_create_commit_on_branch_hidden_repository_is_not_found(client, test_user, test_token, db_session):
    await _fixture(client, test_token)
    _outsider, outsider = await _user_token(db_session, "outsider")
    mutation = """mutation($n: String!) { createCommitOnBranch(input: {
      branch: {repositoryNameWithOwner: $n, branchName: "main"}, expectedHeadOid: "0000000000000000000000000000000000000000",
      message: {headline: "probe"}, fileChanges: {additions: []}}) { commit { oid } } }"""

    hidden = await _gql(client, mutation, outsider, {"n": "testuser/hidden"})
    missing = await _gql(client, mutation, outsider, {"n": "testuser/no-such-repo"})
    assert hidden["errors"][0]["type"] == missing["errors"][0]["type"] == "NOT_FOUND"


async def test_create_repository_for_another_owner_is_refused_before_the_name_check(client, test_user, test_token, db_session):
    await _fixture(client, test_token)
    _outsider, outsider = await _user_token(db_session, "outsider")
    mutation = "mutation($o: ID!, $n: String!) { createRepository(input: {name: $n, ownerId: $o, visibility: \"private\"}) { repository { name } } }"
    owner = _node("User", test_user.id)

    taken = await _gql(client, mutation, outsider, {"o": owner, "n": "hidden"})
    free = await _gql(client, mutation, outsider, {"o": owner, "n": "brand-new"})
    assert taken["errors"][0]["type"] == free["errors"][0]["type"] == "FORBIDDEN"
    assert "already exists" not in str(taken)
    assert (await client.get(f"{API}/repos/testuser/brand-new", headers=auth_headers(test_token))).status_code == 404


async def test_search_pages_skip_hidden_results(client, test_user, test_token, db_session):
    headers = auth_headers(test_token)
    for index in range(3):
        await client.post(f"{API}/user/repos", json={"name": f"needle-private-{index}", "private": True}, headers=headers)
    await client.post(f"{API}/user/repos", json={"name": "needle-public", "private": False}, headers=headers)
    _outsider, outsider = await _user_token(db_session, "outsider")
    query = '{ search(query: "needle", type: REPOSITORY, first: 1) { nodes { ... on Repository { name } } } }'

    # The private matches come first by id; the page still holds a visible one.
    assert (await _gql(client, query, outsider))["data"]["search"]["nodes"] == [{"name": "needle-public"}]


async def test_existing_cross_repository_label_and_milestone_are_not_read(client, test_user, test_token, db_session):
    """An association saved before inputs were validated is not followed."""
    from sqlalchemy import select

    from app.models.issue import Issue, IssueLabel

    await _fixture(client, test_token)
    headers = auth_headers(test_token)
    label = (await client.post(f"{API}/repos/testuser/hidden/labels", json={"name": "secret-label"}, headers=headers)).json()
    milestone = (await client.post(f"{API}/repos/testuser/hidden/milestones", json={"title": "secret milestone"}, headers=headers)).json()
    public = (await client.post(f"{API}/repos/testuser/shown/issues", json={"title": "public issue"}, headers=headers)).json()
    row = (await db_session.execute(select(Issue).where(Issue.id == public["id"]))).scalar_one()
    row.milestone_id = milestone["id"]
    db_session.add(IssueLabel(issue_id=row.id, label_id=label["id"]))
    await db_session.commit()
    query = """query($n: Int!) { repository(owner: "testuser", name: "shown") { issue(number: $n) {
      milestone { title } labels(first: 5) { nodes { name } } } } }"""

    issue = (await _gql(client, query, None, {"n": public["number"]}))["data"]["repository"]["issue"]
    assert issue == {"milestone": None, "labels": {"nodes": []}}

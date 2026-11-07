"""Organization Actions variables (github-emulator#18).

Fullsend sets values once per organization: its CLI creates an organization
variable with visibility "all" (PATCH, then POST on 404), lists the ones a
repository inherits, and its shared workflow reads them as ``vars.*``.
"""

import time
from types import SimpleNamespace

import pytest
from jose import jwt

from app.services.workflow_service import build_run_expression_context
from tests.conftest import API, auth_headers
from tests.test_graphql_repository_visibility import _user_token

pytestmark = pytest.mark.asyncio

ORG = "vars-org"


async def _org(client, token):
    headers = auth_headers(token)
    assert (await client.post(f"{API}/orgs", json={"login": ORG}, headers=headers)).status_code == 201
    repos = {}
    for name, private in (("pub", False), ("priv", True), ("other", False)):
        created = await client.post(f"{API}/orgs/{ORG}/repos", json={"name": name, "private": private}, headers=headers)
        assert created.status_code == 201, created.text
        repos[name] = created.json()["id"]
    return repos


async def _inherited(client, token, repo):
    response = await client.get(f"{API}/repos/{ORG}/{repo}/actions/organization-variables", headers=auth_headers(token))
    assert response.status_code == 200, response.text
    return {item["name"]: item["value"] for item in response.json()["variables"]}


async def test_create_update_get_list_and_delete(client, test_user, test_token):
    await _org(client, test_token)
    headers = auth_headers(test_token)
    base = f"{API}/orgs/{ORG}/actions/variables"

    # Fullsend's create-or-update: PATCH answers 404 for a new variable.
    assert (await client.patch(f"{base}/APP_SET", json={"value": "x", "visibility": "all"}, headers=headers)).status_code == 404
    created = await client.post(base, json={"name": "APP_SET", "value": "one", "visibility": "all"}, headers=headers)
    assert created.status_code == 201, created.text
    duplicate = await client.post(base, json={"name": "APP_SET", "value": "two", "visibility": "all"}, headers=headers)
    assert duplicate.status_code == 409

    updated = await client.patch(f"{base}/APP_SET", json={"value": "two", "visibility": "all"}, headers=headers)
    assert updated.status_code == 204
    got = (await client.get(f"{base}/APP_SET", headers=headers)).json()
    assert got["name"] == "APP_SET" and got["value"] == "two" and got["visibility"] == "all"
    assert "selected_repositories_url" not in got and got["created_at"] and got["updated_at"]

    renamed = await client.patch(f"{base}/APP_SET", json={"name": "RENAMED"}, headers=headers)
    assert renamed.status_code == 204
    assert (await client.get(f"{base}/APP_SET", headers=headers)).status_code == 404

    assert (await client.delete(f"{base}/RENAMED", headers=headers)).status_code == 204
    assert (await client.delete(f"{base}/RENAMED", headers=headers)).status_code == 404


async def test_list_pages_at_thirty(client, test_user, test_token):
    await _org(client, test_token)
    headers = auth_headers(test_token)
    for index in range(32):
        response = await client.post(
            f"{API}/orgs/{ORG}/actions/variables", headers=headers,
            json={"name": f"V{index:02d}", "value": str(index), "visibility": "all"},
        )
        assert response.status_code == 201
    first = await client.get(f"{API}/orgs/{ORG}/actions/variables?per_page=100", headers=headers)
    body = first.json()
    assert body["total_count"] == 32 and len(body["variables"]) == 30
    assert 'rel="next"' in first.headers["link"]
    second = (await client.get(f"{API}/orgs/{ORG}/actions/variables?per_page=100&page=2", headers=headers)).json()
    assert [item["name"] for item in second["variables"]] == ["V30", "V31"]


async def test_visibility_decides_which_repositories_inherit(client, test_user, test_token):
    repos = await _org(client, test_token)
    headers = auth_headers(test_token)
    base = f"{API}/orgs/{ORG}/actions/variables"
    for name, visibility, extra in (
        ("EVERYONE", "all", {}),
        ("PRIVATE_ONLY", "private", {}),
        ("PICKED", "selected", {"selected_repository_ids": [repos["pub"]]}),
    ):
        response = await client.post(base, json={"name": name, "value": name.lower(), "visibility": visibility, **extra}, headers=headers)
        assert response.status_code == 201, response.text

    assert await _inherited(client, test_token, "pub") == {"EVERYONE": "everyone", "PICKED": "picked"}
    assert await _inherited(client, test_token, "priv") == {"EVERYONE": "everyone", "PRIVATE_ONLY": "private_only"}
    assert await _inherited(client, test_token, "other") == {"EVERYONE": "everyone"}
    picked = (await client.get(f"{base}/PICKED", headers=headers)).json()
    assert picked["selected_repositories_url"].endswith(f"/orgs/{ORG}/actions/variables/PICKED/repositories")


async def test_selected_repositories(client, test_user, test_token):
    repos = await _org(client, test_token)
    headers = auth_headers(test_token)
    base = f"{API}/orgs/{ORG}/actions/variables"
    await client.post(base, json={"name": "PICKED", "value": "p", "visibility": "selected", "selected_repository_ids": []}, headers=headers)
    await client.post(base, json={"name": "EVERYONE", "value": "e", "visibility": "all"}, headers=headers)

    def ids(response):
        return {item["id"] for item in response.json()["repositories"]}

    assert (await client.put(f"{base}/PICKED/repositories", json={"selected_repository_ids": [repos["pub"], repos["other"]]}, headers=headers)).status_code == 204
    assert ids(await client.get(f"{base}/PICKED/repositories", headers=headers)) == {repos["pub"], repos["other"]}
    assert (await client.delete(f"{base}/PICKED/repositories/{repos['other']}", headers=headers)).status_code == 204
    assert (await client.put(f"{base}/PICKED/repositories/{repos['priv']}", headers=headers)).status_code == 204
    assert ids(await client.get(f"{base}/PICKED/repositories", headers=headers)) == {repos["pub"], repos["priv"]}

    # A repository outside the organization cannot be selected.
    outside = await client.post(f"{API}/user/repos", json={"name": "mine"}, headers=headers)
    refused = await client.put(f"{base}/PICKED/repositories/{outside.json()['id']}", headers=headers)
    assert refused.status_code == 422

    # Selection endpoints answer 409 unless the visibility is "selected".
    for method, path in (("get", "/repositories"), ("put", f"/repositories/{repos['pub']}"),
                         ("delete", f"/repositories/{repos['pub']}")):
        response = await getattr(client, method)(f"{base}/EVERYONE{path}", headers=headers)
        assert response.status_code == 409, (method, path)

    # Leaving "selected" clears the selection.
    await client.patch(f"{base}/PICKED", json={"visibility": "all"}, headers=headers)
    await client.patch(f"{base}/PICKED", json={"visibility": "selected"}, headers=headers)
    assert ids(await client.get(f"{base}/PICKED/repositories", headers=headers)) == set()


async def test_rejects_bad_input(client, test_user, test_token):
    await _org(client, test_token)
    headers = auth_headers(test_token)
    base = f"{API}/orgs/{ORG}/actions/variables"
    assert (await client.post(base, json={"value": "v", "visibility": "all"}, headers=headers)).status_code == 422
    assert (await client.post(base, json={"name": "N", "visibility": "all"}, headers=headers)).status_code == 422
    assert (await client.post(base, json={"name": "N", "value": "v", "visibility": "everyone"}, headers=headers)).status_code == 422
    assert (await client.post(base, json={"name": "N", "value": "v", "visibility": "selected",
                                          "selected_repository_ids": "1"}, headers=headers)).status_code == 422
    assert (await client.get(f"{API}/orgs/nope/actions/variables", headers=headers)).status_code == 404


async def test_who_may_manage(client, test_user, test_token, admin_token, db_session):
    await _org(client, test_token)
    base = f"{API}/orgs/{ORG}/actions/variables"
    await client.post(base, json={"name": "SET", "value": "v", "visibility": "all"}, headers=auth_headers(test_token))

    _outsider, outsider = await _user_token(db_session, "outsider")
    assert (await client.get(base, headers=auth_headers(outsider))).status_code == 403
    assert (await client.get(base)).status_code == 401
    # A site admin may.
    assert (await client.get(base, headers=auth_headers(admin_token))).status_code == 200

    tokens = {}
    for app_id, account, grant in (("7001", ORG, "read"), ("7002", ORG, "write"), ("7003", "testuser", "write")):
        created = await client.post(
            f"{API}/admin/apps", headers=auth_headers(admin_token),
            json={"app_id": app_id, "name": f"v-{app_id}", "slug": f"v-{app_id}",
                  "permissions": {"organization_actions_variables": grant}},
        )
        installation = await client.post(
            f"{API}/admin/apps/{app_id}/installations", headers=auth_headers(admin_token),
            json={"account_login": account, "account_type": "Organization" if account == ORG else "User",
                  "repositories": [], "permissions": {"organization_actions_variables": grant}},
        )
        assert installation.status_code == 201, installation.text
        now = int(time.time())
        app_jwt = jwt.encode({"iss": app_id, "iat": now - 10, "exp": now + 60}, created.json()["private_key"], algorithm="RS256")
        minted = await client.post(
            f"{API}/app/installations/{installation.json()['id']}/access_tokens",
            headers={"Authorization": f"Bearer {app_jwt}"}, json={},
        )
        assert minted.status_code == 201, minted.text
        tokens[app_id] = auth_headers(minted.json()["token"])

    assert (await client.get(f"{base}/SET", headers=tokens["7001"])).status_code == 200
    assert (await client.patch(f"{base}/SET", json={"value": "w"}, headers=tokens["7001"])).status_code == 403
    assert (await client.patch(f"{base}/SET", json={"value": "w"}, headers=tokens["7002"])).status_code == 204
    # Installed on another account.
    assert (await client.get(f"{base}/SET", headers=tokens["7003"])).status_code == 403


async def test_workflow_vars_include_organization_variables(client, test_user, test_token, db_session):
    repos = await _org(client, test_token)
    headers = auth_headers(test_token)
    await client.post(f"{API}/orgs/{ORG}/actions/variables", headers=headers,
                      json={"name": "FULLSEND_APP_SET", "value": "org", "visibility": "all"})
    await client.post(f"{API}/orgs/{ORG}/actions/variables", headers=headers,
                      json={"name": "SHARED", "value": "org", "visibility": "all"})
    await client.post(f"{API}/orgs/{ORG}/actions/variables", headers=headers,
                      json={"name": "PRIVATE_ONLY", "value": "org", "visibility": "private"})
    repo_var = await client.post(f"{API}/repos/{ORG}/pub/actions/variables", headers=headers,
                                 json={"name": "SHARED", "value": "repo"})
    assert repo_var.status_code == 201

    run = SimpleNamespace(
        repo_id=repos["pub"], trigger_payload={}, event="push", head_branch="main",
        id=1, run_number=1, head_sha="0" * 40,
    )
    context = await build_run_expression_context(db_session, run)
    # The repository's own variable wins over the organization's.
    assert context["vars"] == {"FULLSEND_APP_SET": "org", "SHARED": "repo"}


async def test_deleting_a_repository_drops_its_selections(client, test_user, test_token):
    repos = await _org(client, test_token)
    headers = auth_headers(test_token)
    base = f"{API}/orgs/{ORG}/actions/variables"
    await client.post(base, headers=headers, json={"name": "PICKED", "value": "p", "visibility": "selected",
                                                    "selected_repository_ids": [repos["pub"], repos["other"]]})
    assert (await client.delete(f"{API}/repos/{ORG}/other", headers=headers)).status_code == 204
    listed = (await client.get(f"{base}/PICKED/repositories", headers=headers)).json()
    assert [item["id"] for item in listed["repositories"]] == [repos["pub"]]


async def test_inherited_variables_need_repository_access(client, test_user, test_token, db_session):
    await _org(client, test_token)
    headers = auth_headers(test_token)
    await client.post(f"{API}/orgs/{ORG}/actions/variables", headers=headers,
                      json={"name": "EVERYONE", "value": "e", "visibility": "all"})
    _outsider, outsider = await _user_token(db_session, "outsider")
    path = f"{API}/repos/{ORG}/pub/actions/organization-variables"
    assert (await client.get(path, headers=auth_headers(outsider))).status_code == 403

    _reader, reader = await _user_token(db_session, "reader")
    added = await client.put(f"{API}/repos/{ORG}/pub/collaborators/reader", json={"permission": "pull"}, headers=headers)
    assert added.status_code in (201, 204), added.text
    assert await _inherited(client, reader, "pub") == {"EVERYONE": "e"}


async def test_deleting_an_organization_deletes_its_variables(client, test_user, test_token, admin_token, db_session):
    from sqlalchemy import func, select

    from app.models.actions import OrgVariable, OrgVariableRepository

    headers = auth_headers(test_token)
    assert (await client.post(f"{API}/orgs", json={"login": ORG}, headers=headers)).status_code == 201
    await client.post(f"{API}/orgs/{ORG}/actions/variables", headers=headers,
                      json={"name": "PICKED", "value": "p", "visibility": "selected", "selected_repository_ids": []})
    org_id = (await client.get(f"{API}/orgs/{ORG}", headers=headers)).json()["id"]
    deleted = await client.delete(f"/admin/api/organizations/{org_id}", headers=auth_headers(admin_token))
    assert deleted.status_code == 204, deleted.text
    assert (await db_session.execute(select(func.count()).select_from(OrgVariable))).scalar_one() == 0
    assert (await db_session.execute(select(func.count()).select_from(OrgVariableRepository))).scalar_one() == 0

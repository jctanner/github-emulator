"""Four places where the emulator answered differently from GitHub.

Each was found by a real client behaving correctly against it, and in each
case the emulator's answer sent the caller somewhere unhelpful: a 500 that
means "already exists", a 405 for a route GitHub serves, a 404 for the only
endpoint that enumerates organizations, and a URL naming the wrong owner.
"""

import pytest

from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_creating_a_variable_that_exists_is_a_conflict(client, test_token, test_repo_with_init):
    """409, not 500.

    Clients branch on 409 to decide between POST and PATCH. A 500 says the
    server is broken, so a client that would have updated gives up instead.
    """
    url = f"{API}/repos/testuser/init-repo/actions/variables"
    first = await client.post(url, headers=auth_headers(test_token), json={"name": "DUPE", "value": "a"})
    assert first.status_code == 201

    second = await client.post(url, headers=auth_headers(test_token), json={"name": "DUPE", "value": "b"})
    assert second.status_code == 409
    assert "already exists" in second.json()["message"]

    # The original value must survive a refused create.
    listed = await client.get(url, headers=auth_headers(test_token))
    values = {v["name"]: v["value"] for v in listed.json()["variables"]}
    assert values["DUPE"] == "a"


@pytest.mark.asyncio
async def test_a_single_variable_can_be_fetched(client, test_token, test_repo_with_init):
    """GET on the variable path, which previously answered 405."""
    url = f"{API}/repos/testuser/init-repo/actions/variables"
    await client.post(url, headers=auth_headers(test_token), json={"name": "ONE", "value": "1"})

    got = await client.get(f"{url}/ONE", headers=auth_headers(test_token))
    assert got.status_code == 200
    body = got.json()
    assert body["name"] == "ONE"
    assert body["value"] == "1"
    assert "created_at" in body


@pytest.mark.asyncio
async def test_a_missing_variable_is_not_found_rather_than_method_not_allowed(
    client, test_token, test_repo_with_init
):
    got = await client.get(
        f"{API}/repos/testuser/init-repo/actions/variables/NOPE", headers=auth_headers(test_token)
    )
    assert got.status_code == 404


@pytest.mark.asyncio
async def test_organizations_can_be_enumerated(client, test_token):
    """/organizations is how a client lists orgs without knowing their names."""
    created = await client.post(
        f"{API}/orgs", headers=auth_headers(test_token), json={"login": "fidelity-org"}
    )
    assert created.status_code in (201, 422)  # 422 when a previous test made it

    listed = await client.get(f"{API}/organizations", headers=auth_headers(test_token))
    assert listed.status_code == 200
    logins = {o["login"] for o in listed.json()}
    assert "fidelity-org" in logins


@pytest.mark.asyncio
async def test_organizations_pagination_uses_since(client, test_token):
    """GitHub pages this endpoint by last id seen, not by page number."""
    for login in ("page-org-a", "page-org-b"):
        await client.post(f"{API}/orgs", headers=auth_headers(test_token), json={"login": login})

    first = await client.get(f"{API}/organizations?per_page=1", headers=auth_headers(test_token))
    assert first.status_code == 200
    assert len(first.json()) == 1
    cursor = first.json()[0]["id"]

    second = await client.get(
        f"{API}/organizations?per_page=1&since={cursor}", headers=auth_headers(test_token)
    )
    assert second.status_code == 200
    assert all(o["id"] > cursor for o in second.json())


@pytest.mark.asyncio
async def test_an_org_repos_issue_url_names_the_org(client, db_session, test_token, test_user):
    """The issue URL must name the repository's owner, not its creator.

    An organisation-owned repository has owner_id pointing at the user who
    created it and organization_id set separately, so rebuilding the path from
    owner.login produced .../admin/triage-target/issues/N — a link that 404s.
    """
    from app.models.organization import Organization
    from app.models.repository import Repository

    org = Organization(login="url-org")
    db_session.add(org)
    await db_session.flush()
    repo = Repository(
        owner_id=test_user.id, organization_id=org.id, owner_type="Organization",
        name="widget", full_name="url-org/widget",
    )
    db_session.add(repo)
    await db_session.commit()

    created = await client.post(
        f"{API}/repos/url-org/widget/issues",
        headers=auth_headers(test_token),
        json={"title": "owner in the url"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert "/url-org/widget/issues/" in body["html_url"], body["html_url"]
    assert "/testuser/" not in body["html_url"]
    assert body["repository_url"].endswith("/repos/url-org/widget")

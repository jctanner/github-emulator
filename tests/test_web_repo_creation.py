"""Tests for owner selection on the public repository-creation page."""

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.models.repository import Repository
from app.web.routes import _sign_session
from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_new_repository_page_lists_personal_and_organization_owners(
    client, test_user, test_token
):
    response = await client.post(
        f"{API}/orgs",
        json={"login": "member-org", "name": "Member Organization"},
        headers=auth_headers(test_token),
    )
    assert response.status_code == 201
    client.cookies.set("ui_session", _sign_session(test_user.login))

    page = await client.get("/ui-legacy/new")

    assert page.status_code == 200
    assert 'name="owner"' in page.text
    assert '<option value="testuser" selected>' in page.text
    assert '<option value="member-org">' in page.text
    assert "member-org (organization)" in page.text


@pytest.mark.asyncio
async def test_new_repository_page_creates_under_selected_organization(
    client, db_session, test_user, test_token
):
    await client.post(
        f"{API}/orgs",
        json={"login": "create-org"},
        headers=auth_headers(test_token),
    )
    client.cookies.set("ui_session", _sign_session(test_user.login))

    response = await client.post(
        "/ui-legacy/new",
        data={
            "owner": "create-org",
            "name": "web-created",
            "description": "Created from the owner selector",
            "auto_init": "true",
        },
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/ui-legacy/create-org/web-created"
    repository = (
        await db_session.execute(
            select(Repository).where(
                Repository.full_name == "create-org/web-created"
            )
        )
    ).scalar_one()
    assert repository.owner_type == "Organization"
    assert repository.description == "Created from the owner selector"


@pytest.mark.asyncio
async def test_repository_creation_rejects_unavailable_organization(
    client, db_session, test_user, test_token
):
    db_session.add(Organization(login="unavailable-org"))
    await db_session.commit()
    client.cookies.set("ui_session", _sign_session(test_user.login))

    web_response = await client.post(
        "/ui-legacy/new",
        data={"owner": "unavailable-org", "name": "not-allowed"},
    )
    api_response = await client.post(
        f"{API}/orgs/unavailable-org/repos",
        json={"name": "also-not-allowed"},
        headers=auth_headers(test_token),
    )

    assert web_response.status_code == 200
    assert "You cannot create repositories under" in web_response.text
    assert api_response.status_code == 403
    repositories = (
        await db_session.execute(
            select(Repository).where(
                Repository.full_name.in_(
                    [
                        "unavailable-org/not-allowed",
                        "unavailable-org/also-not-allowed",
                    ]
                )
            )
        )
    ).scalars().all()
    assert repositories == []

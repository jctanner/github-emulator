"""Tests for issue-page label selection in the web frontend."""

import re

import pytest

from app.web.routes import _sign_session
from tests.conftest import auth_headers

API = "/api/v3"


@pytest.mark.asyncio
async def test_issue_page_renders_and_updates_label_selector(
    client, test_user, test_token, test_repo_with_init
):
    """The issue sidebar lists repository labels and persists its selection."""
    owner, repo_name, _ = test_repo_with_init
    issue_response = await client.post(
        f"{API}/repos/{owner}/{repo_name}/issues",
        json={"title": "Issue with labels"},
        headers=auth_headers(test_token),
    )
    assert issue_response.status_code == 201

    for name, color in (("bug", "d73a4a"), ("enhancement", "a2eeef")):
        label_response = await client.post(
            f"{API}/repos/{owner}/{repo_name}/labels",
            json={"name": name, "color": color},
            headers=auth_headers(test_token),
        )
        assert label_response.status_code == 201

    page = await client.get(f"/ui/{owner}/{repo_name}/issues/1")
    assert page.status_code == 200
    assert 'class="issue-detail-sidebar"' in page.text
    assert 'name="labels" value="bug"' in page.text
    assert 'name="labels" value="enhancement"' in page.text
    assert "to edit labels." in page.text

    client.cookies.set("ui_session", _sign_session(owner))
    page = await client.get(f"/ui/{owner}/{repo_name}/issues/1")
    assert page.status_code == 200
    assert "Apply labels" in page.text

    update_response = await client.post(
        f"/ui/{owner}/{repo_name}/issues/1",
        data={"labels": ["bug"]},
        follow_redirects=False,
    )
    assert update_response.status_code == 302
    assert update_response.headers["location"] == f"/ui/{owner}/{repo_name}/issues/1"

    page = await client.get(f"/ui/{owner}/{repo_name}/issues/1")
    assert re.search(r'name="labels" value="bug"\s+checked', page.text)
    assert not re.search(r'name="labels" value="enhancement"\s+checked', page.text)

    labels_response = await client.get(
        f"{API}/repos/{owner}/{repo_name}/issues/1/labels",
        headers=auth_headers(test_token),
    )
    assert labels_response.status_code == 200
    assert [label["name"] for label in labels_response.json()] == ["bug"]


@pytest.mark.asyncio
async def test_issue_label_update_requires_ui_login(
    client, test_user, test_token, test_repo_with_init
):
    """Anonymous users cannot submit label changes from the issue page."""
    owner, repo_name, _ = test_repo_with_init
    issue_response = await client.post(
        f"{API}/repos/{owner}/{repo_name}/issues",
        json={"title": "Protected labels"},
        headers=auth_headers(test_token),
    )
    assert issue_response.status_code == 201

    response = await client.post(
        f"/ui/{owner}/{repo_name}/issues/1",
        data={"labels": ["bug"]},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/ui/login"


@pytest.mark.asyncio
async def test_issue_page_can_create_and_delete_repository_labels(
    client, test_user, test_token, test_repo_with_init
):
    """Authenticated users can manage repository labels from an issue page."""
    owner, repo_name, _ = test_repo_with_init
    issue_response = await client.post(
        f"{API}/repos/{owner}/{repo_name}/issues",
        json={"title": "Manage labels"},
        headers=auth_headers(test_token),
    )
    assert issue_response.status_code == 201

    client.cookies.set("ui_session", _sign_session(owner))
    create_response = await client.post(
        f"/ui/{owner}/{repo_name}/issues/1/labels/create",
        data={
            "name": "needs-triage",
            "color": "#fbca04",
            "description": "Needs review",
        },
        follow_redirects=False,
    )
    assert create_response.status_code == 302

    labels_response = await client.get(
        f"{API}/repos/{owner}/{repo_name}/labels",
        headers=auth_headers(test_token),
    )
    assert [label["name"] for label in labels_response.json()] == ["needs-triage"]
    label_id = labels_response.json()[0]["id"]

    page = await client.get(f"/ui/{owner}/{repo_name}/issues/1")
    assert "needs-triage" in page.text
    assert "Add new label" in page.text

    delete_response = await client.post(
        f"/ui/{owner}/{repo_name}/issues/1/labels/delete",
        data={"label_id": str(label_id)},
        follow_redirects=False,
    )
    assert delete_response.status_code == 302

    labels_response = await client.get(
        f"{API}/repos/{owner}/{repo_name}/labels",
        headers=auth_headers(test_token),
    )
    assert labels_response.json() == []

"""Tests for issue-page label selection in the web frontend."""

import re

import pytest
from sqlalchemy import select

from app.models.actions import WorkflowRun
from app.services import workflow_service
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
    assert "to edit labels." in page.text

    client.cookies.set("ui_session", _sign_session(owner))
    page = await client.get(f"/ui/{owner}/{repo_name}/issues/1")
    assert page.status_code == 200
    assert 'name="labels" value="bug"' in page.text
    assert 'name="labels" value="enhancement"' in page.text
    assert "Apply labels" in page.text
    assert 'aria-label="Manage labels"' in page.text
    assert 'placeholder="Filter labels"' in page.text
    assert "Apply labels to this issue" in page.text
    assert "Selected" in page.text
    assert "Edit labels" in page.text

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
    labels_page = await client.get(f"/ui/{owner}/{repo_name}/labels")
    assert labels_page.status_code == 200
    assert "Labels" in labels_page.text
    assert "New label" in labels_page.text
    assert "needs-triage" in labels_page.text

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


@pytest.mark.asyncio
async def test_issue_web_flow_creates_closes_and_reopens_issue(
    client, db_session, test_user, test_token, test_repo_with_init, monkeypatch
):
    """The web UI exposes issue creation and close/reopen actions."""
    owner, repo_name, _ = test_repo_with_init
    client.cookies.set("ui_session", _sign_session(owner))

    async def fake_detect(_path, _ref="HEAD"):
        return [{
            "_path": ".github/workflows/activity.yml",
            "name": "Activity",
            "on": {
                "issues": {"types": ["opened"]},
                "issue_comment": {"types": ["created"]},
            },
            "jobs": {
                "record": {
                    "runs-on": ["self-hosted"],
                    "steps": [{"run": "echo activity"}],
                },
            },
        }]

    async def fake_ref_sha(_path, _ref):
        return "a" * 40

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    monkeypatch.setattr(workflow_service, "get_ref_sha", fake_ref_sha)

    new_page = await client.get(f"/ui/{owner}/{repo_name}/issues/new")
    assert new_page.status_code == 200
    assert "Submit new issue" in new_page.text

    create_response = await client.post(
        f"/ui/{owner}/{repo_name}/issues/new",
        data={"title": "Closeable issue", "body": "Issue body"},
        follow_redirects=False,
    )
    assert create_response.status_code == 302
    assert create_response.headers["location"] == f"/ui/{owner}/{repo_name}/issues/1"

    runs = (await db_session.execute(
        select(WorkflowRun).order_by(WorkflowRun.id)
    )).scalars().all()
    assert len(runs) == 1
    assert runs[0].event == "issues"
    assert runs[0].trigger_payload["action"] == "opened"
    assert runs[0].trigger_payload["issue"]["number"] == 1

    page = await client.get(f"/ui/{owner}/{repo_name}/issues/1")
    assert page.status_code == 200
    assert "New issue" in page.text
    assert "opened this issue on" in page.text
    assert "IssueDescription" in page.text
    assert "Add a comment" in page.text
    assert "Close issue" in page.text
    assert page.text.index("New issue") < page.text.index("Add a comment")
    assert page.text.index("Add a comment") < page.text.index("Close issue")

    comment_response = await client.post(
        f"/ui/{owner}/{repo_name}/issues/1/comments",
        data={"body": "A web comment"},
        follow_redirects=False,
    )
    assert comment_response.status_code == 302
    page = await client.get(f"/ui/{owner}/{repo_name}/issues/1")
    assert "A web comment" in page.text
    assert "IssueComment" in page.text
    assert 'class="TimelineItem-avatar"' not in page.text

    runs = (await db_session.execute(
        select(WorkflowRun).order_by(WorkflowRun.id)
    )).scalars().all()
    assert len(runs) == 2
    assert runs[1].event == "issue_comment"
    assert runs[1].trigger_payload["action"] == "created"
    assert runs[1].trigger_payload["comment"]["body"] == "A web comment"

    close_response = await client.post(
        f"/ui/{owner}/{repo_name}/issues/1/state",
        data={"state": "closed"},
        follow_redirects=False,
    )
    assert close_response.status_code == 302

    issue_response = await client.get(
        f"{API}/repos/{owner}/{repo_name}/issues/1",
        headers=auth_headers(test_token),
    )
    issue = issue_response.json()
    assert issue["state"] == "closed"
    assert issue["closed_by"]["login"] == owner

    page = await client.get(f"/ui/{owner}/{repo_name}/issues/1")
    assert "Reopen issue" in page.text
    assert "Close issue" not in page.text

    reopen_response = await client.post(
        f"/ui/{owner}/{repo_name}/issues/1/state",
        data={"state": "open"},
        follow_redirects=False,
    )
    assert reopen_response.status_code == 302

    issue_response = await client.get(
        f"{API}/repos/{owner}/{repo_name}/issues/1",
        headers=auth_headers(test_token),
    )
    issue = issue_response.json()
    assert issue["state"] == "open"
    assert issue["closed_by"] is None


@pytest.mark.asyncio
async def test_repository_label_editor_can_create_and_delete_labels(
    client, test_user, test_token, test_repo_with_init
):
    """The repository labels page supports label creation and deletion."""
    owner, repo_name, _ = test_repo_with_init
    client.cookies.set("ui_session", _sign_session(owner))

    page = await client.get(f"/ui/{owner}/{repo_name}/labels")
    assert page.status_code == 200
    assert "No labels found" in page.text

    create_response = await client.post(
        f"/ui/{owner}/{repo_name}/labels/create",
        data={"name": "documentation", "color": "#0075ca", "description": "Docs"},
        follow_redirects=False,
    )
    assert create_response.status_code == 302

    page = await client.get(f"/ui/{owner}/{repo_name}/labels?q=doc")
    assert page.status_code == 200
    assert "documentation" in page.text
    assert "1 label" in page.text

    labels_response = await client.get(
        f"{API}/repos/{owner}/{repo_name}/labels",
        headers=auth_headers(test_token),
    )
    label_id = labels_response.json()[0]["id"]
    delete_response = await client.post(
        f"/ui/{owner}/{repo_name}/labels/delete",
        data={"label_id": str(label_id)},
        follow_redirects=False,
    )
    assert delete_response.status_code == 302

    page = await client.get(f"/ui/{owner}/{repo_name}/labels")
    assert "No labels found" in page.text

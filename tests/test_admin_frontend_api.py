"""Contracts for the API-client site-administration surface."""

import pytest
from sqlalchemy import event

from app.models.issue import Issue
from app.models.pull_request import PullRequest

from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_admin_summary_and_inventory(client, admin_token):
    headers = auth_headers(admin_token)
    summary = await client.get("/admin/api/summary", headers=headers)
    users = await client.get("/admin/api/users", headers=headers)
    repositories = await client.get("/admin/api/repositories", headers=headers)

    assert summary.status_code == 200
    assert summary.json()["users"] >= 1
    assert users.status_code == 200
    assert repositories.status_code == 200


@pytest.mark.asyncio
async def test_admin_user_and_organization_lifecycle(client, admin_token):
    headers = auth_headers(admin_token)
    user = await client.post(
        "/admin/api/users",
        json={"login": "frontend-admin-test", "password": "secret"},
        headers=headers,
    )
    organization = await client.post(
        "/admin/api/organizations",
        json={"login": "frontend-admin-org"},
        headers=headers,
    )

    assert user.status_code == 201
    assert organization.status_code == 201
    assert (
        await client.delete(
            f"/admin/api/organizations/{organization.json()['id']}", headers=headers
        )
    ).status_code == 204
    assert (
        await client.delete(
            f"/admin/api/users/{user.json()['id']}", headers=headers
        )
    ).status_code == 204


@pytest.mark.asyncio
async def test_repository_installations_list_is_typed(
    client, test_token, test_repo_with_init
):
    owner, repo, _ = test_repo_with_init
    response = await client.get(
        f"/api/v3/repos/{owner}/{repo}/installations",
        headers=auth_headers(test_token),
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_admin_issues_uses_one_projection_query(
    client,
    admin_token,
    db_session,
    db_engine,
    test_user,
    test_repo_with_init,
):
    """Issue inventory does not load ORM graphs or query repositories per row."""
    _, _, repository = test_repo_with_init
    issue = Issue(
        repo_id=repository["id"],
        number=2,
        user_id=test_user.id,
        title="Admin inventory pull request",
    )
    db_session.add(issue)
    await db_session.flush()
    db_session.add(
        PullRequest(
            issue_id=issue.id,
            repo_id=repository["id"],
            head_ref="feature",
            head_sha="1" * 40,
            base_ref="main",
            base_sha="0" * 40,
        )
    )
    await db_session.commit()

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.split()).lower())

    event.listen(db_engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        response = await client.get(
            "/admin/api/issues", headers=auth_headers(admin_token)
        )
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", record_statement)

    assert response.status_code == 200
    row = next(item for item in response.json() if item["id"] == issue.id)
    assert row["repository"] == "testuser/init-repo"
    assert row["is_pull_request"] is True
    inventory_queries = [
        statement
        for statement in statements
        if " from issues " in f" {statement} "
    ]
    assert len(inventory_queries) == 1, statements
    assert "join repositories" in inventory_queries[0]
    assert "join pull_requests" in inventory_queries[0]

"""Tests for the Branches REST API endpoints."""

import pytest
import pytest_asyncio

from tests.conftest import auth_headers

API = "/api/v3"


@pytest_asyncio.fixture
async def branch_repo(client, test_user, test_token, db_session):
    """Create a repo and populate it with branch records in the database.

    The branches API reads from the ``branches`` table, so we insert
    Branch rows directly rather than relying on a git push.
    """
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "branch-repo"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    repo_data = resp.json()
    repo_id = repo_data["id"]

    from app.models.branch import Branch

    branches = [
        Branch(repo_id=repo_id, name="main", sha="a" * 40, protected=False),
        Branch(repo_id=repo_id, name="develop", sha="b" * 40, protected=False),
        Branch(repo_id=repo_id, name="feature/login", sha="c" * 40, protected=False),
    ]
    for branch in branches:
        db_session.add(branch)
    await db_session.commit()

    return repo_data


# ---------------------------------------------------------------------------
# Branch listing and retrieval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_branches(client, test_user, test_token, branch_repo):
    """GET /repos/{owner}/{repo}/branches lists all branches."""
    resp = await client.get(
        f"{API}/repos/testuser/branch-repo/branches",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 3
    names = [b["name"] for b in data]
    assert "main" in names
    assert "develop" in names
    assert "feature/login" in names


@pytest.mark.asyncio
async def test_list_branches_response_shape(client, test_user, test_token, branch_repo):
    """Verify each branch in the list has the expected fields."""
    resp = await client.get(
        f"{API}/repos/testuser/branch-repo/branches",
        headers=auth_headers(test_token),
    )
    data = resp.json()
    branch = data[0]
    assert "name" in branch
    assert "commit" in branch
    assert "sha" in branch["commit"]
    assert "url" in branch["commit"]
    assert "protected" in branch
    assert "protection_url" in branch


@pytest.mark.asyncio
async def test_get_branch_by_name(client, test_user, test_token, branch_repo):
    """GET /repos/{owner}/{repo}/branches/{branch} returns a single branch."""
    resp = await client.get(
        f"{API}/repos/testuser/branch-repo/branches/main",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "main"
    assert data["commit"]["sha"] == "a" * 40
    assert data["protected"] is False


@pytest.mark.asyncio
async def test_get_branch_develop(client, test_user, test_token, branch_repo):
    """Retrieve the develop branch specifically."""
    resp = await client.get(
        f"{API}/repos/testuser/branch-repo/branches/develop",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "develop"
    assert data["commit"]["sha"] == "b" * 40


@pytest.mark.asyncio
async def test_get_nonexistent_branch(client, test_user, test_token, branch_repo):
    """GET for a nonexistent branch returns 404."""
    resp = await client.get(
        f"{API}/repos/testuser/branch-repo/branches/nonexistent",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_branches_on_nonexistent_repo(client, test_user, test_token):
    """Listing branches on a nonexistent repo returns 404."""
    resp = await client.get(
        f"{API}/repos/nobody/nothing/branches",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_branches_empty_repo(client, test_user, test_token):
    """Listing branches on a repo with no branch records returns an empty list."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "empty-branch-repo"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(
        f"{API}/repos/testuser/empty-branch-repo/branches",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_branch_protection_info(client, test_user, test_token, branch_repo):
    """Branch response includes protection sub-object."""
    resp = await client.get(
        f"{API}/repos/testuser/branch-repo/branches/main",
        headers=auth_headers(test_token),
    )
    data = resp.json()
    assert "protection" in data
    protection = data["protection"]
    assert "enabled" in protection
    assert "required_status_checks" in protection

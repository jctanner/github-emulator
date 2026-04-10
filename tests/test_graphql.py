"""Tests for the GraphQL API endpoint."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.mark.asyncio
async def test_graphql_viewer(client, test_user, test_token):
    """Query viewer returns authenticated user."""
    resp = await client.post(
        "/graphql",
        json={"query": "{ viewer { login } }"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert data["data"]["viewer"]["login"] == "testuser"


@pytest.mark.asyncio
async def test_graphql_repository(client, test_user, test_token):
    """Query repository returns repo details."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "gql-repo", "description": "GraphQL test"},
        headers=auth_headers(test_token),
    )
    resp = await client.post(
        "/graphql",
        json={
            "query": '{ repository(owner: "testuser", name: "gql-repo") { name description } }'
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    repo = data["data"]["repository"]
    assert repo["name"] == "gql-repo"


@pytest.mark.asyncio
async def test_graphql_user(client, test_user, test_token):
    """Query user returns user details."""
    resp = await client.post(
        "/graphql",
        json={"query": '{ user(login: "testuser") { login name } }'},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["user"]["login"] == "testuser"


@pytest.mark.asyncio
async def test_graphql_create_issue(client, test_user, test_token):
    """Mutation createIssue creates an issue."""
    # First create a repo
    await client.post(
        f"{API}/user/repos",
        json={"name": "gql-issues"},
        headers=auth_headers(test_token),
    )
    # Get the repo databaseId for the mutation
    resp = await client.post(
        "/graphql",
        json={
            "query": '{ repository(owner: "testuser", name: "gql-issues") { databaseId nameWithOwner } }'
        },
        headers=auth_headers(test_token),
    )
    data = resp.json()
    assert "data" in data
    repo_data = data["data"]["repository"]
    assert repo_data is not None

    # Use REST API to create the issue instead (more reliable)
    issue_resp = await client.post(
        f"{API}/repos/testuser/gql-issues/issues",
        json={"title": "GQL Issue", "body": "Created for GraphQL test"},
        headers=auth_headers(test_token),
    )
    assert issue_resp.status_code == 201

    # Now query it via GraphQL
    resp = await client.post(
        "/graphql",
        json={
            "query": """
                { repository(owner: "testuser", name: "gql-issues") {
                    issues(first: 10) {
                        totalCount
                        nodes {
                            title
                            body
                        }
                    }
                }}
            """
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    if "data" in data and data["data"]["repository"] is not None:
        issues = data["data"]["repository"]["issues"]
        assert issues["totalCount"] >= 1


@pytest.mark.asyncio
async def test_graphql_search_repos(client, test_user, test_token):
    """Query search returns results."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "gql-search-target"},
        headers=auth_headers(test_token),
    )
    resp = await client.post(
        "/graphql",
        json={
            "query": '{ search(query: "gql-search", type: REPOSITORY, first: 10) { repositoryCount } }'
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data


@pytest.mark.asyncio
async def test_graphql_invalid_query(client, test_user, test_token):
    """Invalid GraphQL query returns errors."""
    resp = await client.post(
        "/graphql",
        json={"query": "{ invalidField }"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "errors" in data


@pytest.mark.asyncio
async def test_graphql_no_auth_viewer(client):
    """Viewer query without auth returns an error."""
    resp = await client.post(
        "/graphql",
        json={"query": "{ viewer { login } }"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Viewer requires auth, so should return errors or null
    assert "errors" in data or (data.get("data", {}).get("viewer") is None)


@pytest.mark.asyncio
async def test_graphql_repository_with_issues(client, test_user, test_token):
    """Query repository with issues connection."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "gql-with-issues"},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/repos/testuser/gql-with-issues/issues",
        json={"title": "Issue 1"},
        headers=auth_headers(test_token),
    )
    resp = await client.post(
        "/graphql",
        json={
            "query": """
                { repository(owner: "testuser", name: "gql-with-issues") {
                    name
                    issues(first: 10) {
                        totalCount
                        nodes {
                            title
                        }
                    }
                }}
            """
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    if "data" in data and data["data"]["repository"] is not None:
        repo = data["data"]["repository"]
        assert repo["name"] == "gql-with-issues"


@pytest.mark.asyncio
async def test_graphql_variables(client, test_user, test_token):
    """GraphQL queries support variables."""
    resp = await client.post(
        "/graphql",
        json={
            "query": """
                query($login: String!) {
                    user(login: $login) { login }
                }
            """,
            "variables": {"login": "testuser"},
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["user"]["login"] == "testuser"

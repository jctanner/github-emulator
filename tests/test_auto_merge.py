"""Tests for the GitHub auto-merge compatibility path."""

from tests.conftest import auth_headers


API = "/api/v3"


async def _graphql(client, token, query, variables):
    response = await client.post(
        "/graphql",
        json={"query": query, "variables": variables},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    payload = response.json()
    assert "errors" not in payload, payload.get("errors")
    return payload["data"]


async def test_auto_merge_on_unprotected_branch_merges_without_ready_label(
    client, test_token
):
    headers = auth_headers(test_token)
    repo = await client.post(
        f"{API}/user/repos",
        json={"name": "auto-merge"},
        headers=headers,
    )
    assert repo.status_code == 201

    pr_response = await client.post(
        f"{API}/repos/testuser/auto-merge/pulls",
        json={"title": "Queue me", "head": "feature", "base": "main"},
        headers=headers,
    )
    assert pr_response.status_code == 201
    pr = pr_response.json()

    merged_request = await _graphql(
        client,
        test_token,
        """
        mutation($input: EnablePullRequestAutoMergeInput!) {
          enablePullRequestAutoMerge(input: $input) {
            pullRequest {
              number
              merged
              mergeStateStatus
              autoMergeRequest { mergeMethod commitHeadline }
            }
          }
        }
        """,
        {
            "input": {
                "pullRequestId": pr["node_id"],
                "mergeMethod": "SQUASH",
                "commitHeadline": "Queue me",
            }
        },
    )
    assert merged_request["enablePullRequestAutoMerge"]["pullRequest"] == {
        "number": 1,
        "merged": True,
        "mergeStateStatus": "CLEAN",
        "autoMergeRequest": None,
    }

    merged = await client.get(
        f"{API}/repos/testuser/auto-merge/pulls/1",
        headers=headers,
    )
    assert merged.status_code == 200
    assert merged.json()["merged"] is True
    assert merged.json()["state"] == "closed"

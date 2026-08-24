"""Regression coverage for activity-triggered Actions workflow runs."""

import pytest
from sqlalchemy import select

from app.models.actions import WorkflowRun
from app.services import workflow_service
from tests.conftest import API, auth_headers


def test_activity_trigger_matching_forms():
    payload = {"action": "opened", "pull_request": {"base": {"ref": "main"}}}

    assert workflow_service.evaluate_trigger(
        {"on": "pull_request_target"}, "pull_request_target", payload
    )
    assert workflow_service.evaluate_trigger(
        {"on": ["issues", "pull_request_target"]}, "pull_request_target", payload
    )
    assert workflow_service.evaluate_trigger(
        {"on": {"pull_request_target": {}}}, "pull_request_target", payload
    )
    assert workflow_service.evaluate_trigger(
        {"on": {"pull_request_target": {"types": ["opened"]}}},
        "pull_request_target",
        payload,
    )
    assert not workflow_service.evaluate_trigger(
        {"on": {"pull_request_target": {"types": ["closed"]}}},
        "pull_request_target",
        payload,
    )


@pytest.mark.asyncio
async def test_issue_activity_dispatches_runs(client, db_session, test_user, test_token,
                                              test_repo_with_init, monkeypatch):
    _owner, repo_name, _repo_data = test_repo_with_init

    async def fake_detect(_path, _ref="HEAD"):
        return [{
            "_path": ".github/workflows/activity.yml",
            "name": "Activity",
            "on": {"issues": {"types": ["opened", "edited"]}},
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

    headers = auth_headers(test_token)
    created = await client.post(
        f"{API}/repos/{_owner}/{repo_name}/issues",
        json={"title": "Trigger me", "body": "body"},
        headers=headers,
    )
    assert created.status_code == 201

    edited = await client.patch(
        f"{API}/repos/{_owner}/{repo_name}/issues/1",
        json={"title": "Edited"},
        headers=headers,
    )
    assert edited.status_code == 200

    runs = (await db_session.execute(
        select(WorkflowRun).order_by(WorkflowRun.id)
    )).scalars().all()
    assert [run.event for run in runs] == ["issues", "issues"]
    assert [run.trigger_payload["action"] for run in runs] == ["opened", "edited"]
    assert runs[0].trigger_payload["issue"]["number"] == 1
    assert runs[0].trigger_payload["repository"]["full_name"] == "testuser/init-repo"

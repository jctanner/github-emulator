"""Regression coverage for activity-triggered Actions workflow runs."""

import pytest
from sqlalchemy import select
from types import SimpleNamespace

from app.models.actions import WorkflowJob, WorkflowRun
from app.models.repository import Repository
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


@pytest.mark.parametrize(
    ("event", "action", "config"),
    [
        ("issues", "opened", {"types": ["opened", "edited", "labeled"]}),
        ("issues", "edited", {"types": ["opened", "edited", "labeled"]}),
        ("issues", "labeled", {"types": ["opened", "edited", "labeled"]}),
        ("issue_comment", "created", {"types": ["created"]}),
        ("pull_request_target", "opened", {"types": ["opened", "synchronize"]}),
        ("pull_request_target", "synchronize", {"types": ["opened", "synchronize"]}),
        ("pull_request_target", "ready_for_review", {"types": ["ready_for_review"]}),
        ("pull_request_target", "closed", {"types": ["closed"]}),
        ("pull_request_target", "labeled", {"types": ["labeled"]}),
        ("pull_request_target", "unlabeled", {"types": ["unlabeled"]}),
        ("pull_request_review", "submitted", {"types": ["submitted"]}),
    ],
)
def test_fullsend_activity_matrix_matches(event, action, config):
    payload = {
        "action": action,
        "issue": {"number": 1},
        "pull_request": {"number": 1, "base": {"ref": "main"}},
    }
    assert workflow_service.evaluate_trigger(
        {"on": {event: config}}, event, payload
    )
    assert not workflow_service.evaluate_trigger(
        {"on": {event: {"types": ["some-other-action"]}}}, event, payload
    )


def test_job_if_evaluates_fullsend_label_guard():
    condition = (
        "github.event.action != 'labeled' || "
        "startsWith(github.event.label.name, 'ready-')"
    )
    assert not workflow_service.evaluate_job_if(
        condition,
        {"github": {"event": {"action": "labeled", "label": {"name": "duplicate"}}}},
    )
    assert workflow_service.evaluate_job_if(
        condition,
        {"github": {"event": {"action": "labeled", "label": {"name": "ready-triage"}}}},
    )
    assert workflow_service.evaluate_job_if(
        condition,
        {"github": {"event": {"action": "opened", "label": {"name": ""}}}},
    )


@pytest.mark.asyncio
async def test_nested_reusable_workflows_forward_inputs_and_remap_jobs(monkeypatch):
    config_repo = SimpleNamespace(full_name="org/config", disk_path="config.git")

    async def fake_detect(path, ref="HEAD"):
        if path == "config.git":
            return [
                {
                    "_path": ".github/workflows/dispatch.yml",
                    "jobs": {
                        "stage": {
                            "uses": "./.github/workflows/triage.yml@main",
                            "with": {"issue": "${{ inputs.issue }}"},
                        }
                    },
                },
                {
                    "_path": ".github/workflows/triage.yml",
                    "jobs": {
                        "agent": {
                            "runs-on": ["self-hosted"],
                            "env": {"ISSUE": "${{ inputs.issue }}"},
                            "steps": [{"run": "echo $ISSUE"}],
                        }
                    },
                },
            ]
        raise AssertionError((path, ref))

    class Result:
        def scalar_one_or_none(self):
            return config_repo

    class FakeDB:
        async def execute(self, _query):
            return Result()

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    result = await workflow_service.materialize_reusable_workflows(
        {
            "jobs": {
                "dispatch": {
                    "uses": "org/config/.github/workflows/dispatch.yml@main",
                    "if": "github.event.action != 'labeled'",
                    "with": {"issue": "42"},
                }
            }
        },
        "caller.git",
        "main",
        FakeDB(),
    )

    assert list(result["jobs"]) == ["dispatch / stage / agent"]
    assert result["jobs"]["dispatch / stage / agent"]["env"]["ISSUE"] == "42"
    assert result["jobs"]["dispatch / stage / agent"]["if"] == "github.event.action != 'labeled'"


@pytest.mark.asyncio
async def test_local_reusable_workflow_without_ref_is_resolved(monkeypatch):
    async def fake_detect(path, ref="HEAD"):
        assert path == "caller.git"
        assert ref == "main"
        return [{
            "_path": ".github/workflows/agent.yml",
            "jobs": {
                "run": {
                    "runs-on": ["self-hosted", "linux"],
                    "steps": [{"run": "echo local"}],
                }
            },
        }]

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    result = await workflow_service.materialize_reusable_workflows(
        {
            "jobs": {
                "agent": {
                    "uses": "./.github/workflows/agent.yml",
                }
            }
        },
        "caller.git",
        "main",
    )

    assert list(result["jobs"]) == ["agent / run"]
    assert result["jobs"]["agent / run"]["runs-on"] == ["self-hosted", "linux"]


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
                    "env": {
                        "ISSUE_NUMBER": "${{ github.event.issue.number }}",
                        "EVENT_ACTION": "${{ github.event.action }}",
                    },
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
    assert runs[0].trigger_payload["sender"]["login"] == "testuser"
    jobs = (await db_session.execute(
        select(WorkflowJob).order_by(WorkflowJob.id)
    )).scalars().all()
    assert jobs[0].steps[0]["env"]["ISSUE_NUMBER"] == "1"
    assert jobs[0].steps[0]["env"]["EVENT_ACTION"] == "opened"


@pytest.mark.asyncio
async def test_issue_comment_and_label_events_are_distinct(
    client, db_session, test_token, test_repo_with_init, monkeypatch
):
    owner, repo_name, _repo_data = test_repo_with_init

    async def fake_detect(_path, _ref="HEAD"):
        return [{
            "_path": ".github/workflows/activity.yml",
            "name": "Activity",
            "on": {
                "issue_comment": {"types": ["created"]},
                "issues": {"types": ["labeled"]},
            },
            "jobs": {"record": {"runs-on": ["self-hosted"], "steps": [{"run": "true"}]}},
        }]

    async def fake_ref_sha(_path, _ref):
        return "a" * 40

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    monkeypatch.setattr(workflow_service, "get_ref_sha", fake_ref_sha)
    headers = auth_headers(test_token)

    created = await client.post(
        f"{API}/repos/{owner}/{repo_name}/issues",
        json={"title": "Trigger me"}, headers=headers,
    )
    assert created.status_code == 201
    label = await client.post(
        f"{API}/repos/{owner}/{repo_name}/labels",
        json={"name": "ready-for-code", "color": "00ff00"}, headers=headers,
    )
    assert label.status_code == 201
    labeled = await client.patch(
        f"{API}/repos/{owner}/{repo_name}/issues/1",
        json={"labels": ["ready-for-code"]}, headers=headers,
    )
    assert labeled.status_code == 200
    commented = await client.post(
        f"{API}/repos/{owner}/{repo_name}/issues/1/comments",
        json={"body": "/fs-triage"}, headers=headers,
    )
    assert commented.status_code == 201

    runs = (await db_session.execute(
        select(WorkflowRun).order_by(WorkflowRun.id)
    )).scalars().all()
    assert [(run.event, run.trigger_payload.get("action", "")) for run in runs] == [
        ("issues", "labeled"),
        ("issue_comment", "created"),
    ]
    assert runs[0].trigger_payload["label"]["name"] == "ready-for-code"
    assert runs[1].trigger_payload["comment"]["body"] == "/fs-triage"


@pytest.mark.asyncio
async def test_pull_request_review_and_push_sync_events(
    client, db_session, test_user, test_token, test_repo_with_init, monkeypatch
):
    async def fake_detect(_path, _ref="HEAD"):
        return [{
            "_path": ".github/workflows/activity.yml",
            "name": "Activity",
            "on": {
                "push": {},
                "pull_request_target": {"types": ["opened", "synchronize"]},
                "pull_request_review": {"types": ["submitted"]},
            },
            "jobs": {"record": {"runs-on": ["self-hosted"], "steps": [{"run": "true"}]}},
        }]

    async def fake_ref_sha(_path, ref):
        return "b" * 40

    async def fake_changed(_path, _before, _after):
        return [("src/example.py", "M")]

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    monkeypatch.setattr(workflow_service, "get_ref_sha", fake_ref_sha)
    monkeypatch.setattr(workflow_service, "_get_changed_files_between", fake_changed)
    headers = auth_headers(test_token)

    created = await client.post(
        f"{API}/repos/testuser/init-repo/pulls",
        json={"title": "Event PR", "head": "feature", "base": "main"},
        headers=headers,
    )
    assert created.status_code == 201
    review = await client.post(
        f"{API}/repos/testuser/init-repo/pulls/1/reviews",
        json={"body": "looks good", "event": "APPROVE"}, headers=headers,
    )
    assert review.status_code == 201

    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/init-repo")
    )).scalar_one()
    await workflow_service.process_push_event(
        db_session, repo, test_user, before_sha="c" * 40, ref_name="feature"
    )

    runs = (await db_session.execute(
        select(WorkflowRun).order_by(WorkflowRun.id)
    )).scalars().all()
    assert [(run.event, run.trigger_payload.get("action", "")) for run in runs] == [
        ("pull_request_target", "opened"),
        ("pull_request_review", "submitted"),
        ("push", ""),
        ("pull_request_target", "synchronize"),
    ]
    assert runs[1].trigger_payload["review"]["state"] == "APPROVED"
    assert runs[2].trigger_payload["created"] is False
    assert runs[3].trigger_payload["pull_request"]["number"] == 1


@pytest.mark.asyncio
async def test_graphql_pull_request_creation_dispatches_opened_event(
    client, db_session, test_token, test_repo_with_init, monkeypatch
):
    async def fake_detect(_path, _ref="HEAD"):
        return [{
            "_path": ".github/workflows/activity.yml",
            "name": "Activity",
            "on": {"pull_request_target": {"types": ["opened"]}},
            "jobs": {
                "record": {
                    "runs-on": ["self-hosted"],
                    "steps": [{"run": "true"}],
                }
            },
        }]

    async def fake_ref_sha(_path, _ref):
        return "b" * 40

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    monkeypatch.setattr(workflow_service, "get_ref_sha", fake_ref_sha)
    _owner, _name, repository_json = test_repo_with_init

    response = await client.post(
        "/graphql",
        headers=auth_headers(test_token),
        json={
            "query": """
                mutation CreatePullRequest($input: CreatePullRequestInput!) {
                  createPullRequest(input: $input) {
                    pullRequest { number title }
                  }
                }
            """,
            "variables": {
                "input": {
                    "repositoryId": str(repository_json["id"]),
                    "headRefName": "feature",
                    "baseRefName": "main",
                    "title": "GraphQL event PR",
                    "body": "Created through gh-compatible GraphQL",
                }
            },
        },
    )

    assert response.status_code == 200
    assert "errors" not in response.json()
    run = (await db_session.execute(select(WorkflowRun))).scalar_one()
    assert run.event == "pull_request_target"
    assert run.trigger_payload["action"] == "opened"
    assert run.trigger_payload["pull_request"]["number"] == 1
    assert run.trigger_payload["sender"]["login"] == "testuser"

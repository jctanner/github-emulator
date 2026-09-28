"""Actions can be switched off per repository.

GitHub's /repos/{owner}/{repo}/actions/permissions. A mirror of an upstream
repository carries upstream's CI workflows, and every push to the mirror
dispatched them here: lint, tests, and functional-test runs queued on the
hosted stand-in runner for a repository that is content, not a project.
Disabling Actions on the mirror is how a real administrator silences that,
so the emulator honours the same setting in the same place.
"""

import pytest
from sqlalchemy import select

from app.models.actions import WorkflowRun
from app.services import workflow_service
from app.services.workflow_service import process_push_event
from tests.conftest import API, auth_headers


def _workflow(monkeypatch):
    async def fake_detect(_path, _ref="HEAD"):
        return [{
            "_path": ".github/workflows/ci.yml", "name": "CI",
            "on": {"push": {}, "workflow_dispatch": {}},
            "jobs": {"test": {"runs-on": ["ubuntu-24.04"], "steps": [{"run": "echo ci"}]}},
        }]

    async def fake_ref_sha(_path, _ref):
        return "a" * 40

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    monkeypatch.setattr(workflow_service, "get_ref_sha", fake_ref_sha)


async def _runs(db_session):
    return (await db_session.execute(select(WorkflowRun).order_by(WorkflowRun.id))).scalars().all()


@pytest.mark.asyncio
async def test_the_setting_reads_as_github_shapes_it(client, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    url = f"{API}/repos/{owner}/{repo}/actions/permissions"
    assert (await client.get(url, headers=auth_headers(test_token))).json() == {"enabled": True, "allowed_actions": "all"}
    resp = await client.put(url, headers=auth_headers(test_token), json={"enabled": False})
    assert resp.status_code == 204
    assert (await client.get(url, headers=auth_headers(test_token))).json()["enabled"] is False
    assert (await client.put(url, headers=auth_headers(test_token), json={"enabled": "no"})).status_code == 422


@pytest.mark.asyncio
async def test_a_push_to_a_repository_with_actions_off_creates_no_run(client, db_session, test_user, test_token, test_repo_with_init, monkeypatch):
    owner, repo, _ = test_repo_with_init
    _workflow(monkeypatch)
    from app.models.repository import Repository
    repository = (await db_session.execute(select(Repository).where(Repository.full_name == f"{owner}/{repo}"))).scalar_one()

    await client.put(f"{API}/repos/{owner}/{repo}/actions/permissions", headers=auth_headers(test_token), json={"enabled": False})
    await db_session.refresh(repository)
    assert await process_push_event(db_session, repository, test_user, ref_name="main") == []
    assert await _runs(db_session) == []

    await client.put(f"{API}/repos/{owner}/{repo}/actions/permissions", headers=auth_headers(test_token), json={"enabled": True})
    await db_session.refresh(repository)
    (run,) = await process_push_event(db_session, repository, test_user, ref_name="main")
    assert run.event == "push"


@pytest.mark.asyncio
async def test_workflow_dispatch_is_refused_when_actions_is_off(client, test_repo_with_init, test_token, monkeypatch):
    owner, repo, _ = test_repo_with_init
    _workflow(monkeypatch)
    await client.put(f"{API}/repos/{owner}/{repo}/actions/permissions", headers=auth_headers(test_token), json={"enabled": False})
    resp = await client.post(f"{API}/repos/{owner}/{repo}/actions/workflows/ci.yml/dispatches",
                             headers=auth_headers(test_token), json={"ref": "main"})
    assert resp.status_code == 403
    assert "disabled" in resp.text

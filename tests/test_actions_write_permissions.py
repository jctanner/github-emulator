"""Actions writes need the caller's permission on the repository.

GitHub requires write access to dispatch, cancel or re-run a workflow and to
manage artifacts, and admin access for Actions secrets, variables and
permissions. Cancel also acts only on a run of the repository in its path.
"""

import hashlib
import secrets

import pytest
from sqlalchemy import select

from app.models.actions import Workflow, WorkflowRun
from app.models.repository import Collaborator, Repository
from app.models.token import PersonalAccessToken
from app.models.user import User
from tests.conftest import API, auth_headers


async def _person(db_session, login: str) -> tuple[User, str]:
    user = User(login=login, hashed_password=hashlib.sha256(b"pw").hexdigest(),
                email=f"{login}@localhost", name=login, site_admin=False)
    db_session.add(user)
    await db_session.flush()
    raw = f"ghp_{secrets.token_hex(20)}"
    db_session.add(PersonalAccessToken(
        user_id=user.id, name=login, token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        token_prefix=raw[:8], scopes=["repo", "workflow"],
    ))
    await db_session.commit()
    return user, raw


async def _repo_with_run(client, db_session, test_user, test_token, name: str) -> tuple[Repository, WorkflowRun]:
    assert (await client.post(f"{API}/user/repos", json={"name": name}, headers=auth_headers(test_token))).status_code == 201
    repo = (await db_session.execute(select(Repository).where(Repository.full_name == f"testuser/{name}"))).scalar_one()
    workflow = Workflow(repo_id=repo.id, name="CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(workflow_id=workflow.id, repo_id=repo.id, head_sha="abc", head_branch="main",
                      event="push", status="in_progress", run_number=1, actor_id=test_user.id)
    db_session.add(run)
    await db_session.commit()
    return repo, run


@pytest.fixture
async def setup(client, db_session, test_user, test_token):
    repo, run = await _repo_with_run(client, db_session, test_user, test_token, "perm-repo")
    _other, other_run = await _repo_with_run(client, db_session, test_user, test_token, "other-repo")
    reader, reader_token = await _person(db_session, "reader")
    writer, writer_token = await _person(db_session, "writer")
    _outsider, outsider_token = await _person(db_session, "nobody")
    db_session.add_all([
        Collaborator(repo_id=repo.id, user_id=reader.id, permission="pull"),
        Collaborator(repo_id=repo.id, user_id=writer.id, permission="push"),
    ])
    await db_session.commit()
    return {
        "run": run.id, "other_run": other_run.id, "base": f"{API}/repos/testuser/perm-repo/actions",
        "reader": auth_headers(reader_token), "writer": auth_headers(writer_token),
        "outsider": auth_headers(outsider_token), "owner": auth_headers(test_token),
    }


@pytest.mark.asyncio
async def test_cancel_needs_write_access(client, setup):
    url = f"{setup['base']}/runs/{setup['run']}/cancel"
    assert (await client.post(url, headers=setup["outsider"])).status_code == 403
    assert (await client.post(url, headers=setup["reader"])).status_code == 403
    assert (await client.post(url, headers=setup["writer"])).status_code == 202


@pytest.mark.asyncio
async def test_cancel_acts_only_on_a_run_of_the_repository_in_its_path(client, setup):
    url = f"{setup['base']}/runs/{setup['other_run']}/cancel"
    assert (await client.post(url, headers=setup["owner"])).status_code == 404


@pytest.mark.asyncio
async def test_rerun_needs_write_access(client, setup):
    url = f"{setup['base']}/runs/{setup['run']}/rerun"
    assert (await client.post(url, headers=setup["reader"])).status_code == 403


@pytest.mark.asyncio
async def test_secrets_variables_and_permissions_need_admin(client, setup):
    base = setup["base"]
    writes = [
        ("put", f"{base}/secrets/S", {"value": "v"}),
        ("post", f"{base}/variables", {"name": "X", "value": "1"}),
        ("put", f"{base}/permissions", {"enabled": False}),
        ("put", f"{base}/permissions/workflow", {"default_workflow_permissions": "write"}),
    ]
    for who in ("outsider", "reader", "writer"):
        for method, url, body in writes:
            resp = await getattr(client, method)(url, json=body, headers=setup[who])
            assert resp.status_code == 403, (who, url, resp.status_code)
    for method, url, body in writes:
        resp = await getattr(client, method)(url, json=body, headers=setup["owner"])
        assert resp.status_code in (201, 204), (url, resp.status_code)

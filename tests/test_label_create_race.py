"""Adding a label that does not exist yet, from two requests at once."""

import pytest
from sqlalchemy import func, select

from app.api.labels import _get_or_create_label
from app.models.label import Label
from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_a_label_created_meanwhile_is_reused(client, db_session, test_token):
    h = auth_headers(test_token)
    created = await client.post(f"{API}/user/repos", json={"name": "lr"}, headers=h)
    repo_id = created.json()["id"]
    # This session looked the label up before another request created it:
    # its INSERT would collide. The insert is skipped and the row read back.
    assert (await db_session.execute(select(Label).where(Label.repo_id == repo_id, Label.name == "new"))).first() is None
    made = await client.post(f"{API}/repos/testuser/lr/labels", json={"name": "new"}, headers=h)
    assert made.status_code == 201
    label = await _get_or_create_label(db_session, repo_id, "new")
    assert label.name == "new"
    count = (await db_session.execute(
        select(func.count()).select_from(Label).where(Label.repo_id == repo_id, Label.name == "new")
    )).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_a_new_label_is_created(db_session, client, test_token):
    h = auth_headers(test_token)
    repo_id = (await client.post(f"{API}/user/repos", json={"name": "lr2"}, headers=h)).json()["id"]
    label = await _get_or_create_label(db_session, repo_id, "fresh")
    assert label.id is not None and label.color == "ededed"


@pytest.mark.asyncio
async def test_the_lookup_missing_a_label_another_request_just_made(client, db_session, test_token, monkeypatch):
    h = auth_headers(test_token)
    repo_id = (await client.post(f"{API}/user/repos", json={"name": "lr3"}, headers=h)).json()["id"]
    made = await client.post(f"{API}/repos/testuser/lr3/labels", json={"name": "dup"}, headers=h)
    assert made.status_code == 201
    # The first lookup runs before the other request committed: it sees nothing.
    real_execute = db_session.execute
    calls = {"n": 0}

    async def execute(statement, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return await real_execute(select(Label).where(Label.id == -1))
        return await real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", execute)
    label = await _get_or_create_label(db_session, repo_id, "dup")
    assert label.name == "dup"

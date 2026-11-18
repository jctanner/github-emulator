"""Users and organizations share one id space, as accounts do on GitHub."""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.migrations import upgrade_database_sync
from app.models import (
    Organization,
    OrgMembership,
    OrgRoleAssignment,
    Repository,
    Team,
    User,
    Webhook,
)
from app.services import webhook_service
from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_users_and_organizations_never_share_an_id(db_session):
    accounts = [
        User(login="first", hashed_password="x"),
        Organization(login="first-org"),
        User(login="second", hashed_password="x"),
        Organization(login="second-org"),
    ]
    for account in accounts:
        db_session.add(account)
        await db_session.flush()
    await db_session.commit()

    ids = [account.id for account in accounts]
    assert ids == sorted(set(ids))


@pytest.mark.asyncio
async def test_an_explicit_id_held_by_the_other_table_is_refused(db_session):
    org = Organization(login="taken-org")
    db_session.add(org)
    await db_session.commit()

    db_session.add(User(id=org.id, login="clash", hashed_password="x"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_owner_ids_agree_across_rest_graphql_and_webhooks(
    client, test_user, test_token, webhook_requests
):
    headers = auth_headers(test_token)
    assert (await client.post(f"{API}/orgs", json={"login": "acct-org"}, headers=headers)).status_code == 201
    assert (await client.post(f"{API}/orgs/acct-org/repos", json={"name": "org-repo"}, headers=headers)).status_code == 201
    assert (await client.post(f"{API}/user/repos", json={"name": "own-repo"}, headers=headers)).status_code == 201

    org_id = (await client.get(f"{API}/orgs/acct-org")).json()["id"]
    user_id = (await client.get(f"{API}/users/testuser")).json()["id"]
    assert org_id != user_id

    org_repo = (await client.get(f"{API}/repos/acct-org/org-repo", headers=headers)).json()
    own_repo = (await client.get(f"{API}/repos/testuser/own-repo", headers=headers)).json()
    assert org_repo["owner"]["id"] == org_id
    assert own_repo["owner"]["id"] == user_id

    query = '{ organization(login: "acct-org") { databaseId } user(login: "testuser") { databaseId } }'
    data = (await client.post("/graphql", json={"query": query}, headers=headers)).json()["data"]
    assert data["organization"]["databaseId"] == org_id
    assert data["user"]["databaseId"] == user_id

    hook = await client.post(
        f"{API}/repos/acct-org/org-repo/hooks",
        json={"config": {"url": "http://receiver.test/org"}, "events": ["issues"]},
        headers=headers,
    )
    assert hook.status_code == 201
    opened = await client.post(f"{API}/repos/acct-org/org-repo/issues", json={"title": "t"}, headers=headers)
    assert opened.status_code == 201
    await webhook_service.wait_for_deliveries()
    (delivery,) = [r for r in webhook_requests if r.headers["x-github-event"] == "issues"]
    payload = json.loads(delivery.content)
    assert payload["repository"]["owner"]["id"] == org_id
    assert payload["sender"]["id"] == user_id


def test_migration_moves_organizations_off_user_ids(tmp_path, monkeypatch):
    """A database from before 0018: organization 1 has user 1's id, and
    organization 3 an id at or below the highest user id handed out (user 5,
    since deleted). Both move above every id handed out, their references
    follow, organization 9 stays, and new accounts continue above them."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.orm import Session

    path = tmp_path / "accounts.db"
    url = f"sqlite+aiosqlite:///{path}"
    monkeypatch.delenv("GITHUB_EMULATOR_DATABASE_URL", raising=False)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0017_org_role_assignments")

    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        run = lambda sql: connection.execute(text(sql))  # noqa: E731
        run("DROP TABLE IF EXISTS account_ids")
        for user_id in (1, 2, 5):
            run(f"INSERT INTO users (id, login, hashed_password, site_admin, type) VALUES ({user_id}, 'u{user_id}', 'x', 0, 'User')")
        run("DELETE FROM users WHERE id = 5")
        for org_id in (1, 3, 9):
            run(f"INSERT INTO organizations (id, login) VALUES ({org_id}, 'o{org_id}')")
    # Users and organizations go in as raw rows: the ORM would allocate
    # their ids from account_ids, which this revision does not have yet.
    with Session(engine) as session:
        session.add_all([
            Repository(id=1, owner_id=2, organization_id=1, owner_type="Organization", name="r", full_name="o1/r"),
            Repository(id=2, owner_id=2, organization_id=9, owner_type="Organization", name="r", full_name="o9/r"),
            OrgMembership(id=1, org_id=1, user_id=1, role="admin"),
            OrgMembership(id=2, org_id=3, user_id=1, role="member"),
            OrgRoleAssignment(org_id=1, user_id=2, role_id=1),
            Team(org_id=3, name="t", slug="t"),
            Webhook(org_id=1, url="http://h", events=[]),
        ])
        session.commit()
    engine.dispose()

    upgrade_database_sync(url)

    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        value = lambda sql: connection.execute(text(sql)).scalar_one()  # noqa: E731
        orgs = dict(connection.execute(text("SELECT login, id FROM organizations")).all())
        # Above user 5 (deleted) and organization 9, in their old order.
        assert orgs == {"o1": 10, "o3": 11, "o9": 9}
        assert value("SELECT group_concat(organization_id) FROM repositories ORDER BY id") == "10,9"
        assert value("SELECT group_concat(org_id) FROM org_memberships ORDER BY id") == "10,11"
        assert value("SELECT org_id FROM org_role_assignments") == 10
        assert value("SELECT org_id FROM teams") == 11
        assert value("SELECT org_id FROM webhooks") == 10
    engine.dispose()

    with Session(create_engine(f"sqlite:///{path}")) as session:
        user = User(login="new-user", hashed_password="x")
        session.add(user)
        session.flush()
        org = Organization(login="new-org")
        session.add(org)
        session.flush()
        assert (user.id, org.id) == (12, 13)
        session.commit()

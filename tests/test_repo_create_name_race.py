"""A repository name taken between the check and the insert is a 422.

Two concurrent creates of the same name can both pass the existence check;
the unique index on ``full_name`` then refuses the second insert. GitHub
answers that request as a duplicate (422), and so does the emulator now,
instead of a 500 from the IntegrityError.
"""

import pytest

from app.services import repo_service
from tests.conftest import auth_headers

API = "/api/v3"
pytestmark = pytest.mark.asyncio


class _NoRow:
    def scalar_one_or_none(self):
        return None


class _MissesTheExistenceCheck:
    """A session whose first query (the existence check) sees no row."""

    def __init__(self, db):
        self._db = db
        self._first = True

    async def execute(self, *args, **kwargs):
        if self._first:
            self._first = False
            return _NoRow()
        return await self._db.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._db, name)


async def test_a_name_taken_after_the_check_is_a_duplicate(client, db_session, test_user, test_token):
    resp = await client.post(f"{API}/user/repos", json={"name": "raced"}, headers=auth_headers(test_token))
    assert resp.status_code == 201

    with pytest.raises(ValueError, match="already exists"):
        await repo_service.create_repo(
            _MissesTheExistenceCheck(db_session), owner=test_user, name="raced",
            namespace_login="testuser", owner_type="User",
        )
    # The session is usable after the rollback.
    await db_session.execute(repo_service.select(repo_service.Repository))

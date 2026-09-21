"""Who can see a private repository, across every endpoint rather than one.

The visibility check used to live inline in ``GET /repos/{owner}/{repo}`` and
nowhere else, so it was wrong in both directions at once: a private
repository's issues and contents were served to any authenticated token, and
the one endpoint that did check tested ownership, refusing collaborators who
had been added explicitly.
"""


import pytest
from sqlalchemy import select

from app.models.repository import Collaborator, Repository
from tests.conftest import API, auth_headers


@pytest.fixture
async def private_repo(client, test_token):
    created = await client.post(
        f"{API}/user/repos",
        json={"name": "secret-repo", "private": True, "auto_init": True},
        headers=auth_headers(test_token),
    )
    assert created.status_code == 201
    data = created.json()
    assert data["private"] is True
    # Something to find, so a leak is visible rather than an empty list.
    issue = await client.post(
        f"{API}/repos/{data['full_name']}/issues",
        json={"title": "confidential", "body": "should not be readable"},
        headers=auth_headers(test_token),
    )
    assert issue.status_code == 201
    return data


@pytest.fixture
async def outsider_token(db_session):
    """A user with an account and no relationship to the repository."""
    import hashlib
    import secrets

    from app.models.token import PersonalAccessToken
    from app.models.user import User

    user = User(
        login="outsider",
        hashed_password=hashlib.sha256(b"password").hexdigest(),
        email="outsider@localhost",
        name="Outsider",
        site_admin=False,
    )
    db_session.add(user)
    await db_session.flush()

    raw = f"ghp_{secrets.token_hex(20)}"
    db_session.add(
        PersonalAccessToken(
            user_id=user.id,
            name="probe",
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            token_prefix=raw[:8],
            scopes=["repo", "user"],
        )
    )
    await db_session.commit()
    return raw


# --- the leak -------------------------------------------------------------

@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "/issues",
        "/contents/README.md",
        "/commits",
        "/branches",
        "/labels",
        "/readme",
    ],
)
@pytest.mark.asyncio
async def test_an_outsider_sees_nothing_of_a_private_repository(
    client, private_repo, outsider_token, suffix
):
    response = await client.get(
        f"{API}/repos/{private_repo['full_name']}{suffix}",
        headers=auth_headers(outsider_token),
    )
    assert response.status_code == 404, (
        f"{suffix or '/'} leaked a private repository: {response.status_code}"
    )


@pytest.mark.asyncio
async def test_an_unauthenticated_request_sees_nothing(client, private_repo):
    """The branch a check placed after authentication would have missed."""
    for suffix in ("", "/issues"):
        response = await client.get(
            f"{API}/repos/{private_repo['full_name']}{suffix}"
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_the_refusal_is_404_not_403(client, private_repo, outsider_token):
    """403 would confirm the repository exists, which is what this hides."""
    response = await client.get(
        f"{API}/repos/{private_repo['full_name']}/issues",
        headers=auth_headers(outsider_token),
    )
    assert response.status_code == 404
    assert response.json()["message"] == "Not Found"


@pytest.mark.asyncio
async def test_a_write_is_refused_too(client, private_repo, outsider_token):
    response = await client.post(
        f"{API}/repos/{private_repo['full_name']}/issues",
        json={"title": "from an outsider"},
        headers=auth_headers(outsider_token),
    )
    assert response.status_code == 404


# --- the other direction --------------------------------------------------

@pytest.mark.asyncio
async def test_the_owner_still_sees_their_own_private_repository(
    client, private_repo, test_token
):
    for suffix in ("", "/issues", "/readme"):
        response = await client.get(
            f"{API}/repos/{private_repo['full_name']}{suffix}",
            headers=auth_headers(test_token),
        )
        assert response.status_code == 200, suffix


@pytest.mark.asyncio
async def test_a_collaborator_sees_a_private_repository(
    client, db_session, private_repo, outsider_token
):
    """The bug in the opposite direction: the old check tested ownership."""
    from app.models.user import User

    repository = (
        await db_session.execute(
            select(Repository).where(
                Repository.full_name == private_repo["full_name"]
            )
        )
    ).scalar_one()
    outsider = (
        await db_session.execute(select(User).where(User.login == "outsider"))
    ).scalar_one()
    db_session.add(
        Collaborator(
            repo_id=repository.id, user_id=outsider.id, permission="pull"
        )
    )
    await db_session.commit()

    for suffix in ("", "/issues"):
        response = await client.get(
            f"{API}/repos/{private_repo['full_name']}{suffix}",
            headers=auth_headers(outsider_token),
        )
        assert response.status_code == 200, suffix


@pytest.mark.asyncio
async def test_a_public_repository_is_unaffected(
    client, test_repo_with_init, outsider_token
):
    _owner, _name, repo = test_repo_with_init
    response = await client.get(
        f"{API}/repos/{repo['full_name']}/issues",
        headers=auth_headers(outsider_token),
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_an_unauthenticated_read_of_a_public_repository_still_works(
    client, test_repo_with_init
):
    _owner, _name, repo = test_repo_with_init
    response = await client.get(f"{API}/repos/{repo['full_name']}")
    assert response.status_code == 200


# --- the path matcher -----------------------------------------------------

def test_only_repository_paths_are_governed():
    from app.services.repository_access import repository_from_path

    assert repository_from_path("/api/v3/repos/o/r") == ("o", "r")
    assert repository_from_path("/api/v3/repos/o/r/issues/1/comments") == ("o", "r")
    assert repository_from_path("/repos/o/r/contents/a/b.txt") == ("o", "r")
    assert repository_from_path("/api/v3/user/repos") is None
    assert repository_from_path("/api/v3/orgs/o/repos") is None
    assert repository_from_path("/api/v3/user") is None
    assert repository_from_path("/.well-known/jwks.json") is None

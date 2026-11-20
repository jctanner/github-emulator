"""Search returns results only from repositories the caller can read.

The REST search endpoints used to hide private repositories from an anonymous
caller on ``/search/repositories`` only: any signed-in user found every
private repository, and issue, code and commit search filtered nothing at
all, anonymous callers included.
"""

import hashlib
import secrets
import time

import pytest
from jose import jwt
from sqlalchemy import select

from app.models.organization import OrgMembership
from app.models.repository import Collaborator, Repository
from app.models.search_index import CommitMetadata, FileContent
from app.models.token import PersonalAccessToken
from app.models.user import User
from tests.conftest import API, auth_headers

pytestmark = pytest.mark.asyncio

ORG = "needle-org"
PRIVATE = "testuser/hidden-needle"
PUBLIC = "testuser/open-needle"
ORG_PRIVATE = f"{ORG}/org-needle"
EVERYTHING = sorted([PRIVATE, PUBLIC, ORG_PRIVATE])


async def _user_token(db_session, login: str) -> str:
    user = User(
        login=login,
        hashed_password=hashlib.sha256(b"password").hexdigest(),
        email=f"{login}@localhost",
        name=login,
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


async def _repository(db_session, full_name: str) -> Repository:
    return (
        await db_session.execute(select(Repository).where(Repository.full_name == full_name))
    ).scalar_one()


async def _user(db_session, login: str) -> User:
    return (await db_session.execute(select(User).where(User.login == login))).scalar_one()


@pytest.fixture
async def world(client, db_session, test_token):
    """A private and a public user repository and a private organization
    repository, each with an issue, a file and a commit that name "needle"."""
    headers = auth_headers(test_token)
    for name, private in (("hidden-needle", True), ("open-needle", False)):
        made = await client.post(f"{API}/user/repos", headers=headers,
                                 json={"name": name, "private": private})
        assert made.status_code == 201, made.text
    org = await client.post(f"{API}/orgs", headers=headers, json={"login": ORG})
    assert org.status_code == 201, org.text
    made = await client.post(f"{API}/orgs/{ORG}/repos", headers=headers,
                             json={"name": "org-needle", "private": True})
    assert made.status_code == 201, made.text

    for full_name in EVERYTHING:
        issue = await client.post(f"{API}/repos/{full_name}/issues", headers=headers,
                                  json={"title": f"needle in {full_name}"})
        assert issue.status_code == 201, issue.text
    for full_name in EVERYTHING:
        repository = await _repository(db_session, full_name)
        db_session.add(FileContent(repo_id=repository.id, file_path="needle.txt",
                                   blob_sha=secrets.token_hex(20),
                                   content=f"needle in {full_name}\n", ref="main"))
        db_session.add(CommitMetadata(repo_id=repository.id, commit_sha=secrets.token_hex(20),
                                      message=f"needle in {full_name}"))
    await db_session.commit()


async def _search(client, headers, kind: str, q: str = "needle"):
    response = await client.get(f"{API}/search/{kind}", headers=headers, params={"q": q})
    assert response.status_code == 200, (kind, response.text)
    return response.json()


async def _found(client, headers) -> dict[str, list[str]]:
    """Which repositories each of the four searches returns results from."""
    found = {}
    repositories = await _search(client, headers, "repositories")
    found["repositories"] = sorted(item["full_name"] for item in repositories["items"])
    issues = await _search(client, headers, "issues")
    found["issues"] = sorted(item["repository_url"].split("/repos/")[-1] for item in issues["items"])
    for kind in ("code", "commits"):
        data = await _search(client, headers, kind)
        found[kind] = sorted(item["repository"]["full_name"] for item in data["items"])
    for kind, names in found.items():
        assert len(names) == len(set(names)), (kind, names)
    return found


def _all(names):
    return {kind: sorted(names) for kind in ("repositories", "issues", "code", "commits")}


async def test_a_non_member_finds_only_public_results(client, db_session, world):
    outsider = auth_headers(await _user_token(db_session, "outsider"))
    assert await _found(client, outsider) == _all([PUBLIC])


async def test_a_repo_qualifier_naming_an_unreadable_repository_finds_nothing(
    client, db_session, world
):
    outsider = auth_headers(await _user_token(db_session, "outsider"))
    for kind in ("code", "commits"):
        data = await _search(client, outsider, kind, f"needle repo:{PRIVATE}")
        assert data["total_count"] == 0 and data["items"] == [], kind
        # The same qualifier on a public repository still finds it.
        data = await _search(client, outsider, kind, f"needle repo:{PUBLIC}")
        assert data["total_count"] == 1, kind


async def test_an_anonymous_caller_finds_only_public_results(client, world):
    assert await _found(client, {}) == _all([PUBLIC])
    for kind in ("code", "commits"):
        data = await _search(client, {}, kind, f"needle repo:{PRIVATE}")
        assert data["total_count"] == 0, kind


async def test_a_collaborator_finds_the_private_repository(client, db_session, world):
    token = await _user_token(db_session, "collaborator")
    repository = await _repository(db_session, PRIVATE)
    collaborator = await _user(db_session, "collaborator")
    db_session.add(Collaborator(repo_id=repository.id, user_id=collaborator.id, permission="pull"))
    await db_session.commit()
    assert await _found(client, auth_headers(token)) == _all([PRIVATE, PUBLIC])


async def test_an_organization_member_finds_its_private_repository(client, db_session, world):
    token = await _user_token(db_session, "member")
    repository = await _repository(db_session, ORG_PRIVATE)
    member = await _user(db_session, "member")
    db_session.add(OrgMembership(org_id=repository.organization_id, user_id=member.id,
                                 role="member", state="active"))
    await db_session.commit()
    assert await _found(client, auth_headers(token)) == _all([ORG_PRIVATE, PUBLIC])


async def test_a_pending_organization_member_does_not(client, db_session, world):
    token = await _user_token(db_session, "invitee")
    repository = await _repository(db_session, ORG_PRIVATE)
    invitee = await _user(db_session, "invitee")
    db_session.add(OrgMembership(org_id=repository.organization_id, user_id=invitee.id,
                                 role="member", state="pending"))
    await db_session.commit()
    assert await _found(client, auth_headers(token)) == _all([PUBLIC])


async def test_the_owner_and_a_site_admin_find_everything(client, world, test_token, admin_token):
    assert await _found(client, auth_headers(test_token)) == _all(EVERYTHING)
    assert await _found(client, auth_headers(admin_token)) == _all(EVERYTHING)


async def _installation_token(client, admin_token, account: str, app_id: str, body: dict) -> dict:
    created = await client.post(
        f"{API}/admin/apps", headers=auth_headers(admin_token),
        json={"app_id": app_id, "name": f"Search {app_id}", "slug": f"search-{app_id}",
              "permissions": {"issues": "read", "contents": "read", "metadata": "read"}},
    )
    assert created.status_code == 201, created.text
    installation = await client.post(
        f"{API}/admin/apps/{app_id}/installations", headers=auth_headers(admin_token),
        json={"account_login": account, "account_type": "User", "repositories": []},
    )
    assert installation.status_code == 201, installation.text
    now = int(time.time())
    assertion = jwt.encode({"iss": app_id, "iat": now - 10, "exp": now + 60},
                           created.json()["private_key"], algorithm="RS256")
    minted = await client.post(
        f"{API}/app/installations/{installation.json()['id']}/access_tokens",
        headers={"Authorization": f"Bearer {assertion}"}, json=body,
    )
    assert minted.status_code == 201, minted.text
    return {"Authorization": f"token {minted.json()['token']}"}


async def test_an_installation_token_finds_only_its_installation_repositories(
    client, admin_token, world
):
    whole_account = await _installation_token(client, admin_token, "testuser", "2201", {})
    assert await _found(client, whole_account) == _all([PRIVATE, PUBLIC])

    narrowed = await _installation_token(client, admin_token, "testuser", "2202",
                                         {"repositories": ["hidden-needle"]})
    assert await _found(client, narrowed) == _all([PRIVATE])
    for kind in ("code", "commits"):
        data = await _search(client, narrowed, kind, f"needle repo:{PUBLIC}")
        assert data["total_count"] == 0, kind

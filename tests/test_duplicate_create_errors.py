"""Creating something that already exists must fail the way GitHub fails.

Clients tell "it is already there" from other failures by the 422 body, not
the status alone:

- a label: `{"message": "Validation Failed", "errors": [{"resource": "Label",
  "code": "already_exists", "field": "name"}]}`, with names compared
  case-insensitively;
- a release asset: the same shape with `"resource": "ReleaseAsset"`;
- a git ref: 422 `{"message": "Reference already exists"}`, and the ref is left
  where it was.

Found by Fullsend's behaviour tests, whose label bootstrap treats
`errors[].code == "already_exists"` as "nothing to do".
"""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


async def _repo(client, token, name, auto_init=False):
    resp = await client.post(
        f"{API}/user/repos", json={"name": name, "auto_init": auto_init},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_label(client, token, repo, name, color="ededed"):
    return await client.post(
        f"{API}/repos/testuser/{repo}/labels",
        json={"name": name, "color": color},
        headers=auth_headers(token),
    )


def _assert_already_exists(resp, resource, field):
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["message"] == "Validation Failed"
    assert {"resource": resource, "code": "already_exists", "field": field} in body["errors"]
    assert body["documentation_url"]


@pytest.mark.asyncio
async def test_duplicate_label_reports_already_exists(client, test_user, test_token):
    await _repo(client, test_token, "dup-label")
    assert (await _create_label(client, test_token, "dup-label", "bug")).status_code == 201

    resp = await _create_label(client, test_token, "dup-label", "bug", "111111")

    _assert_already_exists(resp, "Label", "name")


@pytest.mark.asyncio
async def test_label_names_compare_case_insensitively(client, test_user, test_token):
    await _repo(client, test_token, "dup-label-case")
    assert (await _create_label(client, test_token, "dup-label-case", "bug")).status_code == 201

    resp = await _create_label(client, test_token, "dup-label-case", "BUG")
    _assert_already_exists(resp, "Label", "name")

    got = await client.get(
        f"{API}/repos/testuser/dup-label-case/labels/Bug", headers=auth_headers(test_token)
    )
    assert got.status_code == 200, got.text
    assert got.json()["name"] == "bug"

    listed = await client.get(
        f"{API}/repos/testuser/dup-label-case/labels", headers=auth_headers(test_token)
    )
    assert [label["name"] for label in listed.json()] == ["bug"]


@pytest.mark.asyncio
async def test_renaming_a_label_onto_another_reports_already_exists(client, test_user, test_token):
    await _repo(client, test_token, "dup-label-rename")
    await _create_label(client, test_token, "dup-label-rename", "bug")
    await _create_label(client, test_token, "dup-label-rename", "defect")

    resp = await client.patch(
        f"{API}/repos/testuser/dup-label-rename/labels/defect",
        json={"new_name": "Bug"},
        headers=auth_headers(test_token),
    )
    _assert_already_exists(resp, "Label", "name")

    # Changing only the case of a label's own name is a rename, not a clash.
    recased = await client.patch(
        f"{API}/repos/testuser/dup-label-rename/labels/bug",
        json={"new_name": "Bug"},
        headers=auth_headers(test_token),
    )
    assert recased.status_code == 200, recased.text
    assert recased.json()["name"] == "Bug"


@pytest.mark.asyncio
async def test_duplicate_release_asset_reports_already_exists(client, test_user, test_token):
    await _repo(client, test_token, "dup-asset", auto_init=True)
    release = await client.post(
        f"{API}/repos/testuser/dup-asset/releases",
        json={"tag_name": "v1.0.0"},
        headers=auth_headers(test_token),
    )
    assert release.status_code == 201, release.text
    upload = f"{API}/repos/testuser/dup-asset/releases/{release.json()['id']}/assets?name=x.tar.gz"
    headers = {**auth_headers(test_token), "Content-Type": "application/gzip"}
    assert (await client.post(upload, content=b"one", headers=headers)).status_code == 201

    resp = await client.post(upload, content=b"two", headers=headers)

    _assert_already_exists(resp, "ReleaseAsset", "name")


@pytest.mark.asyncio
async def test_creating_an_existing_ref_is_refused(client, test_user, test_token):
    repo = await _repo(client, test_token, "dup-ref", auto_init=True)
    main_sha = (await client.get(
        f"{API}/repos/testuser/dup-ref/git/ref/heads/{repo['default_branch']}",
        headers=auth_headers(test_token),
    )).json()["object"]["sha"]
    commit = await client.post(
        f"{API}/repos/testuser/dup-ref/git/commits",
        json={"message": "other", "tree": (await client.get(
            f"{API}/repos/testuser/dup-ref/git/commits/{main_sha}",
            headers=auth_headers(test_token),
        )).json()["tree"]["sha"], "parents": [main_sha]},
        headers=auth_headers(test_token),
    )
    assert commit.status_code == 201, commit.text
    other_sha = commit.json()["sha"]

    first = await client.post(
        f"{API}/repos/testuser/dup-ref/git/refs",
        json={"ref": "refs/heads/feature", "sha": main_sha},
        headers=auth_headers(test_token),
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        f"{API}/repos/testuser/dup-ref/git/refs",
        json={"ref": "refs/heads/feature", "sha": other_sha},
        headers=auth_headers(test_token),
    )
    assert second.status_code == 422, second.text
    assert second.json()["message"] == "Reference already exists"

    ref = await client.get(
        f"{API}/repos/testuser/dup-ref/git/ref/heads/feature",
        headers=auth_headers(test_token),
    )
    assert ref.json()["object"]["sha"] == main_sha


async def test_a_label_with_non_ascii_capitals_is_found_by_its_name(client, test_user, test_token):
    """SQLite's lower() folds ASCII only, so the exact spelling must match too."""
    from urllib.parse import quote

    await _repo(client, test_token, "unicode-labels")
    assert (await _create_label(client, test_token, "unicode-labels", "Étiquette")).status_code == 201

    got = await client.get(f"{API}/repos/testuser/unicode-labels/labels/{quote('Étiquette')}", headers=auth_headers(test_token))
    assert got.status_code == 200, got.text
    issue = await client.post(
        f"{API}/repos/testuser/unicode-labels/issues", json={"title": "t", "labels": ["Étiquette"]},
        headers=auth_headers(test_token),
    )
    assert issue.status_code == 201, issue.text
    assert [label["name"] for label in issue.json()["labels"]] == ["Étiquette"]
    listed = await client.get(f"{API}/repos/testuser/unicode-labels/labels", headers=auth_headers(test_token))
    assert [label["name"] for label in listed.json()] == ["Étiquette"]


async def test_labels_differing_only_in_case_from_an_older_database(client, db_session, test_user, test_token):
    """A database written before names compared case-insensitively can hold
    both "bug" and "BUG"; each is found by its own spelling, without a 500."""
    from sqlalchemy import select

    from app.models.label import Label
    from app.models.repository import Repository

    await _repo(client, test_token, "case-legacy")
    repo = (await db_session.execute(select(Repository).where(Repository.full_name == "testuser/case-legacy"))).scalar_one()
    db_session.add_all([Label(repo_id=repo.id, name="bug", color="ededed"), Label(repo_id=repo.id, name="BUG", color="d73a4a")])
    await db_session.commit()

    for name, color in (("bug", "ededed"), ("BUG", "d73a4a")):
        got = await client.get(f"{API}/repos/testuser/case-legacy/labels/{name}", headers=auth_headers(test_token))
        assert got.status_code == 200, got.text
        assert got.json()["color"] == color
    issue = await client.post(
        f"{API}/repos/testuser/case-legacy/issues", json={"title": "t", "labels": ["BUG"]}, headers=auth_headers(test_token)
    )
    assert issue.status_code == 201, issue.text
    assert [label["color"] for label in issue.json()["labels"]] == ["d73a4a"]
    assert (await client.delete(f"{API}/repos/testuser/case-legacy/labels/BUG", headers=auth_headers(test_token))).status_code == 204

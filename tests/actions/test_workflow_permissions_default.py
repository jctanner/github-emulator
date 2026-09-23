"""The repository default a job inherits when it declares no permissions.

Without this setting the emulator treated such a job as permissive, which
wrongly allows writes on a repository whose default is "read" — a real
divergence from GitHub that B9 recorded rather than fixed.
"""

import pytest

from app.services import job_permissions
from tests.conftest import API, auth_headers


def test_write_default_is_permissive():
    """GitHub's own default; behaviour must be unchanged for it."""
    assert job_permissions.default_permissions_for("write") is None
    assert job_permissions.default_permissions_for(None) is None


def test_read_default_is_not_a_blanket_read():
    """GitHub's restricted default is contents and packages, plus metadata —
    not read on every scope."""
    resolved = job_permissions.default_permissions_for("read")
    assert resolved == {"contents": "read", "packages": "read", "metadata": "read"}


def test_a_job_declaring_nothing_may_write_under_the_write_default():
    assert job_permissions.check(
        "POST", "/api/v3/repos/o/r/issues", None, "write"
    ) is None


def test_a_job_declaring_nothing_may_not_write_under_the_read_default():
    """The divergence this closes: permissive was wrong here."""
    refusal = job_permissions.check("POST", "/api/v3/repos/o/r/issues", None, "read")
    assert refusal is not None


def test_a_job_declaring_nothing_may_still_read_under_the_read_default():
    assert job_permissions.check(
        "GET", "/api/v3/repos/o/r/contents/README.md", None, "read"
    ) is None


def test_an_explicit_permissions_block_still_wins_over_the_default():
    """A job that declares permissions is governed by them, not the default."""
    assert job_permissions.check(
        "POST", "/api/v3/repos/o/r/issues", {"issues": "write"}, "read"
    ) is None


def test_forks_is_mapped_rather_than_hitting_the_fallback():
    """The one repo segment with writes that the map did not cover; the
    fallback denied it, but by accident rather than by rule."""
    assert job_permissions._SCOPE_BY_SEGMENT["forks"] == "contents"
    assert job_permissions.check(
        "POST", "/api/v3/repos/o/r/forks", {"contents": "write"}
    ) is None
    assert job_permissions.check(
        "POST", "/api/v3/repos/o/r/forks", {"contents": "read"}
    ) is not None


@pytest.mark.asyncio
async def test_the_setting_round_trips_through_the_api(client, test_token, test_repo_with_init):
    url = f"{API}/repos/testuser/init-repo/actions/permissions/workflow"

    initial = await client.get(url, headers=auth_headers(test_token))
    assert initial.status_code == 200
    assert initial.json()["default_workflow_permissions"] == "write"

    updated = await client.put(
        url, headers=auth_headers(test_token),
        json={"default_workflow_permissions": "read", "can_approve_pull_request_reviews": True},
    )
    assert updated.status_code == 204

    after = await client.get(url, headers=auth_headers(test_token))
    assert after.json()["default_workflow_permissions"] == "read"
    assert after.json()["can_approve_pull_request_reviews"] is True


@pytest.mark.asyncio
async def test_an_invalid_default_is_refused(client, test_token, test_repo_with_init):
    resp = await client.put(
        f"{API}/repos/testuser/init-repo/actions/permissions/workflow",
        headers=auth_headers(test_token),
        json={"default_workflow_permissions": "sometimes"},
    )
    assert resp.status_code == 422
    assert "read" in resp.json()["message"]

"""Tests for profile pages in the web UI."""

import pytest

from app.models.organization import Organization
from app.models.repository import Repository


@pytest.mark.asyncio
async def test_org_profile_lists_repos_by_org_namespace(client, db_session, admin_user):
    org = Organization(login="opendatahub-io", name="OpenDataHub")
    db_session.add(org)
    await db_session.flush()

    db_session.add_all([
        Repository(
            owner_id=admin_user.id,
            owner_type="Organization",
            name="agent-eval-harness",
            full_name="opendatahub-io/agent-eval-harness",
            visibility="public",
        ),
        Repository(
            owner_id=admin_user.id,
            owner_type="Organization",
            name="eval-datasets",
            full_name="opendatahub-io/eval-datasets",
            visibility="public",
        ),
        Repository(
            owner_id=admin_user.id,
            owner_type="User",
            name="personal-tools",
            full_name="admin/personal-tools",
            visibility="public",
        ),
    ])
    await db_session.commit()

    resp = await client.get("/ui-legacy/opendatahub-io")

    assert resp.status_code == 200
    assert "agent-eval-harness" in resp.text
    assert "eval-datasets" in resp.text
    assert "personal-tools" not in resp.text
    assert '<span class="Counter">2</span>' in resp.text

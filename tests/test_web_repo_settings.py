"""Tests for repository-scoped web settings."""

import os

import pytest
from sqlalchemy import select

from app.git.bare_repo import get_default_branch
from app.models.apps import AppInstallation, GitHubApp
from app.models.branch import Branch, BranchProtection
from app.models.repository import Collaborator, Repository
from app.web.routes import _sign_session


@pytest.mark.asyncio
async def test_repository_owner_can_open_general_settings(
    client, test_user, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init
    client.cookies.set("ui_session", _sign_session(test_user.login))

    page = await client.get(f"/ui/{owner}/{repo_name}/settings")

    assert page.status_code == 200
    assert "General" in page.text
    assert "Repository name" in page.text
    assert "Default branch" in page.text
    assert "Features" in page.text
    assert f'/ui/{owner}/{repo_name}/settings/general' in page.text
    assert f'/ui/{owner}/{repo_name}/settings/branches' in page.text
    assert f'/ui/{owner}/{repo_name}/settings/actions/runners' in page.text
    assert f'/ui/{owner}/{repo_name}/settings/access' in page.text
    assert f'/ui/{owner}/{repo_name}/settings/installations' in page.text


@pytest.mark.asyncio
async def test_repository_settings_require_admin_access(
    client, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init

    response = await client.get(f"/ui/{owner}/{repo_name}/settings")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_general_settings_persist_metadata_visibility_and_features(
    client, db_session, test_user, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init
    client.cookies.set("ui_session", _sign_session(test_user.login))

    response = await client.post(
        f"/ui/{owner}/{repo_name}/settings/general",
        data={
            "description": "Updated through repository settings",
            "homepage": "https://example.test/project",
            "visibility": "private",
            "is_template": "true",
            "has_issues": "true",
            "has_wiki": "true",
        },
    )

    assert response.status_code == 302
    assert response.headers["location"].endswith("/settings?saved=general")
    repository = (
        await db_session.execute(
            select(Repository).where(Repository.full_name == f"{owner}/{repo_name}")
        )
    ).scalar_one()
    await db_session.refresh(repository)
    assert repository.description == "Updated through repository settings"
    assert repository.homepage == "https://example.test/project"
    assert repository.private is True
    assert repository.visibility == "private"
    assert repository.is_template is True
    assert repository.has_issues is True
    assert repository.has_wiki is True
    assert repository.has_projects is False
    assert repository.has_discussions is False


@pytest.mark.asyncio
async def test_default_branch_setting_updates_database_and_bare_head(
    client, db_session, test_user, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init
    client.cookies.set("ui_session", _sign_session(test_user.login))
    repository = (
        await db_session.execute(
            select(Repository).where(Repository.full_name == f"{owner}/{repo_name}")
        )
    ).scalar_one()

    response = await client.post(
        f"/ui/{owner}/{repo_name}/settings/default-branch",
        data={"default_branch": "main"},
    )

    assert response.status_code == 302
    assert response.headers["location"].endswith(
        "/settings?saved=default-branch"
    )
    await db_session.refresh(repository)
    assert repository.default_branch == "main"
    assert await get_default_branch(repository.disk_path) == "main"


@pytest.mark.asyncio
async def test_rename_setting_moves_repository_and_redirects(
    client, db_session, test_user, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init
    client.cookies.set("ui_session", _sign_session(test_user.login))
    repository = (
        await db_session.execute(
            select(Repository).where(Repository.full_name == f"{owner}/{repo_name}")
        )
    ).scalar_one()
    old_disk_path = repository.disk_path

    response = await client.post(
        f"/ui/{owner}/{repo_name}/settings/rename",
        data={"name": "renamed-repository"},
    )

    assert response.status_code == 302
    assert response.headers["location"] == (
        f"/ui/{owner}/renamed-repository/settings?saved=rename"
    )
    await db_session.refresh(repository)
    assert repository.full_name == f"{owner}/renamed-repository"
    assert repository.disk_path.endswith("/renamed-repository.git")
    assert os.path.isdir(repository.disk_path)
    assert not os.path.exists(old_disk_path)


@pytest.mark.asyncio
async def test_branch_settings_page_lists_protection_rules(
    client, test_user, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init
    client.cookies.set("ui_session", _sign_session(test_user.login))

    page = await client.get(f"/ui/{owner}/{repo_name}/settings/branches")

    assert page.status_code == 200
    assert "Branch protection rules" in page.text
    assert "Protect main" in page.text
    assert "Require a pull request before merging" in page.text
    assert "Require status checks to pass before merging" in page.text


@pytest.mark.asyncio
async def test_branch_settings_require_admin_access(client, test_repo_with_init):
    owner, repo_name, _repo_data = test_repo_with_init

    response = await client.get(f"/ui/{owner}/{repo_name}/settings/branches")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_actions_runner_settings_keep_settings_navigation(
    client, test_user, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init
    client.cookies.set("ui_session", _sign_session(test_user.login))

    page = await client.get(
        f"/ui/{owner}/{repo_name}/settings/actions/runners"
    )

    assert page.status_code == 200
    assert "Repository runners" in page.text
    assert "No runners registered" in page.text
    assert 'aria-label="Repository settings"' in page.text
    assert f'/ui/{owner}/{repo_name}/settings/branches' in page.text
    assert f'/ui/{owner}/{repo_name}/settings/actions/runners' in page.text


@pytest.mark.asyncio
async def test_actions_runner_settings_require_admin_access(
    client, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init

    response = await client.get(
        f"/ui/{owner}/{repo_name}/settings/actions/runners"
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_collaborator_settings_render_inside_settings_navigation(
    client, test_user, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init
    client.cookies.set("ui_session", _sign_session(test_user.login))

    page = await client.get(f"/ui/{owner}/{repo_name}/settings/access")

    assert page.status_code == 200
    assert "Collaborators and teams" in page.text
    assert "Direct access" in page.text
    assert "You haven't invited any collaborators yet" in page.text
    assert 'aria-label="Repository settings"' in page.text
    assert f'/ui/{owner}/{repo_name}/settings/access' in page.text


@pytest.mark.asyncio
async def test_collaborator_settings_add_update_and_remove_direct_access(
    client, db_session, test_user, admin_user, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init
    client.cookies.set("ui_session", _sign_session(test_user.login))
    base = f"/ui/{owner}/{repo_name}/settings/access"

    added = await client.post(
        f"{base}/collaborators",
        data={"username": admin_user.login, "permission": "push"},
    )
    assert added.status_code == 302
    assert added.headers["location"].endswith("/settings/access?saved=collaborator")
    collaborator = (
        await db_session.execute(
            select(Collaborator).where(Collaborator.user_id == admin_user.id)
        )
    ).scalar_one()
    assert collaborator.permission == "push"

    updated = await client.post(
        f"{base}/collaborators/{admin_user.login}/permission",
        data={"permission": "maintain"},
    )
    assert updated.status_code == 302
    await db_session.refresh(collaborator)
    assert collaborator.permission == "maintain"

    removed = await client.post(
        f"{base}/collaborators/{admin_user.login}/remove"
    )
    assert removed.status_code == 302
    remaining = (
        await db_session.execute(
            select(Collaborator).where(Collaborator.id == collaborator.id)
        )
    ).scalar_one_or_none()
    assert remaining is None


@pytest.mark.asyncio
async def test_collaborator_settings_reject_unknown_user(
    client, test_user, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init
    client.cookies.set("ui_session", _sign_session(test_user.login))

    response = await client.post(
        f"/ui/{owner}/{repo_name}/settings/access/collaborators",
        data={"username": "missing-user", "permission": "push"},
    )

    assert response.status_code == 302
    assert "error=User%20%27missing-user%27%20was%20not%20found" in response.headers[
        "location"
    ]


@pytest.mark.asyncio
async def test_collaborator_settings_require_admin_access(
    client, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init

    page = await client.get(f"/ui/{owner}/{repo_name}/settings/access")
    mutation = await client.post(
        f"/ui/{owner}/{repo_name}/settings/access/collaborators",
        data={"username": "admin", "permission": "push"},
    )

    assert page.status_code == 403
    assert mutation.status_code == 403


@pytest.mark.asyncio
async def test_github_apps_settings_list_repository_installations(
    client, db_session, test_user, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init
    app = GitHubApp(
        app_id="424242",
        client_id="Iv1.settings-test",
        name="Settings Test App",
        slug="settings-test-app",
        private_key_pem="test-private-key",
        permissions={"contents": "read", "issues": "write", "metadata": "read"},
    )
    db_session.add(app)
    await db_session.flush()
    installation = AppInstallation(
        app_id=app.id,
        user_id=test_user.id,
        account_login=owner,
        account_type="User",
        repositories=[f"{owner}/{repo_name}"],
        permissions={"contents": "read", "issues": "write", "metadata": "read"},
    )
    db_session.add(installation)
    await db_session.commit()
    client.cookies.set("ui_session", _sign_session(test_user.login))

    page = await client.get(f"/ui/{owner}/{repo_name}/settings/installations")

    assert page.status_code == 200
    assert "Installed GitHub Apps" in page.text
    assert "Settings Test App" in page.text
    assert "settings-test-app" in page.text
    assert "contents: read" in page.text
    assert "issues: write" in page.text
    assert "metadata: read" in page.text
    assert 'aria-label="Repository settings"' in page.text


@pytest.mark.asyncio
async def test_github_apps_settings_exclude_other_repository_installations(
    client, db_session, test_user, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init
    app = GitHubApp(
        app_id="434343",
        client_id="Iv1.other-repo-test",
        name="Other Repository App",
        slug="other-repository-app",
        private_key_pem="test-private-key",
        permissions={"issues": "write"},
    )
    db_session.add(app)
    await db_session.flush()
    db_session.add(
        AppInstallation(
            app_id=app.id,
            user_id=test_user.id,
            account_login=owner,
            account_type="User",
            repositories=[f"{owner}/some-other-repository"],
            permissions={"issues": "write"},
        )
    )
    await db_session.commit()
    client.cookies.set("ui_session", _sign_session(test_user.login))

    page = await client.get(f"/ui/{owner}/{repo_name}/settings/installations")

    assert page.status_code == 200
    assert "No GitHub Apps installed" in page.text
    assert "Other Repository App" not in page.text


@pytest.mark.asyncio
async def test_github_apps_settings_require_admin_access(
    client, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init

    response = await client.get(
        f"/ui/{owner}/{repo_name}/settings/installations"
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_branch_settings_create_and_update_protection(
    client, db_session, test_user, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init
    client.cookies.set("ui_session", _sign_session(test_user.login))

    response = await client.post(
        f"/ui/{owner}/{repo_name}/settings/branches/protection",
        data={
            "branch_name": "main",
            "protection_enabled": "true",
            "require_reviews": "true",
            "required_approving_review_count": "2",
            "dismiss_stale_reviews": "true",
            "require_last_push_approval": "true",
            "require_status_checks": "true",
            "status_contexts": "ci/test, lint\nbuild",
            "strict_status_checks": "true",
            "enforce_admins": "true",
            "required_linear_history": "true",
        },
    )

    assert response.status_code == 302
    assert response.headers["location"].endswith(
        "/settings/branches?branch=main&saved=updated"
    )
    branch = (
        await db_session.execute(select(Branch).where(Branch.name == "main"))
    ).scalar_one()
    await db_session.refresh(branch)
    protection = (
        await db_session.execute(
            select(BranchProtection).where(BranchProtection.branch_id == branch.id)
        )
    ).scalar_one()
    assert branch.protected is True
    assert protection.required_status_checks == {
        "strict": True,
        "contexts": ["ci/test", "lint", "build"],
        "checks": [],
    }
    assert protection.required_pull_request_reviews[
        "required_approving_review_count"
    ] == 2
    assert protection.required_pull_request_reviews["dismiss_stale_reviews"] is True
    assert protection.enforce_admins is True
    assert protection.required_linear_history is True


@pytest.mark.asyncio
async def test_branch_settings_remove_protection(
    client, db_session, test_user, test_repo_with_init
):
    owner, repo_name, _repo_data = test_repo_with_init
    client.cookies.set("ui_session", _sign_session(test_user.login))
    endpoint = f"/ui/{owner}/{repo_name}/settings/branches/protection"
    enabled = await client.post(
        endpoint,
        data={"branch_name": "main", "protection_enabled": "true"},
    )
    assert enabled.status_code == 302

    removed = await client.post(endpoint, data={"branch_name": "main"})

    assert removed.status_code == 302
    assert removed.headers["location"].endswith(
        "/settings/branches?branch=main&saved=removed"
    )
    branch = (
        await db_session.execute(select(Branch).where(Branch.name == "main"))
    ).scalar_one()
    await db_session.refresh(branch)
    protection = (
        await db_session.execute(
            select(BranchProtection).where(BranchProtection.branch_id == branch.id)
        )
    ).scalar_one_or_none()
    assert branch.protected is False
    assert protection is None

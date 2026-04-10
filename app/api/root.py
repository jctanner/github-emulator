"""Root API endpoints -- mirrors GitHub's ``GET /`` discovery document."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.config import settings

router = APIRouter(tags=["meta"])

BASE = settings.BASE_URL


def _api_urls() -> dict:
    """Return a mapping of resource names to URL templates."""
    api = f"{BASE}/api/v3"
    return {
        "current_user_url": f"{api}/user",
        "current_user_authorizations_html_url": f"{BASE}/settings/connections/applications{{/client_id}}",
        "authorizations_url": f"{api}/authorizations",
        "code_search_url": f"{api}/search/code?q={{query}}{{&page,per_page,sort,order}}",
        "commit_search_url": f"{api}/search/commits?q={{query}}{{&page,per_page,sort,order}}",
        "emails_url": f"{api}/user/emails",
        "emojis_url": f"{api}/emojis",
        "events_url": f"{api}/events",
        "feeds_url": f"{api}/feeds",
        "followers_url": f"{api}/user/followers",
        "following_url": f"{api}/user/following{{/target}}",
        "gists_url": f"{api}/gists{{/gist_id}}",
        "hub_url": f"{api}/hub",
        "issue_search_url": f"{api}/search/issues?q={{query}}{{&page,per_page,sort,order}}",
        "issues_url": f"{api}/issues",
        "keys_url": f"{api}/user/keys",
        "label_search_url": f"{api}/search/labels?q={{query}}&repository_id={{repository_id}}{{&page,per_page}}",
        "notifications_url": f"{api}/notifications",
        "organization_url": f"{api}/orgs/{{org}}",
        "organization_repositories_url": f"{api}/orgs/{{org}}/repos{{?type,page,per_page,sort}}",
        "organization_teams_url": f"{api}/orgs/{{org}}/teams",
        "public_gists_url": f"{api}/gists/public",
        "rate_limit_url": f"{api}/rate_limit",
        "repository_url": f"{api}/repos/{{owner}}/{{repo}}",
        "repository_search_url": f"{api}/search/repositories?q={{query}}{{&page,per_page,sort,order}}",
        "current_user_repositories_url": f"{api}/user/repos{{?type,page,per_page,sort}}",
        "starred_url": f"{api}/user/starred{{/owner}}{{/repo}}",
        "starred_gists_url": f"{api}/gists/starred",
        "topic_search_url": f"{api}/search/topics?q={{query}}{{&page,per_page}}",
        "user_url": f"{api}/users/{{user}}",
        "user_organizations_url": f"{api}/user/orgs",
        "user_repositories_url": f"{api}/users/{{user}}/repos{{?type,page,per_page,sort}}",
        "user_search_url": f"{api}/search/users?q={{query}}{{&page,per_page,sort,order}}",
    }


@router.get("/")
async def api_root(request: Request):
    """Root endpoint returning URL templates for all resources.

    Browsers (Accept: text/html) are redirected to the web UI.
    API clients receive the JSON discovery document.
    """
    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        return RedirectResponse(url="/ui/", status_code=302)
    return _api_urls()


@router.get("/api/v3")
async def api_v3_root():
    """Alias for root, matching GitHub Enterprise ``/api/v3`` path."""
    return _api_urls()


@router.get("/api/v3/meta")
async def meta():
    """Server meta information."""
    return {
        "verifiable_password_authentication": True,
        "ssh_key_fingerprints": {},
        "ssh_keys": [],
        "hooks": [],
        "web": [],
        "api": [],
        "git": [],
        "packages": [],
        "pages": [],
        "importer": [],
        "actions": [],
        "dependabot": [],
    }


@router.get("/api/v3/rate_limit")
async def rate_limit():
    """Rate-limit status -- always returns unlimited for the emulator."""
    resource_template = {
        "limit": 5000,
        "used": 0,
        "remaining": 5000,
        "reset": 0,
    }
    return {
        "resources": {
            "core": dict(resource_template),
            "search": {"limit": 30, "used": 0, "remaining": 30, "reset": 0},
            "graphql": dict(resource_template),
            "integration_manifest": {"limit": 5000, "used": 0, "remaining": 5000, "reset": 0},
            "code_scanning_upload": {"limit": 500, "used": 0, "remaining": 500, "reset": 0},
        },
        "rate": dict(resource_template),
    }

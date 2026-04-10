"""Tests for miscellaneous API endpoints (emojis, gitignore, licenses, markdown, meta)."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


# ---------------------------------------------------------------------------
# GET /emojis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emojis_returns_dict(client, test_user, test_token):
    """GET /emojis returns a dictionary of emoji name to URL mappings."""
    resp = await client.get(f"{API}/emojis")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert len(data) > 0


@pytest.mark.asyncio
async def test_emojis_contains_known_keys(client, test_user, test_token):
    """GET /emojis includes well-known emoji names."""
    resp = await client.get(f"{API}/emojis")
    data = resp.json()
    for key in ["+1", "-1", "heart", "rocket", "eyes"]:
        assert key in data, f"Missing emoji: {key}"


@pytest.mark.asyncio
async def test_emojis_values_are_urls(client, test_user, test_token):
    """Each emoji value is an HTTP(S) URL."""
    resp = await client.get(f"{API}/emojis")
    data = resp.json()
    for name, url in data.items():
        assert url.startswith("http"), f"Emoji '{name}' value is not a URL: {url}"


# ---------------------------------------------------------------------------
# GET /gitignore/templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gitignore_templates_returns_list(client, test_user, test_token):
    """GET /gitignore/templates returns a sorted list of template names."""
    resp = await client.get(f"{API}/gitignore/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    # Should be sorted
    assert data == sorted(data)


@pytest.mark.asyncio
async def test_gitignore_templates_contains_known(client, test_user, test_token):
    """The template list includes common languages."""
    resp = await client.get(f"{API}/gitignore/templates")
    data = resp.json()
    assert "Python" in data
    assert "Node" in data


@pytest.mark.asyncio
async def test_gitignore_template_get_by_name(client, test_user, test_token):
    """GET /gitignore/templates/{name} returns template content."""
    resp = await client.get(f"{API}/gitignore/templates/Python")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Python"
    assert "source" in data
    assert "__pycache__" in data["source"]


@pytest.mark.asyncio
async def test_gitignore_template_not_found(client, test_user, test_token):
    """GET /gitignore/templates/{name} returns 404 for unknown template."""
    resp = await client.get(f"{API}/gitignore/templates/NonexistentLanguage")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /licenses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_licenses_returns_list(client, test_user, test_token):
    """GET /licenses returns a list of license summaries."""
    resp = await client.get(f"{API}/licenses")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0


@pytest.mark.asyncio
async def test_licenses_item_shape(client, test_user, test_token):
    """Each license in the list has the expected summary fields."""
    resp = await client.get(f"{API}/licenses")
    data = resp.json()
    license_item = data[0]
    for field in ["key", "name", "spdx_id", "url", "node_id"]:
        assert field in license_item, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_licenses_contains_mit(client, test_user, test_token):
    """The license list includes the MIT license."""
    resp = await client.get(f"{API}/licenses")
    data = resp.json()
    keys = [lic["key"] for lic in data]
    assert "mit" in keys


@pytest.mark.asyncio
async def test_get_license_by_key(client, test_user, test_token):
    """GET /licenses/{key} returns full license details."""
    resp = await client.get(f"{API}/licenses/mit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "mit"
    assert data["name"] == "MIT License"
    assert data["spdx_id"] == "MIT"
    assert "body" in data
    assert "permissions" in data
    assert "conditions" in data
    assert "limitations" in data


@pytest.mark.asyncio
async def test_get_license_not_found(client, test_user, test_token):
    """GET /licenses/{key} returns 404 for unknown license."""
    resp = await client.get(f"{API}/licenses/nonexistent-license")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /markdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_markdown_renders_text(client, test_user, test_token):
    """POST /markdown renders text and returns HTML."""
    resp = await client.post(
        f"{API}/markdown",
        json={"text": "Hello world"},
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "Hello world" in body


@pytest.mark.asyncio
async def test_markdown_renders_heading(client, test_user, test_token):
    """POST /markdown renders Markdown headings as HTML heading tags."""
    resp = await client.post(
        f"{API}/markdown",
        json={"text": "# Title\n## Subtitle\n### Section"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<h1>" in body
    assert "Title" in body
    assert "<h2>" in body
    assert "Subtitle" in body
    assert "<h3>" in body
    assert "Section" in body


@pytest.mark.asyncio
async def test_markdown_paragraph(client, test_user, test_token):
    """POST /markdown wraps plain text in paragraph tags."""
    resp = await client.post(
        f"{API}/markdown",
        json={"text": "Just a paragraph"},
    )
    assert resp.status_code == 200
    assert "<p>" in resp.text
    assert "Just a paragraph" in resp.text


@pytest.mark.asyncio
async def test_markdown_empty_text(client, test_user, test_token):
    """POST /markdown with empty text returns empty HTML."""
    resp = await client.post(
        f"{API}/markdown",
        json={"text": ""},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /meta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_meta_returns_server_info(client, test_user, test_token):
    """GET /meta returns server meta information."""
    resp = await client.get(f"{API}/meta")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "verifiable_password_authentication" in data
    assert data["verifiable_password_authentication"] is True


@pytest.mark.asyncio
async def test_meta_response_shape(client, test_user, test_token):
    """GET /meta includes all expected top-level keys."""
    resp = await client.get(f"{API}/meta")
    data = resp.json()
    expected_keys = [
        "verifiable_password_authentication",
        "ssh_key_fingerprints",
        "ssh_keys",
        "hooks",
        "web",
        "api",
        "git",
        "packages",
        "pages",
        "importer",
        "actions",
        "dependabot",
    ]
    for key in expected_keys:
        assert key in data, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# GET / (root) and /api/v3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_root_returns_url_templates(client, test_user, test_token):
    """GET / returns the API discovery document with URL templates."""
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "current_user_url" in data
    assert "repository_url" in data
    assert "user_url" in data


@pytest.mark.asyncio
async def test_api_v3_root(client, test_user, test_token):
    """GET /api/v3 returns the same discovery document as GET /."""
    resp = await client.get(f"{API}")
    assert resp.status_code == 200
    data = resp.json()
    assert "current_user_url" in data


# ---------------------------------------------------------------------------
# GET /rate_limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit(client, test_user, test_token):
    """GET /rate_limit returns rate limit information."""
    resp = await client.get(f"{API}/rate_limit")
    assert resp.status_code == 200
    data = resp.json()
    assert "resources" in data
    assert "rate" in data
    assert "core" in data["resources"]
    assert "search" in data["resources"]
    core = data["resources"]["core"]
    assert core["limit"] == 5000
    assert core["remaining"] == 5000

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.frontend import FrontendStaticFiles


@pytest.mark.asyncio
async def test_frontend_static_host_serves_assets_and_history_fallback(tmp_path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<main>frontend shell</main>", encoding="utf-8")
    (assets / "app.js").write_text("window.loaded = true;", encoding="utf-8")

    app = FastAPI()
    app.mount("/ui", FrontendStaticFiles(dist))
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        deep_link = await client.get("/ui/owner/repo/issues/1")
        filename_deep_link = await client.get(
            "/ui/owner/repo/blob/main/README.md"
        )
        asset = await client.get("/ui/assets/app.js")
        missing_asset = await client.get("/ui/assets/missing.js")

    assert deep_link.status_code == 200
    assert "frontend shell" in deep_link.text
    assert filename_deep_link.status_code == 200
    assert "frontend shell" in filename_deep_link.text
    assert asset.status_code == 200
    assert missing_asset.status_code == 404


@pytest.mark.asyncio
async def test_frontend_shell_revalidates_and_hashed_assets_cache(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<main>frontend shell</main>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("1;", encoding="utf-8")

    app = FastAPI()
    app.mount("/ui", FrontendStaticFiles(dist))
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        root = await client.get("/ui/")
        deep_link = await client.get("/ui/owner/repo/issues/1")
        asset = await client.get("/ui/assets/index-abc123.js")
        revalidated = await client.get(
            "/ui/owner/repo/issues/1",
            headers={"If-None-Match": deep_link.headers["etag"]},
        )

    # A deploy reaches open browsers on their next page load.
    assert root.headers["cache-control"] == "no-cache"
    assert deep_link.headers["cache-control"] == "no-cache"
    assert revalidated.status_code == 304
    assert revalidated.headers["cache-control"] == "no-cache"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"

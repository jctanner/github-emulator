"""Markdown endpoint -- render markdown to HTML."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["markdown"])


@router.post("/markdown")
async def render_markdown(body: dict):
    """Render Markdown text to HTML.

    For the emulator we wrap the text in a ``<div>`` with basic
    formatting. No full Markdown parser is used.
    """
    text = body.get("text", "")
    mode = body.get("mode", "markdown")

    # Very basic rendering: wrap lines in <p> tags
    lines = text.split("\n")
    html_parts = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            html_parts.append(f"<h1>{stripped[2:]}</h1>")
        elif stripped.startswith("## "):
            html_parts.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith("### "):
            html_parts.append(f"<h3>{stripped[4:]}</h3>")
        elif stripped == "":
            continue
        else:
            html_parts.append(f"<p>{stripped}</p>")

    html = "\n".join(html_parts)
    return HTMLResponse(content=html, media_type="text/html")


@router.post("/markdown/raw")
async def render_markdown_raw(body: bytes):
    """Render raw Markdown text to HTML."""
    text = body.decode("utf-8", errors="replace")
    return HTMLResponse(content=f"<p>{text}</p>", media_type="text/html")

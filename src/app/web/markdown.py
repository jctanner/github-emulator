"""Small Markdown renderer for web UI comment bodies.

This intentionally covers the GitHub-style constructs the UI needs most for
PR and issue text while escaping all source text before emitting HTML.
"""

from __future__ import annotations

import html
import re


_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def render_markdown(text: str | None) -> str:
    """Render a safe subset of Markdown to HTML."""
    if not text:
        return ""

    lines = text.splitlines()
    blocks: list[str] = []
    paragraph: list[str] = []
    index = 0

    def flush_paragraph() -> None:
        if paragraph:
            content = " ".join(line.strip() for line in paragraph)
            blocks.append(f"<p>{_render_inline(content)}</p>")
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            index += 1
            continue

        if (
            _looks_like_table_row(stripped)
            and index + 1 < len(lines)
            and _TABLE_SEPARATOR_RE.match(lines[index + 1].strip())
        ):
            flush_paragraph()
            table_lines = [stripped]
            index += 2
            while index < len(lines) and _looks_like_table_row(lines[index].strip()):
                table_lines.append(lines[index].strip())
                index += 1
            blocks.append(_render_table(table_lines))
            continue

        heading_level = _heading_level(stripped)
        if heading_level:
            flush_paragraph()
            content = stripped[heading_level + 1 :].strip()
            blocks.append(f"<h{heading_level}>{_render_inline(content)}</h{heading_level}>")
            index += 1
            continue

        task = _parse_task_item(stripped)
        if task:
            flush_paragraph()
            items = []
            while index < len(lines):
                task = _parse_task_item(lines[index].strip())
                if not task:
                    break
                checked, content = task
                checked_attr = " checked" if checked else ""
                items.append(
                    "<li class=\"task-list-item\">"
                    f"<input type=\"checkbox\" disabled{checked_attr}> "
                    f"{_render_inline(content)}</li>"
                )
                index += 1
            blocks.append(f"<ul class=\"contains-task-list\">{''.join(items)}</ul>")
            continue

        paragraph.append(line)
        index += 1

    flush_paragraph()
    return "\n".join(blocks)


def _heading_level(stripped: str) -> int | None:
    marker = stripped.split(" ", 1)[0]
    if 1 <= len(marker) <= 6 and set(marker) == {"#"} and stripped.startswith(f"{marker} "):
        return len(marker)
    return None


def _parse_task_item(stripped: str) -> tuple[bool, str] | None:
    match = re.match(r"^[-*]\s+\[([ xX])\]\s+(.+)$", stripped)
    if not match:
        return None
    return match.group(1).lower() == "x", match.group(2)


def _looks_like_table_row(stripped: str) -> bool:
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _render_table(rows: list[str]) -> str:
    header = _split_table_row(rows[0])
    body = [_split_table_row(row) for row in rows[1:]]
    thead = "".join(f"<th>{_render_inline(cell)}</th>" for cell in header)
    body_rows = []
    for row in body:
        cells = "".join(f"<td>{_render_inline(cell)}</td>" for cell in row)
        body_rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _split_table_row(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _render_inline(text: str) -> str:
    parts = re.split(r"(`[^`]+`)", text)
    rendered = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) >= 2:
            rendered.append(f"<code>{html.escape(part[1:-1])}</code>")
        else:
            escaped = html.escape(part)
            escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
            rendered.append(escaped)
    return "".join(rendered)

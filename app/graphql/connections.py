"""Relay-style pagination types and helpers for GraphQL connections."""

import base64
from typing import Generic, Optional, Sequence, TypeVar

import strawberry


CURSOR_PREFIX = "cursor:"

T = TypeVar("T")


def encode_cursor(offset: int) -> str:
    """Encode an offset into a base64 cursor string."""
    raw = f"{CURSOR_PREFIX}{offset}"
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


def decode_cursor(cursor: str) -> int:
    """Decode a base64 cursor string back into an offset."""
    try:
        raw = base64.b64decode(cursor.encode("utf-8")).decode("utf-8")
        if not raw.startswith(CURSOR_PREFIX):
            raise ValueError(f"Invalid cursor format: {cursor}")
        return int(raw[len(CURSOR_PREFIX):])
    except Exception:
        raise ValueError(f"Invalid cursor: {cursor}")


@strawberry.type
class PageInfo:
    """Information about pagination in a connection."""
    has_next_page: bool
    has_previous_page: bool
    start_cursor: Optional[str]
    end_cursor: Optional[str]


@strawberry.type
class Edge(Generic[T]):
    """An edge in a connection, wrapping a node with its cursor."""
    node: T
    cursor: str


@strawberry.type
class Connection(Generic[T]):
    """A Relay-style connection wrapping a paginated list of nodes."""
    edges: list[Edge[T]]
    nodes: list[T]
    page_info: PageInfo
    total_count: int


def build_connection(
    items: Sequence,
    convert_fn,
    total_count: int,
    first: Optional[int] = None,
    after: Optional[str] = None,
    last: Optional[int] = None,
    before: Optional[str] = None,
) -> Connection:
    """
    Build a Relay-style connection from a list of items.

    Args:
        items: The full list of items (or the already-sliced list if total_count
               is provided separately).
        convert_fn: A callable that converts a raw item (e.g., a SQLAlchemy model
                    instance) into the corresponding Strawberry type.
        total_count: The total number of items available (before pagination).
        first: Return the first N items after the cursor.
        after: Cursor pointing to the item after which to start.
        last: Return the last N items before the cursor.
        before: Cursor pointing to the item before which to end.

    Returns:
        A Connection object with edges, nodes, page_info, and total_count.
    """
    all_items = list(items)
    length = len(all_items)

    # Determine start and end indices based on cursors
    start_index = 0
    end_index = length

    if after is not None:
        after_offset = decode_cursor(after)
        start_index = after_offset + 1

    if before is not None:
        before_offset = decode_cursor(before)
        end_index = min(end_index, before_offset)

    # Apply first/last limits
    if first is not None:
        end_index = min(end_index, start_index + first)

    if last is not None:
        start_index = max(start_index, end_index - last)

    # Clamp to valid range
    start_index = max(0, start_index)
    end_index = min(length, end_index)

    sliced = all_items[start_index:end_index]

    # Build edges and nodes
    edges = []
    nodes = []
    for i, item in enumerate(sliced):
        absolute_index = start_index + i
        converted = convert_fn(item)
        edges.append(Edge(node=converted, cursor=encode_cursor(absolute_index)))
        nodes.append(converted)

    # Build page info
    has_previous_page = start_index > 0
    has_next_page = end_index < length

    start_cursor = edges[0].cursor if edges else None
    end_cursor = edges[-1].cursor if edges else None

    page_info = PageInfo(
        has_next_page=has_next_page,
        has_previous_page=has_previous_page,
        start_cursor=start_cursor,
        end_cursor=end_cursor,
    )

    return Connection(
        edges=edges,
        nodes=nodes,
        page_info=page_info,
        total_count=total_count,
    )

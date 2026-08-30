"""Pydantic schemas for GitHub Label API responses."""

import base64
from typing import Optional

from pydantic import BaseModel, ConfigDict


def _make_node_id(type_name: str, id_value: int) -> str:
    """Generate a node_id as base64('Type:id')."""
    raw = f"{type_name}:{id_value}"
    return base64.b64encode(raw.encode()).decode()


class LabelCreate(BaseModel):
    """Schema for creating a label."""

    name: str
    color: str = "ededed"
    description: Optional[str] = None


class LabelUpdate(BaseModel):
    """Schema for updating a label."""

    new_name: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None


class LabelResponse(BaseModel):
    """GitHub-compatible label JSON response."""

    id: int
    node_id: str
    url: str
    name: str
    description: Optional[str] = None
    color: str
    default: bool = False

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_db(cls, label, base_url: str, owner_login: str, repo_name: str) -> "LabelResponse":
        """Construct a LabelResponse from a DB label object."""
        api_base = f"{base_url}/api/v3"
        return cls(
            id=label.id,
            node_id=_make_node_id("Label", label.id),
            url=f"{api_base}/repos/{owner_login}/{repo_name}/labels/{label.name}",
            name=label.name,
            description=label.description,
            color=label.color,
            default=getattr(label, "is_default", False),
        )

"""Explicit ORM loading policies for latency-sensitive request paths."""

from sqlalchemy.orm import joinedload, raiseload

from app.models.repository import Repository


def scalar_only_options():
    """Prevent an entity query from implicitly traversing relationships."""
    return (raiseload("*"),)


def repository_identity_options():
    """Load only repository namespace identity needed by API serializers."""
    return (
        raiseload("*"),
        joinedload(Repository.owner).raiseload("*"),
        joinedload(Repository.organization).raiseload("*"),
    )

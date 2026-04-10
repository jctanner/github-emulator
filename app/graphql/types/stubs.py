"""Lightweight stub types for fields the gh CLI queries but we don't fully implement.

These types allow the GraphQL schema to accept and resolve queries from the gh CLI
without erroring on missing fields, while returning sensible empty/default values.
"""

from typing import Annotated, Optional, Union

import strawberry

from app.graphql.connections import Connection, PageInfo, Edge


@strawberry.type
class LicenseInfo:
    """A repository's license."""
    key: str = ""
    name: str = ""
    nickname: Optional[str] = None
    spdx_id: Optional[str] = None
    url: Optional[str] = None


@strawberry.type
class RepositoryTopic:
    """A repository-topic pair."""
    topic_name: str = ""
    url: str = ""


@strawberry.type
class FundingLink:
    """A funding link for a repository."""
    platform: str = ""
    url: str = ""


@strawberry.type
class CodeOfConduct:
    """A code of conduct for a repository."""
    key: str = ""
    name: str = ""
    url: Optional[str] = None
    body: Optional[str] = None


@strawberry.type
class ContactLink:
    """A contact link for a repository."""
    about: str = ""
    name: str = ""
    url: str = ""


@strawberry.type
class IssueTemplate:
    """An issue template."""
    name: str = ""
    title: str = ""
    about: str = ""
    body: Optional[str] = None


@strawberry.type
class PullRequestTemplate:
    """A pull request template."""
    body: Optional[str] = None
    filename: Optional[str] = None


@strawberry.type
class ReleaseStub:
    """A minimal release type."""
    name: Optional[str] = None
    tag_name: str = ""
    is_draft: bool = False
    is_prerelease: bool = False
    published_at: Optional[str] = None


@strawberry.type
class ProjectV2ItemFieldSingleSelectValue:
    """A single select field value on a ProjectV2 item."""
    name: Optional[str] = None
    option_id: Optional[str] = None


@strawberry.type
class ProjectV2FieldValue:
    """A field value on a ProjectV2 item."""
    name: Optional[str] = None


@strawberry.type
class ProjectV2:
    """A minimal ProjectV2 stub."""
    title: str = ""
    number: int = 0

    @strawberry.field
    def id(self) -> strawberry.ID:
        return strawberry.ID("")


@strawberry.type
class ProjectV2Stub:
    """A minimal ProjectV2 item stub."""
    title: str = ""
    number: int = 0

    @strawberry.field
    def id(self) -> strawberry.ID:
        return strawberry.ID("")

    @strawberry.field
    def project(self) -> ProjectV2:
        return ProjectV2()

    @strawberry.field
    def field_value_by_name(
        self, name: str
    ) -> Optional[Annotated[
        Union[ProjectV2ItemFieldSingleSelectValue, ProjectV2FieldValue],
        strawberry.union("ProjectV2ItemFieldValue"),
    ]]:
        return None


@strawberry.type
class ProjectCardStub:
    """A minimal project card stub."""
    note: Optional[str] = None


@strawberry.type
class StatusCheckRollup:
    """A status check rollup stub."""
    state: str = "SUCCESS"


@strawberry.type
class AutoMergeRequest:
    """An auto merge request stub."""
    enabled_at: Optional[str] = None
    merge_method: str = "MERGE"
    commit_headline: Optional[str] = None
    commit_body: Optional[str] = None


@strawberry.type
class ReviewRequestStub:
    """A review request stub."""
    requested_reviewer: Optional[str] = None


@strawberry.type
class ReactingUserConnection:
    """A connection of users who reacted."""
    total_count: int = 0


@strawberry.type
class ReactionGroup:
    """A group of reactions with the same content."""
    content: str
    total_count: int = 0

    @strawberry.field
    def users(self) -> ReactingUserConnection:
        return ReactingUserConnection(total_count=self.total_count)


@strawberry.type
class PullRequestCommitStub:
    """A minimal pull request commit stub."""
    oid: str = ""
    message: str = ""


# The standard 8 GitHub reaction types
STANDARD_REACTION_GROUPS = [
    ReactionGroup(content="THUMBS_UP", total_count=0),
    ReactionGroup(content="THUMBS_DOWN", total_count=0),
    ReactionGroup(content="LAUGH", total_count=0),
    ReactionGroup(content="HOORAY", total_count=0),
    ReactionGroup(content="CONFUSED", total_count=0),
    ReactionGroup(content="HEART", total_count=0),
    ReactionGroup(content="ROCKET", total_count=0),
    ReactionGroup(content="EYES", total_count=0),
]


def empty_connection(type_class=None) -> Connection:
    """Return an empty Relay-style connection."""
    return Connection(
        edges=[],
        nodes=[],
        page_info=PageInfo(
            has_next_page=False,
            has_previous_page=False,
            start_cursor=None,
            end_cursor=None,
        ),
        total_count=0,
    )

"""GraphQL enum types expected by the gh CLI."""

from enum import Enum
from typing import Optional

import strawberry


@strawberry.enum
class RepositoryPrivacy(Enum):
    """The privacy of a repository."""
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"


@strawberry.enum
class RepositoryVisibility(Enum):
    """The visibility of a repository."""
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"
    INTERNAL = "INTERNAL"


@strawberry.enum
class PullRequestMergeMethod(Enum):
    """Represents available merge methods."""
    MERGE = "MERGE"
    REBASE = "REBASE"
    SQUASH = "SQUASH"


@strawberry.enum
class IssueState(Enum):
    """The possible states of an issue or pull request."""
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    MERGED = "MERGED"


@strawberry.enum
class IssueStateReason(Enum):
    """The reason an issue was closed or reopened."""
    COMPLETED = "COMPLETED"
    NOT_PLANNED = "NOT_PLANNED"
    REOPENED = "REOPENED"
    DUPLICATE = "DUPLICATE"


@strawberry.enum
class RepositoryOrderField(Enum):
    """Properties by which repository connections can be ordered."""
    CREATED_AT = "CREATED_AT"
    UPDATED_AT = "UPDATED_AT"
    PUSHED_AT = "PUSHED_AT"
    NAME = "NAME"
    STARGAZERS = "STARGAZERS"


@strawberry.enum
class OrderDirection(Enum):
    """Possible directions in which to order a list of items."""
    ASC = "ASC"
    DESC = "DESC"


@strawberry.enum
class RepositoryAffiliation(Enum):
    """The affiliation of a user to a repository."""
    OWNER = "OWNER"
    COLLABORATOR = "COLLABORATOR"
    ORGANIZATION_MEMBER = "ORGANIZATION_MEMBER"


@strawberry.enum
class SubscriptionState(Enum):
    """The possible states of a subscription."""
    UNSUBSCRIBED = "UNSUBSCRIBED"
    SUBSCRIBED = "SUBSCRIBED"
    IGNORED = "IGNORED"


@strawberry.enum
class MergeStateStatus(Enum):
    """Detailed status of a pull request merge state."""
    BEHIND = "BEHIND"
    BLOCKED = "BLOCKED"
    CLEAN = "CLEAN"
    DIRTY = "DIRTY"
    DRAFT = "DRAFT"
    HAS_HOOKS = "HAS_HOOKS"
    UNKNOWN = "UNKNOWN"
    UNSTABLE = "UNSTABLE"


@strawberry.enum
class PullRequestReviewDecision(Enum):
    """The review decision on a pull request."""
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@strawberry.enum
class CommentAuthorAssociation(Enum):
    """The association of a comment author with a repository."""
    COLLABORATOR = "COLLABORATOR"
    CONTRIBUTOR = "CONTRIBUTOR"
    FIRST_TIMER = "FIRST_TIMER"
    FIRST_TIME_CONTRIBUTOR = "FIRST_TIME_CONTRIBUTOR"
    MANNEQUIN = "MANNEQUIN"
    MEMBER = "MEMBER"
    NONE = "NONE"
    OWNER = "OWNER"


@strawberry.input
class RepositoryOrder:
    """Ordering options for repository connections."""
    field: RepositoryOrderField
    direction: OrderDirection


@strawberry.enum
class IssueOrderField(Enum):
    """Properties by which issue connections can be ordered."""
    CREATED_AT = "CREATED_AT"
    UPDATED_AT = "UPDATED_AT"
    COMMENTS = "COMMENTS"


@strawberry.input
class IssueOrder:
    """Ordering options for issue connections."""
    field: IssueOrderField
    direction: OrderDirection


@strawberry.input
class IssueFilters:
    """Filtering options for issue connections."""
    assignee: Optional[str] = None
    created_by: Optional[str] = None
    labels: Optional[list[str]] = None
    mentioned: Optional[str] = None
    milestone: Optional[str] = None
    milestone_number: Optional[str] = None
    since: Optional[str] = None
    states: Optional[list[str]] = None
    viewer_subscribed: Optional[bool] = None


@strawberry.enum
class LabelOrderField(Enum):
    """Properties by which label connections can be ordered."""
    CREATED_AT = "CREATED_AT"
    NAME = "NAME"


@strawberry.input
class LabelOrder:
    """Ordering options for label connections."""
    field: LabelOrderField
    direction: OrderDirection

"""Root GraphQL schema combining queries and mutations.

Usage with FastAPI:

    from strawberry.fastapi import GraphQLRouter
    from app.graphql.schema import schema

    graphql_router = GraphQLRouter(
        schema,
        context_getter=get_context,
    )
    app.include_router(graphql_router, prefix="/graphql")

The context_getter should provide:
    - "db": an async SQLAlchemy session
    - "user": the authenticated User model instance (or None)
"""

from datetime import datetime
from typing import NewType

import strawberry
from strawberry.schema.config import StrawberryConfig

from app.graphql.queries import Query
from app.graphql.mutations import Mutation
from app.graphql.types.stubs import ProjectV2ItemFieldSingleSelectValue
from app.graphql.types.enums import (
    RepositoryPrivacy,
    RepositoryVisibility,
    PullRequestMergeMethod,
    IssueState,
    IssueStateReason,
    RepositoryOrderField,
    OrderDirection,
    RepositoryAffiliation,
    SubscriptionState,
    MergeStateStatus,
    PullRequestReviewDecision,
    CommentAuthorAssociation,
    RepositoryOrder,
    IssueOrderField,
    IssueOrder,
    IssueFilters,
    LabelOrderField,
    LabelOrder,
)


# Custom datetime scalar that always includes "Z" suffix (RFC 3339)
GitHubDateTime = strawberry.scalar(
    NewType("DateTime", datetime),
    serialize=lambda v: v.strftime("%Y-%m-%dT%H:%M:%SZ") if v else None,
    parse_value=lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None,
)


schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    types=[
        RepositoryPrivacy,
        RepositoryVisibility,
        PullRequestMergeMethod,
        IssueStateReason,
        RepositoryOrderField,
        OrderDirection,
        RepositoryAffiliation,
        SubscriptionState,
        MergeStateStatus,
        PullRequestReviewDecision,
        CommentAuthorAssociation,
        RepositoryOrder,
        IssueState,
        IssueOrderField,
        IssueOrder,
        IssueFilters,
        LabelOrderField,
        LabelOrder,
        ProjectV2ItemFieldSingleSelectValue,
    ],
    config=StrawberryConfig(
        scalar_map={datetime: GitHubDateTime},
    ),
)

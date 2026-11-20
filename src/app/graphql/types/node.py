"""GitHub's Node interface: an object with a global ID."""

import strawberry


@strawberry.interface(name="Node")
class Node:
    """An object with a global ``id``, which ``Query.node`` resolves.

    Each implementing type supplies its own ``id`` resolver.
    """

    @strawberry.field
    def id(self) -> strawberry.ID:
        raise NotImplementedError

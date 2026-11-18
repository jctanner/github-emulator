"""One id space for users and organizations.

On GitHub a user and an organization are both accounts and never share an
id: a repository's ``owner.id`` (and the OIDC ``repository_owner_id``) names
exactly one of them. The users and organizations tables each had their own
autoincrement id, so the admin user and the first organization were both 1,
and a relying party that pinned an owner by id matched either.

Every user and organization id is now taken from ``account_ids``: inserting
a row there allocates the next id, and the user or organization is inserted
with it. The table is AUTOINCREMENT like every other (app/models/__init__.py),
so an id is never handed out twice, across both tables. A row created with an
explicit id records that id here too, so it cannot be handed out again and an
id already used by the other table fails the insert instead of colliding.
Migration 0018 moves existing organizations off ids a user holds and seeds the
table.
"""

from sqlalchemy import Integer, event, insert
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization import Organization
from app.models.user import User


class AccountId(Base):
    __tablename__ = "account_ids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)


def allocate_account_id(connection, explicit_id: int | None = None) -> int:
    """Reserve an account id on ``connection``: the next one, or ``explicit_id``."""
    table = AccountId.__table__
    if explicit_id is not None:
        connection.execute(insert(table).values(id=explicit_id))
        return explicit_id
    return connection.execute(insert(table)).inserted_primary_key[0]


@event.listens_for(User, "before_insert")
@event.listens_for(Organization, "before_insert")
def _assign_account_id(mapper, connection, target) -> None:
    target.id = allocate_account_id(connection, target.id)

"""Organization creation shared by the REST and admin APIs."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization, OrgMembership
from app.models.user import User


async def find_user(db: AsyncSession, login: str) -> User | None:
    return (await db.execute(select(User).where(User.login == login))).scalar_one_or_none()


async def create_organization(
    db: AsyncSession, login: str, owner: User, **fields
) -> Organization:
    """Create an organization with ``owner`` as its first active admin.

    Every organization starts with an owner, as on GitHub: without one nobody
    could create repositories in it or add members to it.
    """
    org = Organization(login=login, **fields)
    db.add(org)
    await db.flush()
    db.add(OrgMembership(org_id=org.id, user_id=owner.id, role="admin", state="active"))
    await db.commit()
    await db.refresh(org)
    return org

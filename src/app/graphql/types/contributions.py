"""A user's contributions collection: GitHub's contribution calendar.

GitHub's ``user.contributionsCollection`` answers "what did this account do,
day by day" for a window of at most a year. Org Pulse's team-tracker asks
for exactly that (``contributionCalendar { totalContributions weeks {
contributionDays { date contributionCount } } }``) to build per-person
activity, and the gh CLI never asks for it, which is why the emulator had
nothing here.

What counts follows GitHub's published rules, on the data the emulator has:

- commits: rows the push indexer keeps in ``commit_metadata`` whose author
  email is the user's (case-insensitively) or whose author name is the
  user's login, de-duplicated by sha so a fork's copy is not a second
  contribution;
- issues opened: issue rows the user created that are not pull requests;
- pull requests opened: pull-request rows whose issue the user created;
- reviews: submitted review rows by the user.

Days are bucketed in UTC. The calendar begins on the Sunday on or before
``from`` and ends on ``to``, so the first week may be partial, as GitHub's
is; ``weekday`` is 0 for Sunday. The window defaults to the year ending now
and may not exceed a year, GitHub's limit.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Optional

import strawberry
from sqlalchemy import func, select
from strawberry.types import Info

MAX_WINDOW = timedelta(days=366)


@strawberry.type
class ContributionCalendarDay:
    """One day of the calendar."""
    date: date
    contribution_count: int
    weekday: int
    color: str = "#ebedf0"


@strawberry.type
class ContributionCalendarWeek:
    """Seven days, or fewer at the edges of the window."""
    contribution_days: list[ContributionCalendarDay]
    first_day: date


@strawberry.type
class ContributionCalendar:
    """The calendar of a user's contributions over the window."""
    total_contributions: int
    weeks: list[ContributionCalendarWeek]
    colors: list[str] = strawberry.field(
        default_factory=lambda: ["#9be9a8", "#40c463", "#30a14e", "#216e39"]
    )
    is_halloween: bool = False


@strawberry.type
class ContributionsCollection:
    """A user's contributions between ``startedAt`` and ``endedAt``."""
    started_at: datetime
    ended_at: datetime
    contribution_calendar: ContributionCalendar
    total_commit_contributions: int
    total_issue_contributions: int
    total_pull_request_contributions: int
    total_pull_request_review_contributions: int
    has_any_contributions: bool
    restricted_contributions_count: int = 0


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _day_of(value) -> Optional[date]:
    """The UTC calendar day of a stored timestamp: a datetime column, or the
    ISO 8601 string the commit indexer keeps from ``git log --format=%aI``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value).date()
    try:
        return _utc(datetime.fromisoformat(str(value))).date()
    except ValueError:
        return None


def resolve_window(from_: Optional[datetime], to: Optional[datetime]) -> tuple[datetime, datetime]:
    """GitHub's defaults and limit: the year ending now, at most a year."""
    ended_at = _utc(to) if to is not None else datetime.now(timezone.utc)
    started_at = _utc(from_) if from_ is not None else ended_at - timedelta(days=365)
    if started_at > ended_at:
        raise ValueError("'from' must be before 'to'")
    if ended_at - started_at > MAX_WINDOW:
        raise ValueError("The total time spanned by 'from' and 'to' must not exceed 1 year")
    return started_at, ended_at


async def contributions_for(db, user, from_: Optional[datetime], to: Optional[datetime]) -> ContributionsCollection:
    """Build the collection for *user* (a User model) over the window."""
    from app.models.issue import Issue
    from app.models.pull_request import PullRequest
    from app.models.review import Review
    from app.models.search_index import CommitMetadata

    started_at, ended_at = resolve_window(from_, to)
    first_day = started_at.date()
    first_day -= timedelta(days=(first_day.weekday() + 1) % 7)  # back to Sunday
    last_day = ended_at.date()

    def in_window(day: Optional[date]) -> bool:
        return day is not None and first_day <= day <= last_day

    counts: dict[date, int] = {}

    def add(day: Optional[date]) -> bool:
        if not in_window(day):
            return False
        counts[day] = counts.get(day, 0) + 1
        return True

    # Commits: by author email, or by author name equal to the login.
    commit_filter = CommitMetadata.author_name == user.login
    if user.email:
        commit_filter = commit_filter | (func.lower(CommitMetadata.author_email) == user.email.lower())
    rows = await db.execute(
        select(CommitMetadata.commit_sha, CommitMetadata.author_date).where(commit_filter)
    )
    seen: set[str] = set()
    commits = 0
    for sha, author_date in rows.all():
        if sha in seen:
            continue
        seen.add(sha)
        if add(_day_of(author_date)):
            commits += 1

    # Issues and pull requests the user opened. A pull request is an issue
    # row with a pull_requests row; it counts once, as a pull request.
    pr_issue_ids = select(PullRequest.issue_id)
    rows = await db.execute(
        select(Issue.created_at, Issue.id.in_(pr_issue_ids)).where(Issue.user_id == user.id)
    )
    issues = pulls = 0
    for created_at, is_pull in rows.all():
        if add(_day_of(created_at)):
            if is_pull:
                pulls += 1
            else:
                issues += 1

    # Submitted reviews.
    rows = await db.execute(
        select(Review.submitted_at).where(Review.user_id == user.id, Review.submitted_at.isnot(None))
    )
    reviews = 0
    for (submitted_at,) in rows.all():
        if add(_day_of(submitted_at)):
            reviews += 1

    weeks: list[ContributionCalendarWeek] = []
    day = first_day
    while day <= last_day:
        days = []
        for _ in range(7):
            if day > last_day:
                break
            count = counts.get(day, 0)
            days.append(ContributionCalendarDay(
                date=day,
                contribution_count=count,
                weekday=(day.weekday() + 1) % 7,
                color="#ebedf0" if count == 0 else "#40c463",
            ))
            day += timedelta(days=1)
        weeks.append(ContributionCalendarWeek(contribution_days=days, first_day=days[0].date))

    total = sum(counts.values())
    return ContributionsCollection(
        started_at=started_at,
        ended_at=ended_at,
        contribution_calendar=ContributionCalendar(total_contributions=total, weeks=weeks),
        total_commit_contributions=commits,
        total_issue_contributions=issues,
        total_pull_request_contributions=pulls,
        total_pull_request_review_contributions=reviews,
        has_any_contributions=total > 0,
    )


async def resolve_contributions_collection(
    info: Info,
    user_id: int,
    from_: Optional[datetime],
    to: Optional[datetime],
) -> ContributionsCollection:
    from app.models.user import User

    db = info.context["db"]
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    return await contributions_for(db, user, from_, to)


FromArgument = Annotated[Optional[datetime], strawberry.argument(name="from")]

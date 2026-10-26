"""Tell open web UI pages when the issue or pull request they show changes.

Every committed ORM change to an issue, its comments, events, labels and
assignees, or to a pull request, its reviews and review comments, is noted
as a topic: ``("issue", issue_id)`` or ``("pull", pull_request_id)``. After
the commit, each subscriber watching one of those topics is woken. Hooking
the session rather than the routes covers every writer — REST, GraphQL, the
web UI, Actions and webhooks alike — without each one having to announce
itself.

Subscribers live in this process. The emulator runs as one process, so that
is every subscriber; a second worker would need a shared channel instead.
"""

import asyncio
from collections import defaultdict

from sqlalchemy import Delete, Update, event
from sqlalchemy.orm import ORMExecuteState, Session

from app.models.comment import IssueComment, PRReviewComment
from app.models.issue import Issue, IssueAssignee, IssueLabel
from app.models.issue_event import IssueEvent
from app.models.pull_request import PullRequest
from app.models.review import Review

Topic = tuple[str, int]

_PENDING = "live_update_topics"
# Bulk statements carry no objects; their issue comes from the WHERE clause.
_BULK_TABLES = {IssueLabel.__table__, IssueAssignee.__table__}

_subscribers: dict[Topic, set["Subscription"]] = defaultdict(set)


def topics_for(obj) -> list[Topic]:
    """The topics an ORM object's change belongs to."""
    if isinstance(obj, Issue):
        return [("issue", obj.id)]
    if isinstance(obj, (IssueComment, IssueEvent, IssueLabel, IssueAssignee)):
        return [("issue", obj.issue_id)]
    if isinstance(obj, PullRequest):
        return [("issue", obj.issue_id), ("pull", obj.id)]
    if isinstance(obj, (Review, PRReviewComment)):
        return [("pull", obj.pull_request_id)]
    return []


def _note(session: Session, topics) -> None:
    pending = session.info.setdefault(_PENDING, set())
    pending.update(t for t in topics if t[1] is not None)


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, flush_context) -> None:
    for obj in (*session.new, *session.dirty, *session.deleted):
        _note(session, topics_for(obj))


@event.listens_for(Session, "do_orm_execute")
def _bulk_statement(state: ORMExecuteState) -> None:
    statement = state.statement
    if not isinstance(statement, (Delete, Update)) or statement.table not in _BULK_TABLES:
        return
    params = statement.compile().params
    _note(
        state.session,
        (("issue", value) for key, value in params.items() if key.startswith("issue_id")),
    )


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    for topic in session.info.pop(_PENDING, ()):
        for subscription in list(_subscribers.get(topic, ())):
            subscription.notify()


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(_PENDING, None)


class Subscription:
    """Wakes when any of its topics changes; use as a context manager."""

    def __init__(self, topics: list[Topic]):
        self.topics = topics
        self.changed = asyncio.Event()
        self._loop = asyncio.get_running_loop()

    def notify(self) -> None:
        # A commit can run outside the loop's thread (a sync session in a
        # worker thread), where setting an asyncio.Event is unsafe.
        self._loop.call_soon_threadsafe(self.changed.set)

    def __enter__(self) -> "Subscription":
        for topic in self.topics:
            _subscribers[topic].add(self)
        return self

    def __exit__(self, *exc) -> None:
        for topic in self.topics:
            waiters = _subscribers.get(topic)
            if waiters is not None:
                waiters.discard(self)
                if not waiters:
                    del _subscribers[topic]

    async def wait(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds; True if something changed."""
        try:
            await asyncio.wait_for(self.changed.wait(), timeout)
        except TimeoutError:
            return False
        self.changed.clear()
        return True

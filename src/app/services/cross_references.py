"""Record GitHub's cross-referenced events when an issue is mentioned.

When an issue or pull request's body, or a comment on one, mentions another
issue -- ``#12``, ``owner/repo#12`` or the issue's URL -- GitHub adds a
``cross-referenced`` event to the mentioned issue's timeline, pointing back at
the issue or pull request the mention was written in. One source adds one
event per target however often it repeats the mention, and an issue does not
reference itself. Mentions inside code are not references.

Like ``app.services.live_updates``, this hooks the SQLAlchemy ``Session``
rather than the routes, so REST, GraphQL, the web UI and Actions are all
covered. ``before_flush`` notes each new or edited body; once the flush has
assigned ids, ``after_flush_postexec`` resolves the mentions and stages the
events, which the commit's next flush writes.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.comment import IssueComment
from app.models.issue import Issue
from app.models.issue_event import IssueEvent
from app.models.repository import Repository

CROSS_REFERENCED = "cross-referenced"

_PENDING = "cross_reference_sources"

_NAME = r"[A-Za-z0-9_.-]+"
# ``#12`` or ``owner/repo#12``, not preceded by anything that would make it
# part of a word, path or URL fragment.
_SHORT = re.compile(
    rf"(?<![\w/#&-])(?:(?P<owner>{_NAME})/(?P<repo>{_NAME}))?#(?P<number>\d+)\b"
)
_URL = re.compile(
    rf"https?://(?P<host>[^/\s]+)/(?:ui/)?(?P<owner>{_NAME})/(?P<repo>{_NAME})"
    r"/(?:issues|pull)/(?P<number>\d+)\b"
)
_FENCED = re.compile(r"^(?P<fence>```|~~~).*?^(?P=fence)[ \t]*$", re.MULTILINE | re.DOTALL)
_INLINE = re.compile(r"(`+)(?:(?!\1).)+\1", re.DOTALL)

Reference = tuple[str | None, int]


def parse_references(text: str | None) -> list[Reference]:
    """Mentioned issues as ``(full_name or None for this repo, number)``."""
    prose = _INLINE.sub(" ", _FENCED.sub(" ", text or ""))
    host = urlsplit(settings.BASE_URL).netloc.lower()
    found: list[Reference] = []

    def add(reference: Reference) -> None:
        if reference not in found:
            found.append(reference)

    for match in _URL.finditer(prose):
        if match["host"].lower() == host:
            add((f"{match['owner']}/{match['repo']}", int(match["number"])))
    # URLs are matched above; blank them so their paths are not read again.
    for match in _SHORT.finditer(_URL.sub(" ", prose)):
        full_name = f"{match['owner']}/{match['repo']}" if match["owner"] else None
        add((full_name, int(match["number"])))
    return found


def _changed_body(obj) -> bool:
    state = inspect(obj)
    return state.pending or state.attrs.body.history.has_changes()


@event.listens_for(Session, "before_flush")
def _note_sources(session: Session, flush_context, instances) -> None:
    for obj in (*session.new, *session.dirty):
        if isinstance(obj, (Issue, IssueComment)) and obj.body and _changed_body(obj):
            session.info.setdefault(_PENDING, []).append(obj)


@event.listens_for(Session, "after_flush_postexec")
def _record_references(session: Session, flush_context) -> None:
    sources = session.info.pop(_PENDING, None)
    if not sources:
        return
    with session.no_autoflush:
        for obj in sources:
            if isinstance(obj, Issue):
                source_id, repo_id = obj.id, obj.repo_id
            else:
                source_id = obj.issue_id
                repo_id = session.execute(
                    select(Issue.repo_id).where(Issue.id == source_id)
                ).scalar_one()
            for target_id in _targets(session, repo_id, source_id, obj.body):
                session.add(
                    IssueEvent(
                        issue_id=target_id,
                        actor_id=obj.user_id,
                        event=CROSS_REFERENCED,
                        source_issue_id=source_id,
                    )
                )


def _targets(session: Session, repo_id: int, source_id: int, body: str) -> list[int]:
    """Ids of the issues ``body`` mentions that ``source_id`` has not yet referenced."""
    targets: list[int] = []
    for full_name, number in parse_references(body):
        target_repo_id = repo_id
        if full_name is not None:
            target_repo_id = session.execute(
                select(Repository.id).where(Repository.full_name == full_name)
            ).scalar_one_or_none()
            if target_repo_id is None:
                continue
        target_id = session.execute(
            select(Issue.id).where(Issue.repo_id == target_repo_id, Issue.number == number)
        ).scalar_one_or_none()
        if target_id is None or target_id == source_id or target_id in targets:
            continue
        # Events staged by an earlier source in this same flush are not in the
        # database yet.
        staged = any(
            isinstance(obj, IssueEvent)
            and obj.event == CROSS_REFERENCED
            and obj.issue_id == target_id
            and obj.source_issue_id == source_id
            for obj in session.new
        )
        recorded = session.execute(
            select(IssueEvent.id).where(
                IssueEvent.issue_id == target_id,
                IssueEvent.event == CROSS_REFERENCED,
                IssueEvent.source_issue_id == source_id,
            )
        ).first()
        if not staged and recorded is None:
            targets.append(target_id)
    return targets

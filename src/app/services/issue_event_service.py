from app.models.issue_event import IssueEvent


def record_label_event(db, issue, actor, event: str, label) -> IssueEvent:
    """Stage a GitHub-shaped labeled or unlabeled timeline event."""
    issue_event = IssueEvent(
        issue_id=issue.id,
        actor_id=actor.id,
        event=event,
        label={
            "id": label.id,
            "name": label.name,
            "color": label.color,
            "description": label.description,
            "default": bool(getattr(label, "is_default", False)),
        },
    )
    db.add(issue_event)
    return issue_event

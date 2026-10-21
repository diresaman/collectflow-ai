"""The only writer of TimelineEvent. Call it inside the transaction that makes the change it records."""

from typing import Any

from django.db import connection

from .models import ActorType, TimelineEvent

# An actor is a user, or ActorType.SYSTEM / ActorType.SCHEDULER for unattended work.
Actor = Any


def actor_fields(actor: Actor) -> dict[str, Any]:
    if actor in (ActorType.SYSTEM, ActorType.SCHEDULER):
        return {"actor_type": actor, "actor": None}
    if actor is None or not getattr(actor, "is_authenticated", False):
        return {"actor_type": ActorType.SYSTEM, "actor": None}
    return {"actor_type": ActorType.USER, "actor": actor}


def record(event_type: str, *, actor: Actor, invoice: Any = None, client: Any = None, **payload: Any) -> TimelineEvent:
    if client is None and invoice is not None:
        client = invoice.client
    return TimelineEvent.objects.create(
        type=event_type, invoice=invoice, client=client, payload=payload, **actor_fields(actor)
    )


def build(event_type: str, *, actor: Actor, invoice: Any = None, client: Any = None, **payload: Any) -> TimelineEvent:
    """An unsaved event, for bulk_create."""
    if client is None and invoice is not None:
        client = invoice.client
    return TimelineEvent(type=event_type, invoice=invoice, client=client, payload=payload, **actor_fields(actor))


def bulk_record(events: list[TimelineEvent]) -> None:
    TimelineEvent.objects.bulk_create(events)


# Append-only enforcement --------------------------------------------------------

TABLE = "timeline_timelineevent"
TRIGGER_SQL = [
    f"""
    CREATE TRIGGER IF NOT EXISTS timeline_event_no_update BEFORE UPDATE ON {TABLE}
    BEGIN SELECT RAISE(ABORT, 'timeline events are append-only'); END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS timeline_event_no_delete BEFORE DELETE ON {TABLE}
    BEGIN SELECT RAISE(ABORT, 'timeline events are append-only'); END
    """,
]
DROP_TRIGGER_SQL = [
    "DROP TRIGGER IF EXISTS timeline_event_no_update",
    "DROP TRIGGER IF EXISTS timeline_event_no_delete",
]


def install_triggers() -> None:
    with connection.cursor() as cursor:
        for statement in TRIGGER_SQL:
            cursor.execute(statement)


def remove_triggers() -> None:
    with connection.cursor() as cursor:
        for statement in DROP_TRIGGER_SQL:
            cursor.execute(statement)

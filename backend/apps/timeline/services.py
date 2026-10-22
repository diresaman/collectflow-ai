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
# PostgreSQL is the application database. The SQLite statements remain because SQLite files
# created before the PostgreSQL move (and the snapshots made from them) carry these triggers.

TABLE = "timeline_timelineevent"

TRIGGER_SQL: dict[str, list[str]] = {
    "postgresql": [
        """
        CREATE OR REPLACE FUNCTION timeline_event_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'timeline events are append-only' USING ERRCODE = 'restrict_violation';
        END
        $$
        """,
        f"""
        CREATE OR REPLACE TRIGGER timeline_event_no_update_delete
        BEFORE UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION timeline_event_append_only()
        """,
        f"""
        CREATE OR REPLACE TRIGGER timeline_event_no_truncate
        BEFORE TRUNCATE ON {TABLE}
        FOR EACH STATEMENT EXECUTE FUNCTION timeline_event_append_only()
        """,
    ],
    "sqlite": [
        f"""
        CREATE TRIGGER IF NOT EXISTS timeline_event_no_update BEFORE UPDATE ON {TABLE}
        BEGIN SELECT RAISE(ABORT, 'timeline events are append-only'); END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS timeline_event_no_delete BEFORE DELETE ON {TABLE}
        BEGIN SELECT RAISE(ABORT, 'timeline events are append-only'); END
        """,
    ],
}
DROP_TRIGGER_SQL: dict[str, list[str]] = {
    "postgresql": [
        f"DROP TRIGGER IF EXISTS timeline_event_no_update_delete ON {TABLE}",
        f"DROP TRIGGER IF EXISTS timeline_event_no_truncate ON {TABLE}",
        "DROP FUNCTION IF EXISTS timeline_event_append_only()",
    ],
    "sqlite": [
        "DROP TRIGGER IF EXISTS timeline_event_no_update",
        "DROP TRIGGER IF EXISTS timeline_event_no_delete",
    ],
}


def _execute(statements: dict[str, list[str]], conn: Any = None) -> None:
    conn = conn or connection
    with conn.cursor() as cursor:
        for statement in statements.get(conn.vendor, []):
            cursor.execute(statement)


def install_triggers(conn: Any = None) -> None:
    _execute(TRIGGER_SQL, conn)


def remove_triggers(conn: Any = None) -> None:
    _execute(DROP_TRIGGER_SQL, conn)


# Migration operations (timeline 0001): run for the database being migrated.

def install_triggers_migration(apps: Any, schema_editor: Any) -> None:
    install_triggers(schema_editor.connection)


def remove_triggers_migration(apps: Any, schema_editor: Any) -> None:
    remove_triggers(schema_editor.connection)

"""The only writer of TimelineEvent. Call it inside the transaction that makes the change it records."""

from collections.abc import Callable
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


# Called with the types of every batch of recorded events (M13: insights recompute after relevant changes).
# A listener must not write timeline events itself and should defer work with transaction.on_commit.
_listeners: list[Callable[[set[str]], None]] = []


def subscribe(listener: Callable[[set[str]], None]) -> None:
    if listener not in _listeners:
        _listeners.append(listener)


def _notify(types: set[str]) -> None:
    for listener in _listeners:
        listener(types)


def record(event_type: str, *, actor: Actor, invoice: Any = None, client: Any = None, **payload: Any) -> TimelineEvent:
    if client is None and invoice is not None:
        client = invoice.client
    event = TimelineEvent.objects.create(
        type=event_type, invoice=invoice, client=client, payload=payload, **actor_fields(actor)
    )
    _notify({event_type})
    return event


def build(event_type: str, *, actor: Actor, invoice: Any = None, client: Any = None, **payload: Any) -> TimelineEvent:
    """An unsaved event, for bulk_create."""
    if client is None and invoice is not None:
        client = invoice.client
    return TimelineEvent(type=event_type, invoice=invoice, client=client, payload=payload, **actor_fields(actor))


def bulk_record(events: list[TimelineEvent]) -> None:
    TimelineEvent.objects.bulk_create(events)
    if events:
        _notify({event.type for event in events})


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


# Other append-only audit tables (PostgreSQL only), guarded the same way once they exist.
AUDIT_TABLES = ("inbox_replyclassification",)
AUDIT_FUNCTION_SQL = """
    CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
    END
    $$
"""


def _audit_install_sql(table: str) -> list[str]:
    return [
        f"CREATE OR REPLACE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION audit_append_only()",
        f"CREATE OR REPLACE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_append_only()",
    ]


def _audit_drop_sql(table: str) -> list[str]:
    return [f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}",
            f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}"]


def _execute(statements: dict[str, list[str]], conn: Any = None) -> None:
    conn = conn or connection
    with conn.cursor() as cursor:
        for statement in statements.get(conn.vendor, []):
            cursor.execute(statement)


def _existing_audit_tables(conn: Any, tables: tuple[str, ...] | None = None) -> list[str]:
    if conn.vendor != "postgresql":
        return []
    present = set(conn.introspection.table_names())
    return [table for table in (tables or AUDIT_TABLES) if table in present]


def install_triggers(conn: Any = None) -> None:
    conn = conn or connection
    _execute(TRIGGER_SQL, conn)
    install_audit_triggers(conn)


def remove_triggers(conn: Any = None) -> None:
    conn = conn or connection
    _execute(DROP_TRIGGER_SQL, conn)
    remove_audit_triggers(conn)


def install_audit_triggers(conn: Any, tables: tuple[str, ...] | None = None) -> None:
    existing = _existing_audit_tables(conn, tables)
    if not existing:
        return
    with conn.cursor() as cursor:
        cursor.execute(AUDIT_FUNCTION_SQL)
        for table in existing:
            for statement in _audit_install_sql(table):
                cursor.execute(statement)


def remove_audit_triggers(conn: Any, tables: tuple[str, ...] | None = None) -> None:
    with conn.cursor() as cursor:
        for table in _existing_audit_tables(conn, tables):
            for statement in _audit_drop_sql(table):
                cursor.execute(statement)


# Migration operations (timeline 0001): run for the database being migrated.

def install_triggers_migration(apps: Any, schema_editor: Any) -> None:
    install_triggers(schema_editor.connection)


def remove_triggers_migration(apps: Any, schema_editor: Any) -> None:
    remove_triggers(schema_editor.connection)

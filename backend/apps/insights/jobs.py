"""
When client insights are recomputed (M13).

- After a relevant change: every change that can move a score already writes a timeline event (sync,
  payment status, reply, send, promise, dispute). A timeline listener requests a recompute once the
  change commits. Requests coalesce: while a recompute is still PENDING, another one is not queued.
- On a schedule: once per INSIGHTS_REFRESH_MINUTES slot (days overdue grow even when nothing changes),
  enqueued by the worker with a slot key, so restarts and repeated scheduling make one job per slot.

A recompute is a pure function of the database, so a retried or repeated job is harmless.
"""

from typing import Any

from django.db import transaction

from apps.automation import outbox
from apps.automation.models import OutboxJob
from apps.timeline.models import EventType

from . import risk

RECOMPUTE_JOB_KIND = "insights.recompute"

TRIGGER_EVENTS = frozenset({
    EventType.INVOICE_CREATED, EventType.INVOICE_UPDATED, EventType.INVOICE_REMOVED_FROM_SHEET,
    EventType.INVOICE_RETURNED_TO_SHEET, EventType.FOLLOW_UP_SENT, EventType.REPLY_RECEIVED,
    EventType.PROMISE_RECORDED, EventType.PROMISE_KEPT, EventType.PROMISE_BROKEN, EventType.PROMISE_CANCELLED,
    EventType.DISPUTE_OPENED, EventType.DISPUTE_RESOLVED,
})


def request_recompute(*, trigger: str = "change", created_by: Any = None) -> OutboxJob:
    """Queue a recompute unless one is already waiting to start (it will see this change)."""
    waiting = OutboxJob.objects.filter(kind=RECOMPUTE_JOB_KIND, status=OutboxJob.Status.PENDING).order_by("created_at").first()
    if waiting is not None:
        return waiting
    return outbox.enqueue(RECOMPUTE_JOB_KIND, {"trigger": trigger}, created_by=created_by)


def on_timeline_events(types: set[str]) -> None:
    if types & TRIGGER_EVENTS:
        transaction.on_commit(request_recompute)


def handle_recompute_job(job: Any) -> dict[str, Any]:
    return risk.recompute()

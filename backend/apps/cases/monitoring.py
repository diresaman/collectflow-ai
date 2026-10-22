"""
Deterministic promise monitoring and overdue tasks (M11). Run by the worker every CASES_MONITOR_MINUTES
(idempotent slot key) and after each successful sync. Never drafts, approves or sends.

For each OPEN promise, from the sheet's own record (never AI output):
- the invoice turned PAID (the sync's INVOICE_UPDATED event with status UNPAID -> PAID, after the promise
  was recorded) on or before promised_date + PROMISE_GRACE_DAYS -> KEPT;
- it turned PAID later -> BROKEN, but nothing is escalated (the invoice is paid and leaves its sequence);
- it is still UNPAID after that day -> BROKEN: the promise's hold is released and the sequence's next
  reminder is one tone firmer and mentions the missed date (other holds still apply);
- otherwise it stays OPEN. An invoice missing from the sheet is left OPEN (it is not chased anyway).

Each promise is handled in its own transaction under SELECT … FOR UPDATE SKIP LOCKED with a status
re-check, so parallel workers or repeated runs never apply a transition twice.
"""

import datetime as dt
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.inbox.models import Task
from apps.invoices.models import Invoice
from apps.sequences import engine
from apps.timeline import services as timeline
from apps.timeline.models import ActorType, EventType, TimelineEvent

from . import promises
from .models import PromiseToPay

MONITOR_JOB_KIND = "cases.monitor"
Status = PromiseToPay.Status


def paid_seen_at(promise: PromiseToPay) -> dt.datetime | None:
    """When the sheet first showed the invoice PAID after the promise was recorded."""
    events = TimelineEvent.objects.filter(
        invoice_id=promise.invoice_id, type=EventType.INVOICE_UPDATED, occurred_at__gte=promise.created_at,
        payload__changes__status__to=Invoice.Status.PAID,
    ).order_by("occurred_at")
    first = events.values_list("occurred_at", flat=True).first()
    return first


def evaluate(promise: PromiseToPay, *, today: dt.date) -> tuple[str, dt.datetime | None]:
    """(new status or "" to keep OPEN, when payment was seen). Pure apart from reading the timeline."""
    last_day = promises.deadline(promise)
    invoice = promise.invoice
    if invoice.status == Invoice.Status.PAID:
        seen = paid_seen_at(promise) or timezone.now()
        return (Status.KEPT if timezone.localtime(seen).date() <= last_day else Status.BROKEN), seen
    if not invoice.in_sheet:
        return "", None
    if today > last_day:
        return Status.BROKEN, None
    return "", None


def check_promises(*, now: dt.datetime | None = None) -> dict[str, int]:
    now = now or timezone.now()
    today = timezone.localdate(now)
    counts = {"kept": 0, "broken": 0, "escalated": 0, "open": 0}
    for promise_id in PromiseToPay.objects.filter(status=Status.OPEN).values_list("pk", flat=True):
        with transaction.atomic():
            promise = (PromiseToPay.objects.select_for_update(skip_locked=True, of=("self",))
                       .select_related("invoice__client").filter(pk=promise_id, status=Status.OPEN).first())
            if promise is None:
                continue  # another worker has it, or it was resolved meanwhile
            Invoice.objects.select_for_update().filter(pk=promise.invoice_id).first()
            promise.invoice.refresh_from_db()
            status, seen = evaluate(promise, today=today)
            if not status:
                counts["open"] += 1
                continue
            promises.finish(promise, status, actor=ActorType.SCHEDULER, paid_seen_at=seen,
                            note="Paid within the grace period." if status == Status.KEPT else
                            ("Paid after the grace period." if seen else "Not paid within the grace period."))
            counts[status.lower()] += 1
            if status == Status.BROKEN and seen is None:
                result = engine.escalate_after_broken_promise(promise.invoice_id, promised_date=promise.promised_date,
                                                              actor=ActorType.SCHEDULER)
                if result.get("escalated"):
                    counts["escalated"] += 1
                    timeline.record(EventType.SEQUENCE_ESCALATED, actor=ActorType.SCHEDULER, invoice=promise.invoice,
                                    promise_id=str(promise.pk), escalation_level=result["escalation_level"],
                                    pending_step=result["pending_step"])
    return counts


def flag_overdue_tasks(*, now: dt.datetime | None = None) -> int:
    """Records each open task that passed its due time, once."""
    now = now or timezone.now()
    flagged = 0
    for task_id in Task.objects.filter(status=Task.Status.OPEN, due_at__lt=now,
                                       overdue_notified_at__isnull=True).values_list("pk", flat=True):
        with transaction.atomic():
            claimed = Task.objects.filter(pk=task_id, status=Task.Status.OPEN, overdue_notified_at__isnull=True).update(
                overdue_notified_at=now)
            if claimed != 1:
                continue
            task = Task.objects.select_related("invoice", "client").get(pk=task_id)
            timeline.record(EventType.TASK_OVERDUE, actor=ActorType.SCHEDULER, invoice=task.invoice,
                            client=task.client, task_id=str(task.pk), kind=task.kind,
                            due_at=task.due_at.isoformat())
            flagged += 1
    return flagged


def handle_monitor_job(job: Any) -> dict[str, Any]:
    return {**check_promises(), "overdue_tasks": flag_overdue_tasks()}

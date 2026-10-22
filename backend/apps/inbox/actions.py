"""
What a reply's label does (M10). Deterministic backend rules; the AI only proposes the label.

| Label          | Chasing                                   | Other effect                         |
| -------------- | ----------------------------------------- | ------------------------------------ |
| PROMISE_TO_PAY | held (PROMISE_TO_PAY) until a person acts | review (M11 adds the promise record) |
| DISPUTE        | held (DISPUTE) until a person acts        | review (M11 adds the dispute case)   |
| CLAIMS_PAID    | held (CLAIMS_PAID)                        | RECONCILE task; never marks PAID     |
| WRONG_CONTACT  | held (WRONG_CONTACT)                      | UPDATE_CONTACT task                  |
| REQUESTS_INFO  | released                                  | SEND_INFO task                       |
| OUT_OF_OFFICE  | released; next step postponed             | none                                 |
| OTHER          | AI: held for review; person: released     | none                                 |

Releasing a PROMISE_TO_PAY, DISPUTE or CLAIMS_PAID hold needs an APPROVER (see triage). Every call
runs inside the caller's transaction with the invoices locked; repeating it changes nothing (one hold
per reply and invoice, one task per reply and kind, postponement only ever moves later).
"""

import datetime as dt
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.invoices.models import ChaseHold, Invoice
from apps.sequences import engine
from apps.timeline import services as timeline
from apps.timeline.models import EventType

from . import holds
from .models import InboundMessage, ReplyLabel, Task

HOLDING_LABELS = {
    ReplyLabel.PROMISE_TO_PAY: ChaseHold.Reason.PROMISE_TO_PAY,
    ReplyLabel.DISPUTE: ChaseHold.Reason.DISPUTE,
    ReplyLabel.CLAIMS_PAID: ChaseHold.Reason.CLAIMS_PAID,
    ReplyLabel.WRONG_CONTACT: ChaseHold.Reason.WRONG_CONTACT,
}
TASKS = {
    ReplyLabel.CLAIMS_PAID: (Task.Kind.RECONCILE, "Check whether invoice {number} was paid"),
    ReplyLabel.WRONG_CONTACT: (Task.Kind.UPDATE_CONTACT, "Update the contact for invoice {number}"),
    ReplyLabel.REQUESTS_INFO: (Task.Kind.SEND_INFO, "Send the requested information for invoice {number}"),
}
TASK_DETAIL_FIELDS = {
    ReplyLabel.CLAIMS_PAID: ("paid_date", "paid_amount", "payment_reference"),
    ReplyLabel.WRONG_CONTACT: ("new_contact_email",),
    ReplyLabel.REQUESTS_INFO: ("requested_info",),
}


def out_of_office_until(extracted: dict[str, Any], now: dt.datetime) -> dt.datetime:
    """The day after the return date, never in the past and at most OUT_OF_OFFICE_MAX_DAYS ahead."""
    latest = now + dt.timedelta(days=settings.OUT_OF_OFFICE_MAX_DAYS)
    value = extracted.get("return_date") or ""
    try:
        back = dt.date.fromisoformat(value)
    except ValueError:
        return now + dt.timedelta(days=settings.OUT_OF_OFFICE_DEFAULT_DAYS)
    until = timezone.make_aware(dt.datetime.combine(back + dt.timedelta(days=1), dt.time(9, 0)))
    return min(max(until, now), latest)


def _task(reply: InboundMessage, invoice: Invoice, label: str, extracted: dict[str, Any], actor: Any) -> Task:
    kind, title = TASKS[label]
    facts = [f"{name.replace('_', ' ')}: {extracted[name]}" for name in TASK_DETAIL_FIELDS[label] if extracted.get(name)]
    task, created = Task.objects.get_or_create(
        reply=reply, kind=kind,
        defaults={"title": title.format(number=invoice.external_id)[:200], "invoice": invoice,
                  "client": invoice.client, "detail": "; ".join(facts)[:500]},
    )
    if created:
        timeline.record(EventType.TASK_CREATED, actor=actor, invoice=invoice, task_id=str(task.pk), kind=kind,
                        reply_id=str(reply.pk))
    return task


def apply_label(reply: InboundMessage, invoices: list[Invoice], *, label: str, extracted: dict[str, Any],
                actor: Any, by_person: bool) -> dict[str, Any]:
    """Applies the label's effect to the reply's (locked) invoices. Returns what was done."""
    done: dict[str, Any] = {"label": label}
    if label in HOLDING_LABELS:
        holds.place(invoices, reply=reply, reason=HOLDING_LABELS[label], actor=actor,
                    detail=f"Customer reply classified as {ReplyLabel(label).label.lower()}.")
        done["hold"] = HOLDING_LABELS[label]
    elif label == ReplyLabel.OTHER and not by_person:
        holds.place(invoices, reply=reply, reason=ChaseHold.Reason.NEEDS_REVIEW, actor=actor,
                    detail="No automatic action fits this reply; a person must review it.")
        done["hold"] = ChaseHold.Reason.NEEDS_REVIEW
    else:
        released = holds.release(reply, actor=actor, note=f"Reply classified as {label}.")
        done["released_holds"] = len(released)

    if label in TASKS:
        done["task_id"] = str(_task(reply, reply.invoice, label, extracted, actor).pk)
    if label == ReplyLabel.OUT_OF_OFFICE:
        until = out_of_office_until(extracted, timezone.now())
        done["postponed"] = [engine.postpone(invoice.pk, until, actor=actor, reply_id=str(reply.pk)) for invoice in invoices]
    return done


def hold_for_review(reply: InboundMessage, invoices: list[Invoice], *, label: str, actor: Any, reason: str) -> None:
    """Triage: keep chasing held. A promise, dispute or payment claim keeps its own (protected) reason."""
    hold_reason = HOLDING_LABELS.get(label, ChaseHold.Reason.NEEDS_REVIEW)
    if label in (ReplyLabel.WRONG_CONTACT,):
        hold_reason = ChaseHold.Reason.NEEDS_REVIEW
    holds.place(invoices, reply=reply, reason=hold_reason, actor=actor,
                detail=f"Reply needs review ({reason.replace('_', ' ')}).")

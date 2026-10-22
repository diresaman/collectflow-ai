"""
Promise-to-pay workflow (M11, v2 §6).

- A person confirms a promise (from a PROMISE_TO_PAY reply or manually). The AI's extracted date and
  amount are only suggestions shown in the form; the promise holds what the person submitted.
- Recording it: one OPEN promise per invoice; the invoice's unsent reminders are withdrawn without
  completing the sequence step; a chase hold owned by the promise stops chasing (the step stays pending);
  the reply's own hold on this invoice is handed over to it.
- Monitoring (monitoring.py) decides KEPT or BROKEN from the sheet, deterministically.
- An approver may cancel an OPEN promise, with a note. That releases only the promise's hold.
"""

import datetime as dt
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.followups.services import cancel_unsent
from apps.inbox import holds
from apps.inbox.models import ReplyLabel
from apps.invoices.models import ChaseHold
from apps.timeline import services as timeline
from apps.timeline.models import EventType

from .common import (
    CaseError,
    InvalidCase,
    amount_value,
    date_value,
    lock_case_rows,
    require,
    require_chaseable,
    require_not_disputed,
    settle_reply,
    text,
)
from .models import PromiseToPay

Status = PromiseToPay.Status


class PromiseExists(CaseError):
    code = "promise_exists"


def validate_date(promised: dt.date, today: dt.date | None = None) -> None:
    today = today or timezone.localdate()
    latest = today + dt.timedelta(days=settings.PROMISE_MAX_DAYS)
    if promised < today:
        raise InvalidCase("The promised date is in the past. Leave the reply in review or call the client.")
    if promised > latest:
        raise InvalidCase(f"The promised date is more than {settings.PROMISE_MAX_DAYS} days away "
                          f"(latest {latest.isoformat()}). Leave the reply in review or call the client.")


def deadline(promise: PromiseToPay) -> dt.date:
    """The last day the payment may be recorded for the promise to count as kept."""
    return promise.promised_date + dt.timedelta(days=settings.PROMISE_GRACE_DAYS)


def record(invoice_id: Any, *, promised_date: Any, amount: Any = None, reply_id: Any = None, note: Any = "",
           actor: Any) -> PromiseToPay:
    require(actor, Role.OPERATOR, "record a promise")
    promised = date_value(promised_date, "promised_date")
    validate_date(promised)
    note = text(note, "note", required=False, limit=500)
    value = amount_value(amount, "amount")
    try:
        with transaction.atomic():
            invoice, reply = lock_case_rows(invoice_id, reply_id)
            if reply is not None:
                existing = PromiseToPay.objects.filter(reply=reply).first()
                if existing is not None:
                    return existing  # the same reply confirmed twice: one promise
            require_chaseable(invoice)
            require_not_disputed(invoice)
            if value is None:
                value = invoice.amount
            if value > invoice.amount:
                raise InvalidCase(f"The promised amount is more than the invoice amount ({invoice.amount} {invoice.currency}).")
            if PromiseToPay.objects.filter(invoice=invoice, status=Status.OPEN).exists():
                raise PromiseExists("This invoice already has an open promise. Cancel it first to record a new one.")

            promise = PromiseToPay.objects.create(
                invoice=invoice, client=invoice.client, reply=reply, promised_date=promised, amount=value,
                currency=invoice.currency, note=note, owner=actor,
            )
            withdrawn = cancel_unsent(invoice.pk, reason="promise", actor=actor)
            holds.place_owned(invoice, owner="promise", owner_id=promise.pk, reason=ChaseHold.Reason.PROMISE_TO_PAY,
                              actor=actor, detail=f"Promise to pay by {promised.isoformat()}.")
            if reply is not None:
                settle_reply(reply, invoice, label=ReplyLabel.PROMISE_TO_PAY, decision="promise_recorded", actor=actor,
                             note=note, extracted={"promised_date": promised.isoformat(), "promised_amount": str(value)})
            timeline.record(EventType.PROMISE_RECORDED, actor=actor, invoice=invoice, promise_id=str(promise.pk),
                            promised_date=promised.isoformat(), amount=str(value), currency=invoice.currency,
                            reply_id=str(reply.pk) if reply else None,
                            withdrawn_follow_up_ids=[str(f.pk) for f in withdrawn],
                            grace_until=deadline(promise).isoformat())
    except IntegrityError:
        raise PromiseExists("A promise was recorded for this invoice at the same time.") from None
    return promise


def cancel(promise_id: Any, *, note: Any, actor: Any) -> PromiseToPay:
    """An approver withdraws an OPEN promise. Releases only the promise's own hold."""
    require(actor, Role.APPROVER, "cancel a promise (it resumes chasing)")
    note = text(note, "note", required=True, limit=500)
    with transaction.atomic():
        promise = PromiseToPay.objects.select_for_update().select_related("invoice__client").get(pk=promise_id)
        if promise.status != Status.OPEN:
            raise CaseError(f"Only an open promise can be cancelled; this one is {promise.status}.")
        holds.lock_invoices([promise.invoice_id])
        finish(promise, Status.CANCELLED, actor=actor, note=note)
    return promise


def finish(promise: PromiseToPay, status: str, *, actor: Any, note: str = "", paid_seen_at: Any = None) -> None:
    """Closes a (locked, OPEN) promise and releases its hold. Runs in the caller's transaction."""
    promise.status = status
    promise.resolved_at = timezone.now()
    promise.resolved_by = actor if getattr(actor, "is_authenticated", False) else None
    promise.resolution_note = note[:500]
    promise.paid_seen_at = paid_seen_at or promise.paid_seen_at
    promise.save()
    holds.release_owned(owner="promise", owner_id=promise.pk, actor=actor, note=f"Promise {status.lower()}.")
    event = {Status.KEPT: EventType.PROMISE_KEPT, Status.BROKEN: EventType.PROMISE_BROKEN,
             Status.CANCELLED: EventType.PROMISE_CANCELLED}[status]
    timeline.record(event, actor=actor, invoice=promise.invoice, promise_id=str(promise.pk),
                    promised_date=promise.promised_date.isoformat(), grace_until=deadline(promise).isoformat(),
                    paid_seen_at=promise.paid_seen_at.isoformat() if promise.paid_seen_at else None)

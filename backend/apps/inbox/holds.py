"""
Chase holds placed and released because of customer replies (M10). Every function here runs in the
caller's transaction and locks the invoice rows first, the same lock that drafting, approval and send
reservation take before re-checking eligibility, so a hold and a reminder can never cross.
"""

from collections.abc import Iterable
from typing import Any

from django.utils import timezone

from apps.invoices.models import ChaseHold, Invoice
from apps.timeline import services as timeline
from apps.timeline.models import EventType

Reason = ChaseHold.Reason


def lock_invoices(invoice_ids: Iterable[Any]) -> list[Invoice]:
    """Locks the invoices in a fixed order (by primary key) so concurrent writers cannot deadlock."""
    return list(Invoice.objects.select_for_update().select_related("client").filter(pk__in=list(invoice_ids)).order_by("pk"))


def place(invoices: list[Invoice], *, reply: Any, reason: str, actor: Any, detail: str = "") -> list[ChaseHold]:
    """Puts (or keeps) each invoice on hold for this reply with `reason`. Idempotent."""
    holds = []
    for invoice in invoices:
        hold = ChaseHold.objects.filter(invoice=invoice, reply_id=reply.pk, released_at__isnull=True).first()
        if hold is not None and hold.reason == reason:
            holds.append(hold)
            continue
        previous = hold.reason if hold is not None else None
        if hold is None:
            hold = ChaseHold.objects.create(invoice=invoice, reply_id=reply.pk, reason=reason, detail=detail[:300])
        else:
            hold.reason = reason
            hold.detail = detail[:300] or hold.detail
            hold.save(update_fields=["reason", "detail", "updated_at"])
        timeline.record(EventType.CHASE_HOLD_PLACED, actor=actor, invoice=invoice, hold_id=str(hold.pk),
                        reply_id=str(reply.pk), reason=reason, previous_reason=previous)
        holds.append(hold)
    return holds


def active_for_reply(reply: Any, invoice: Any = None) -> list[ChaseHold]:
    holds = ChaseHold.objects.filter(reply_id=reply.pk, released_at__isnull=True).select_related("invoice")
    if invoice is not None:
        holds = holds.filter(invoice=invoice)
    return list(holds)


def release(reply: Any, *, actor: Any, note: str = "", invoice: Any = None) -> list[ChaseHold]:
    """
    Releases this reply's holds (only on `invoice`, when given). Chasing resumes once an invoice has no
    other active hold: holds owned by other replies, promises or disputes are never touched.
    """
    return _release(active_for_reply(reply, invoice), actor=actor, note=note)


# Holds owned by a promise or a dispute (M11) -------------------------------------------------------

OWNERS = ("promise", "dispute")


def place_owned(invoice: Invoice, *, owner: str, owner_id: Any, reason: str, actor: Any, detail: str = "") -> ChaseHold:
    """Holds the (locked) invoice for a promise or dispute. Idempotent."""
    assert owner in OWNERS
    key = {f"{owner}_id": owner_id}
    hold = ChaseHold.objects.filter(invoice=invoice, released_at__isnull=True, **key).first()
    if hold is not None:
        return hold
    hold = ChaseHold.objects.create(invoice=invoice, reason=reason, detail=detail[:300], **key)
    timeline.record(EventType.CHASE_HOLD_PLACED, actor=actor, invoice=invoice, hold_id=str(hold.pk),
                    reason=reason, **{k: str(v) for k, v in key.items()})
    return hold


def release_owned(*, owner: str, owner_id: Any, actor: Any, note: str = "") -> list[ChaseHold]:
    """Releases the holds owned by this promise or dispute, and no others."""
    assert owner in OWNERS
    holds = ChaseHold.objects.filter(released_at__isnull=True, **{f"{owner}_id": owner_id}).select_related("invoice")
    return _release(list(holds), actor=actor, note=note)


def _release(holds: list[ChaseHold], *, actor: Any, note: str) -> list[ChaseHold]:
    released = []
    now = timezone.now()
    for hold in holds:
        hold.released_at = now
        hold.released_by = actor if getattr(actor, "is_authenticated", False) else None
        hold.release_note = note[:300]
        hold.save(update_fields=["released_at", "released_by", "release_note", "updated_at"])
        remaining = ChaseHold.objects.filter(invoice_id=hold.invoice_id, released_at__isnull=True).count()
        owner = {"reply_id": hold.reply_id, "promise_id": hold.promise_id, "dispute_id": hold.dispute_id}
        timeline.record(EventType.CHASE_HOLD_RELEASED, actor=actor, invoice=hold.invoice, hold_id=str(hold.pk),
                        reason=hold.reason, other_active_holds=remaining,
                        **{k: str(v) for k, v in owner.items() if v})
        released.append(hold)
    return released

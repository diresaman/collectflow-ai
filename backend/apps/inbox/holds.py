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


def active_for_reply(reply: Any) -> list[ChaseHold]:
    return list(ChaseHold.objects.filter(reply_id=reply.pk, released_at__isnull=True).select_related("invoice"))


def release(reply: Any, *, actor: Any, note: str = "") -> list[ChaseHold]:
    """Releases this reply's holds. Chasing resumes once an invoice has no other active hold."""
    released = []
    now = timezone.now()
    for hold in active_for_reply(reply):
        hold.released_at = now
        hold.released_by = actor if getattr(actor, "is_authenticated", False) else None
        hold.release_note = note[:300]
        hold.save(update_fields=["released_at", "released_by", "release_note", "updated_at"])
        remaining = ChaseHold.objects.filter(invoice_id=hold.invoice_id, released_at__isnull=True).count()
        timeline.record(EventType.CHASE_HOLD_RELEASED, actor=actor, invoice=hold.invoice, hold_id=str(hold.pk),
                        reply_id=str(reply.pk), reason=hold.reason, other_active_holds=remaining)
        released.append(hold)
    return released

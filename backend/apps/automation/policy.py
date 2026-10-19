"""
Deterministic proactive follow-up policy. Pure decision logic: no writes, no API calls.

Evaluation order (first match wins):
  1. An active follow-up exists:
       DRAFT/APPROVED whose invoice changed  -> stale_draft / stale_approved (surface for review)
       DRAFT                                 -> draft_awaiting_review
       APPROVED                              -> approved_awaiting_send
       SENDING / DELIVERY_UNKNOWN            -> delivery_unresolved
  2. Invoice rules (shared with manual generation): not_in_sheet, not_unpaid, not_overdue,
     invalid_recipient, invalid_amount.
  3. Most recent follow-up FAILED            -> delivery_failed (operator attention)
  4. Last SENT within the cooldown           -> cooldown
  5. Most recent follow-up REJECTED within the cooldown -> recently_rejected
  Otherwise the invoice is eligible.
"""

import datetime as dt
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from apps.followups.eligibility import changed_snapshot_fields, ineligibility
from apps.followups.models import FollowUp
from apps.invoices.models import Invoice


@dataclass(frozen=True)
class Decision:
    eligible: bool
    reason_code: str = ""
    detail: str = ""
    follow_up: FollowUp | None = None

    @classmethod
    def skip(cls, reason_code: str, detail: str, follow_up: FollowUp | None = None) -> "Decision":
        return cls(False, reason_code, detail, follow_up)


ELIGIBLE = Decision(True)


def cooldown() -> dt.timedelta:
    return dt.timedelta(days=settings.FOLLOWUP_COOLDOWN_DAYS)


def evaluate(invoice: Invoice, follow_ups: list[FollowUp], *, now: dt.datetime | None = None) -> Decision:
    """`follow_ups` are the invoice's follow-ups, newest first."""
    now = now or timezone.now()

    active = next((f for f in follow_ups if f.status in FollowUp.ACTIVE_STATUSES), None)
    if active is not None:
        if active.status in (FollowUp.Status.DRAFT, FollowUp.Status.APPROVED):
            changed = changed_snapshot_fields(active.invoice_snapshot, invoice)
            label = "draft" if active.status == FollowUp.Status.DRAFT else "approved reminder"
            if changed:
                return Decision.skip(
                    f"stale_{active.status.lower()}",
                    f"Invoice changed since the {label} was generated ({', '.join(changed)}). Review it.",
                    active,
                )
            if active.status == FollowUp.Status.DRAFT:
                return Decision.skip("draft_awaiting_review", "A draft is awaiting review.", active)
            return Decision.skip("approved_awaiting_send", "An approved reminder has not been sent yet.", active)
        return Decision.skip(
            "delivery_unresolved", "A delivery is in progress or has an unknown outcome; reconcile it first.", active
        )

    reasons = ineligibility(invoice)
    if reasons:
        return Decision.skip(*reasons[0])

    latest = follow_ups[0] if follow_ups else None
    if latest is not None and latest.status == FollowUp.Status.FAILED:
        return Decision.skip(
            "delivery_failed", "The last delivery failed and needs operator attention.", latest
        )

    last_sent = next((f for f in follow_ups if f.status == FollowUp.Status.SENT and f.sent_at), None)
    if last_sent is not None and now - last_sent.sent_at < cooldown():  # type: ignore[operator]
        until = timezone.localtime(last_sent.sent_at + cooldown()).date()  # type: ignore[operator]
        return Decision.skip("cooldown", f"A reminder was sent recently; cooldown until {until.isoformat()}.")

    if latest is not None and latest.status == FollowUp.Status.REJECTED and now - latest.updated_at < cooldown():
        return Decision.skip("recently_rejected", "A draft was rejected recently; not redrafting during the cooldown.")

    return ELIGIBLE

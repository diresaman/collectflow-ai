"""
Four-eyes approval policy (v2 §6).

A reminder needs an APPROVER who is not its last editor when its tone is FIRM, its sequence step
requires an approver, or the invoice amount is at or above HIGH_VALUE_THRESHOLD (compared in the
invoice's own currency), or it is a consolidated statement (M14, always). Anything else can be
approved by an OPERATOR. Approval never sends; every send still needs its own confirmation.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings

from .models import Role
from .roles import has_role

APPROVER_REQUIRED = "approver_required"
SELF_APPROVAL = "self_approval"


@dataclass(frozen=True)
class ApprovalRequirement:
    role: str
    reasons: tuple[str, ...]

    @property
    def four_eyes(self) -> bool:
        return self.role == Role.APPROVER


def requirement(follow_up: Any) -> ApprovalRequirement:
    reasons: list[str] = []
    if getattr(follow_up, "kind", "INVOICE") == "STATEMENT":
        reasons.append("A consolidated statement always needs an approver")
    if follow_up.tone == "FIRM":
        reasons.append("Firm tone")
    step = getattr(follow_up, "step", None)
    if step is not None and step.requires_approver:
        reasons.append(f"Sequence step {step.order} requires an approver")
    amount = _amount(follow_up)
    if amount is not None and amount >= settings.HIGH_VALUE_THRESHOLD:
        currency = follow_up.invoice_snapshot.get("currency", "")
        reasons.append(f"Amount {amount:.2f} {currency} is at or above the {settings.HIGH_VALUE_THRESHOLD:.0f} threshold")
    return ApprovalRequirement(Role.APPROVER if reasons else Role.OPERATOR, tuple(reasons))


def refusal(follow_up: Any, user: Any) -> tuple[str, str] | None:
    """(code, message) if `user` may not approve this follow-up, else None."""
    need = requirement(follow_up)
    if user is None:
        # Internal callers without a user may only approve what an operator could.
        return (APPROVER_REQUIRED, "An approver must approve this reminder (" + "; ".join(need.reasons) + ").") if need.four_eyes else None
    if not has_role(user, need.role):
        return APPROVER_REQUIRED, (
            "An approver must approve this reminder (" + "; ".join(need.reasons) + "). Your role cannot."
        )
    if need.four_eyes and follow_up.last_edited_by_id is not None and follow_up.last_edited_by_id == getattr(user, "pk", None):
        return SELF_APPROVAL, (
            "You edited this reminder, so another approver must approve it (four-eyes rule: "
            + "; ".join(need.reasons) + ")."
        )
    return None


def _amount(follow_up: Any) -> Decimal | None:
    # The amount the reminder was written for (approval also refuses a changed invoice).
    try:
        return Decimal(str(follow_up.invoice_snapshot.get("amount")))
    except (InvalidOperation, TypeError):
        return None

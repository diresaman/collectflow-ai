"""Errors, permissions and locking shared by promise and dispute services (M11)."""

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib.auth import get_user_model

from apps.accounts.models import Role
from apps.accounts.roles import has_role
from apps.followups.eligibility import is_disputed
from apps.followups.models import FollowUp
from apps.followups.services import FollowUpError
from apps.inbox import holds
from apps.inbox.models import InboundMessage, ReplyClassification
from apps.inbox.prompts import PROMPT_VERSION as CLASSIFY_PROMPT_VERSION
from apps.invoices.models import Invoice


class CaseError(FollowUpError):
    code = "case_conflict"


class InvalidCase(CaseError):
    status_code = 400
    code = "invalid_input"


class CaseForbidden(CaseError):
    status_code = 403
    code = "forbidden"


class ApproverRequired(CaseForbidden):
    code = "approver_required"


class InvoiceNotChaseable(CaseError):
    code = "invoice_not_chaseable"


def require(actor: Any, role: str, what: str) -> None:
    if not has_role(actor, role):
        if role == Role.APPROVER:
            raise ApproverRequired(f"Only an approver can {what}.")
        raise CaseForbidden("Your role does not allow this action.")


def text(value: Any, name: str, *, required: bool, limit: int) -> str:
    if value in (None, ""):
        if required:
            raise InvalidCase(f"{name} is required.")
        return ""
    if not isinstance(value, str):
        raise InvalidCase(f"{name} must be text.")
    value = value.strip()
    if required and not value:
        raise InvalidCase(f"{name} is required.")
    if len(value) > limit:
        raise InvalidCase(f"{name} must be at most {limit} characters.")
    return value


def date_value(value: Any, name: str) -> dt.date:
    if isinstance(value, dt.date):
        return value
    if not isinstance(value, str):
        raise InvalidCase(f"{name} must be a date (YYYY-MM-DD).")
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        raise InvalidCase(f"{name} must be a date (YYYY-MM-DD).") from None


def amount_value(value: Any, name: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        raise InvalidCase(f"{name} must be a number.") from None
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:  # type: ignore[operator]
        raise InvalidCase(f"{name} must be a positive amount with at most two decimals.")
    return amount


def user_with_role(user_id: Any, name: str) -> Any:
    """An active user with a CollectFlow role, for owners and assignees."""
    if user_id in (None, ""):
        return None
    user = get_user_model().objects.filter(pk=user_id, is_active=True).first() if str(user_id).isdigit() else None
    if user is None or not has_role(user, Role.OPERATOR):
        raise InvalidCase(f"{name} must be an active CollectFlow user.")
    return user


def lock_case_rows(invoice_id: Any, reply_id: Any = None) -> tuple[Invoice, InboundMessage | None]:
    """
    Locks, in this order, the invoice's unsent reminders, the invoice, then the reply (the order approval
    and send reservation use: reminder before invoice), so concurrent writers cannot deadlock.
    """
    list(FollowUp.objects.select_for_update(of=("self",)).filter(
        invoice_id=invoice_id, status__in=[FollowUp.Status.DRAFT, FollowUp.Status.APPROVED]).order_by("pk"))
    locked = holds.lock_invoices([invoice_id])
    if not locked:
        raise Invoice.DoesNotExist
    reply = None
    if reply_id not in (None, ""):
        reply = InboundMessage.objects.select_for_update(of=("self",)).filter(pk=reply_id).first()
        if reply is None:
            raise InvalidCase("reply_id does not match a stored reply.")
        if str(locked[0].pk) not in [str(i) for i in reply.thread_invoice_ids] and reply.invoice_id != locked[0].pk:
            raise InvalidCase("That reply is not about this invoice.")
    return locked[0], reply


def require_chaseable(invoice: Invoice) -> None:
    if invoice.status != Invoice.Status.UNPAID or not invoice.in_sheet:
        raise InvoiceNotChaseable("Only unpaid invoices in the sheet can have a promise or a dispute.")


def require_not_disputed(invoice: Invoice) -> None:
    if is_disputed(invoice):
        from apps.followups.services import InvoiceDisputed

        raise InvoiceDisputed("The invoice has an open dispute. An approver must resolve it first.")


def settle_reply(reply: InboundMessage, invoice: Invoice, *, label: str, extracted: dict[str, Any], decision: str,
                 actor: Any, note: str) -> None:
    """
    The person's decision on the reply that led to a promise or dispute: recorded as a HUMAN
    classification next to the AI's proposal (which stays as it was), and the reply's own hold on this
    invoice is handed over to the promise or dispute hold.
    """
    from django.utils import timezone

    holds.release(reply, actor=actor, note=f"Replaced by the {decision.split('_')[0]} record.", invoice=invoice)
    ReplyClassification.objects.create(
        message=reply, source=InboundMessage.Source.HUMAN, label=label, extracted=extracted, decision=decision,
        actor=actor, note=note[:300], prompt_version=CLASSIFY_PROMPT_VERSION,
    )
    reply.label, reply.label_source, reply.confidence = label, InboundMessage.Source.HUMAN, None
    reply.extracted = {**reply.extracted, **extracted}
    if not holds.active_for_reply(reply):
        reply.state, reply.triage_reason = InboundMessage.State.RESOLVED, ""
        reply.resolved_at, reply.resolved_by = timezone.now(), actor
    reply.save()

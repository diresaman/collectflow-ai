"""Deterministic follow-up rules. These are never delegated to the AI model."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from apps.invoices.models import Invoice

from .models import Tone


def ineligibility_reasons(invoice: Invoice) -> list[str]:
    """Returns why the invoice cannot get a reminder; an empty list means eligible."""
    reasons: list[str] = []
    if invoice.status != Invoice.Status.UNPAID:
        reasons.append("Invoice is not unpaid.")
    if invoice.due_date >= timezone.localdate():
        reasons.append("Invoice is not overdue yet.")
    try:
        validate_email(invoice.client_email)
    except ValidationError:
        reasons.append("Client email address is invalid.")
    if invoice.amount is None or invoice.amount <= Decimal("0"):
        reasons.append("Invoice amount must be positive.")
    return reasons


def tone_for_days_overdue(days_overdue: int) -> str:
    if days_overdue < 1:
        raise ValueError("Tone only applies to overdue invoices.")
    if days_overdue <= 7:
        return Tone.FRIENDLY
    if days_overdue <= 14:
        return Tone.PROFESSIONAL
    return Tone.FIRM


# Fields that, if changed after generation, make a draft stale.
SNAPSHOT_FIELDS = ("external_id", "client_name", "client_email", "amount", "currency", "due_date", "status")


def invoice_snapshot(invoice: Invoice) -> dict[str, str]:
    return {
        "external_id": invoice.external_id,
        "client_name": invoice.client_name,
        "client_email": invoice.client_email,
        "amount": f"{invoice.amount:.2f}",
        "currency": invoice.currency,
        "due_date": invoice.due_date.isoformat(),
        "status": invoice.status,
    }


def changed_snapshot_fields(snapshot: dict[str, str], invoice: Invoice) -> list[str]:
    current = invoice_snapshot(invoice)
    return [name for name in SNAPSHOT_FIELDS if snapshot.get(name) != current[name]]

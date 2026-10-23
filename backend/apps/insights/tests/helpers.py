import datetime as dt
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.cases.models import DisputeCase, PromiseToPay
from apps.followups.models import FollowUp
from apps.followups.tests.helpers import make_follow_up, overdue_invoice
from apps.inbox.models import InboundMessage
from apps.invoices.models import Client, Invoice
from apps.timeline.models import ActorType, EventType, TimelineEvent

EMAIL = "ap@client.test"


def inv(external_id: str, *, days: int = 5, email: str = EMAIL, amount: str = "1000.00", currency: str = "USD",
        **fields: Any) -> Invoice:
    """An invoice `days` overdue (negative: not yet due) for the client with `email`."""
    return overdue_invoice(days=days, external_id=external_id, client_email=email, amount=Decimal(amount),
                           currency=currency, **fields)


def client(email: str = EMAIL) -> Client:
    return Client.objects.get(email=email)


def paid(invoice: Invoice, *, days_late: int) -> None:
    """The sheet showed the invoice PAID `days_late` days after its due date (the sync's event)."""
    seen = timezone.make_aware(dt.datetime.combine(invoice.due_date + dt.timedelta(days=days_late), dt.time(12)))
    Invoice.objects.filter(pk=invoice.pk).update(status=Invoice.Status.PAID)
    TimelineEvent.objects.create(type=EventType.INVOICE_UPDATED, invoice=invoice, client=invoice.client,
                                 actor_type=ActorType.SYSTEM, occurred_at=seen,
                                 payload={"changes": {"status": {"from": "UNPAID", "to": "PAID"}}})


def approver() -> Any:
    from django.contrib.auth import get_user_model

    from apps.accounts.models import Role
    from apps.accounts.testing import make_user

    return get_user_model().objects.filter(username="approver").first() or make_user("approver", Role.APPROVER)


def promise(invoice: Invoice, status: str, *, resolved_days_ago: int = 1) -> PromiseToPay:
    user = approver()
    resolved = None if status == PromiseToPay.Status.OPEN else timezone.now() - dt.timedelta(days=resolved_days_ago)
    return PromiseToPay.objects.create(
        invoice=invoice, client=invoice.client, status=status, resolved_at=resolved, owner=user,
        resolved_by=None if resolved is None else user, amount=invoice.amount, currency=invoice.currency,
        promised_date=timezone.localdate() - dt.timedelta(days=resolved_days_ago + 3))


def dispute(invoice: Invoice, status: str = DisputeCase.Status.OPEN) -> DisputeCase:
    user = approver()
    resolved = None if status == DisputeCase.Status.OPEN else timezone.now()
    return DisputeCase.objects.create(invoice=invoice, client=invoice.client, status=status, reason="AMOUNT",
                                      description="x", opened_by=user, resolved_at=resolved,
                                      resolved_by=None if resolved is None else user,
                                      resolution="" if resolved is None else "Fine")


def send(invoice: Invoice, *, days_ago: float) -> FollowUp:
    when = timezone.now() - dt.timedelta(days=days_ago)
    return make_follow_up(invoice, FollowUp.Status.SENT, sent_at=when, generated_at=when)


def replied(invoice: Invoice, *, days_ago: float, n: int = 0) -> InboundMessage:
    when = timezone.now() - dt.timedelta(days=days_ago)
    return InboundMessage.objects.create(
        gmail_message_id=f"r-{invoice.external_id}-{days_ago}-{n}", gmail_thread_id="t", invoice=invoice,
        client=invoice.client, sender_email=invoice.client_email, subject="Re", received_at=when,
        state=InboundMessage.State.ACTIONED,
    )


def factors(c: Client) -> dict[str, int]:
    c.refresh_from_db()
    return {f["key"]: f["points"] for f in c.risk_factors["factors"]}

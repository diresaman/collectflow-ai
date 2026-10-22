import datetime as dt
from typing import Any

from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.testing import make_user
from apps.inbox import classification, ingestion
from apps.inbox.models import InboundMessage, MailboxCursor
from apps.inbox.tests.helpers import FakeClassifier, FakeMailbox, classification as proposal, collectflow_thread, mail
from apps.invoices.models import Invoice
from apps.sequences import engine
from apps.timeline.models import ActorType, EventType, TimelineEvent


def users() -> tuple[Any, Any]:
    return make_user("op", Role.OPERATOR), make_user("ap", Role.APPROVER)


def chased(external_id: str = "INV-1", thread: str = "thread-1", days: int = 5) -> Invoice:
    """An overdue invoice with step 1 sent in `thread`, enrolled in the default sequence."""
    invoice, _ = collectflow_thread(external_id, thread=thread, days=days)
    engine.reconcile(actor=ActorType.SYSTEM)
    return invoice


def reply(invoice: Invoice, text: str, label: str = "PROMISE_TO_PAY", confidence: float = 0.95,
          thread: str = "thread-1", **extracted: Any) -> InboundMessage:
    """A customer reply in the invoice's thread, ingested and classified by a fake Gemini."""
    MailboxCursor.objects.get_or_create(name="gmail", defaults={"history_id": "100"})
    box = FakeMailbox()
    box.add(mail(f"m-{InboundMessage.objects.count()}", thread, sender=invoice.client_email, text=text))
    ingestion.poll(box, owner="test")  # type: ignore[arg-type]
    stored = InboundMessage.objects.order_by("-created_at").first()
    assert stored is not None
    classification.classify(stored.pk, gemini=FakeClassifier(proposal(label, confidence, **extracted)))
    stored.refresh_from_db()
    return stored


def paid_event(invoice: Invoice, at: dt.datetime | None = None) -> None:
    """What a sync records when the sheet shows the invoice paid: the status change and its event time."""
    Invoice.objects.filter(pk=invoice.pk).update(status=Invoice.Status.PAID)
    invoice.refresh_from_db()
    TimelineEvent.objects.create(type=EventType.INVOICE_UPDATED, invoice=invoice, client=invoice.client,
                                 actor_type=ActorType.SYSTEM, occurred_at=at or timezone.now(),
                                 payload={"changes": {"status": {"from": "UNPAID", "to": "PAID"}}})


def today_plus(days: int) -> dt.date:
    return timezone.localdate() + dt.timedelta(days=days)

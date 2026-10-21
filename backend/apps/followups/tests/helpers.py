import datetime as dt
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.followups.models import FollowUp
from apps.invoices.models import Invoice
from integrations.gemini import GeminiError


def overdue_invoice(days: int = 5, **overrides: Any) -> Invoice:
    fields: dict[str, Any] = {
        "external_id": "INV-100",
        "client_name": "Fictional Studio",
        "client_email": "billing@fictional.test",
        "amount": Decimal("1200.00"),
        "currency": "USD",
        "due_date": timezone.localdate() - dt.timedelta(days=days),
        "status": Invoice.Status.UNPAID,
        "last_synced_at": timezone.now(),
    }
    fields.update(overrides)
    return Invoice.objects.create(**fields)


def ai_output(tone: str = "FRIENDLY", **overrides: Any) -> dict[str, Any]:
    data = {
        "subject": "Friendly reminder: invoice INV-100",
        "body": "Hi Fictional Studio,\n\nThis is a quick reminder that invoice INV-100 is past due.\n\nAccounts Receivable Team",
        "tone": tone,
        "reasoning": "The invoice is a few days overdue, so a warm nudge is appropriate.",
        "recommended_action": "Approve and send the reminder.",
    }
    data.update(overrides)
    return data


class FakeGemini:
    """Stands in for GeminiClient; never calls the network."""

    model = "fake-gemini"

    def __init__(self, response: dict[str, Any] | None = None, error: GeminiError | None = None, on_call: Any = None):
        self.response = response
        self.error = error
        self.on_call = on_call
        self.calls: list[dict[str, Any]] = []

    def generate_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.on_call:
            self.on_call()
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


def make_follow_up(invoice: Invoice, status: str = FollowUp.Status.DRAFT, **overrides: Any) -> FollowUp:
    from apps.followups.eligibility import invoice_snapshot

    fields: dict[str, Any] = {
        "invoice": invoice,
        "subject": "Reminder: invoice INV-100",
        "body": "Hello, this is a reminder that your invoice is overdue.",
        "tone": "FRIENDLY",
        "reasoning": "Recently overdue.",
        "recommended_action": "Approve.",
        "status": status,
        "invoice_snapshot": invoice_snapshot(invoice),
    }
    fields.update(overrides)
    return FollowUp.objects.create(**fields)


class FakeGmail:
    """Stands in for GmailClient; never touches the network."""

    def __init__(
        self,
        *,
        ready: bool = True,
        configured: bool = True,
        detail: str = "Gmail is ready to send.",
        result: Any = None,
        error: Exception | None = None,
        on_check: Any = None,
    ) -> None:
        from integrations.gmail import DeliveryResult

        self.ready = ready
        self.configured = configured
        self.detail = detail
        self.result = result or DeliveryResult(message_id="msg-123", thread_id="thread-456")
        self.error = error
        self.on_check = on_check
        self.sent: list[dict[str, str]] = []

    def check_configured(self):  # type: ignore[no-untyped-def]
        from integrations.gmail import GmailStatus

        return GmailStatus(True, self.configured, "sender@example.test", None, "Gmail is configured.")

    def check_ready(self):  # type: ignore[no-untyped-def]
        from integrations.gmail import GmailStatus

        if self.on_check:
            self.on_check()
        return GmailStatus(True, self.ready, "sender@example.test", "sender@example.test", self.detail)

    def send(self, *, recipient: str, subject: str, body: str):  # type: ignore[no-untyped-def]
        self.sent.append({"recipient": recipient, "subject": subject, "body": body})
        if self.error:
            raise self.error
        return self.result

import datetime as dt
from typing import Any

from django.utils import timezone

from apps.invoices.tests.factories import HEADER, FakeSheetsClient, row
from integrations.gemini import GeminiError


def iso(days_from_today: int) -> str:
    return (timezone.localdate() + dt.timedelta(days=days_from_today)).isoformat()


def sheet(*rows: list[Any]) -> FakeSheetsClient:
    return FakeSheetsClient([HEADER, *rows])


def overdue_row(invoice_id: str, days: int = 5, **overrides: Any) -> list[Any]:
    return row(invoice_id=invoice_id, client_email=f"ap-{invoice_id.lower()}@fictional.test", due_date=iso(-days), **overrides)


class RoutingGemini:
    """Fake Gemini that returns a valid draft, or fails for selected invoice IDs."""

    model = "fake-gemini"

    def __init__(self, fail_for: dict[str, GeminiError] | None = None, on_call: Any = None) -> None:
        self.fail_for = fail_for or {}
        self.on_call = on_call
        self.calls: list[str] = []

    def generate_json(self, *, system_instruction: str, prompt: str, response_schema: dict[str, Any]) -> dict[str, Any]:
        invoice_id = prompt.split('"invoice_number": "')[1].split('"')[0]
        self.calls.append(invoice_id)
        if self.on_call:
            self.on_call(invoice_id)
        if invoice_id in self.fail_for:
            raise self.fail_for[invoice_id]
        tone = response_schema["properties"]["tone"]["enum"][0]
        return {
            "subject": f"Reminder: invoice {invoice_id}",
            "body": f"Hello,\n\nThis is a reminder that invoice {invoice_id} is overdue.\n\nAccounts Receivable Team",
            "tone": tone,
            "reasoning": "The invoice is overdue.",
            "recommended_action": "Review and approve.",
        }


def follow_up_for(external_id: str, status: str, **overrides: Any):  # type: ignore[no-untyped-def]
    from apps.followups.eligibility import invoice_snapshot
    from apps.followups.models import FollowUp
    from apps.invoices.models import Invoice

    invoice = Invoice.objects.get(external_id=external_id)
    fields: dict[str, Any] = {
        "invoice": invoice,
        "subject": f"Reminder: invoice {external_id}",
        "body": "Hello, this is a reminder that your invoice is overdue.",
        "tone": "FRIENDLY",
        "reasoning": "Overdue.",
        "recommended_action": "Approve.",
        "status": status,
        "invoice_snapshot": invoice_snapshot(invoice),
    }
    fields.update(overrides)
    return FollowUp.objects.create(**fields)

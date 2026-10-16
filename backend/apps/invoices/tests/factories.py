import datetime as dt
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.invoices.models import Invoice
from apps.invoices.sheet_parser import EXPECTED_HEADERS
from apps.invoices.sheets_client import SheetData, SheetsFetchError

HEADER = list(EXPECTED_HEADERS)


def row(
    invoice_id: Any = "INV-001",
    client_name: Any = "Acme Ltd",
    client_email: Any = "billing@acme.test",
    amount: Any = 1250,
    currency: Any = "USD",
    due_date: Any = "2026-01-15",
    status: Any = "UNPAID",
) -> list[Any]:
    return [invoice_id, client_name, client_email, amount, currency, due_date, status]


def make_invoice(**overrides: Any) -> Invoice:
    fields: dict[str, Any] = {
        "external_id": "INV-001",
        "client_name": "Acme Ltd",
        "client_email": "billing@acme.test",
        "amount": Decimal("1250.00"),
        "currency": "USD",
        "due_date": timezone.localdate(),
        "status": Invoice.Status.UNPAID,
        "last_synced_at": timezone.now(),
    }
    fields.update(overrides)
    return Invoice.objects.create(**fields)


def days_from_today(days: int) -> dt.date:
    return timezone.localdate() + dt.timedelta(days=days)


class FakeSheetsClient:
    """Stands in for GoogleSheetsClient; never touches the network."""

    def __init__(self, values: list[list[Any]] | None = None, error: str | None = None) -> None:
        self.values = values or []
        self.error = error
        self.calls = 0

    def fetch_values(self) -> SheetData:
        self.calls += 1
        if self.error:
            raise SheetsFetchError(self.error)
        return SheetData(values=self.values, start_row=1)

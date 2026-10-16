"""
Validates raw spreadsheet values and converts them into invoice records.

Pure functions only: no database access and no Google API calls.
"""

import datetime as dt
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .models import Invoice

EXPECTED_HEADERS = (
    "invoice_id",
    "client_name",
    "client_email",
    "amount",
    "currency",
    "due_date",
    "status",
)

MAX_AMOUNT = Decimal("9999999999.99")  # max_digits=12, decimal_places=2
SHEETS_EPOCH = dt.date(1899, 12, 30)  # Day zero for Google Sheets date serial numbers.
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class InvoiceRecord:
    row: int
    external_id: str
    client_name: str
    client_email: str
    amount: Decimal
    currency: str
    due_date: dt.date
    status: str


@dataclass(frozen=True)
class RowError:
    row: int | None
    field: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __str__(self) -> str:
        location = f"Row {self.row}" if self.row is not None else "Sheet"
        if self.field:
            location += f" [{self.field}]"
        return f"{location}: {self.message}"


@dataclass
class ParseResult:
    records: list[InvoiceRecord] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    # Nonempty data rows (valid or not).
    rows_processed: int = 0
    # Nonempty data rows with at least one error.
    rejected_rows: int = 0


def parse_sheet(values: list[list[Any]], start_row: int = 1) -> ParseResult:
    result = ParseResult()

    if not values or _is_empty(values[0]):
        result.errors.append(RowError(start_row, None, "Header row is missing."))
        return result

    columns, header_errors = _parse_header(values[0], start_row)
    if header_errors:
        result.errors.extend(header_errors)
        return result

    first_seen: dict[str, int] = {}
    for offset, raw_row in enumerate(values[1:], start=1):
        row_number = start_row + offset
        if _is_empty(raw_row):
            continue

        result.rows_processed += 1
        cells = {name: _cell(raw_row, index) for name, index in columns.items()}
        record, errors = _parse_row(cells, row_number)

        if record is not None:
            if record.external_id in first_seen:
                errors.append(
                    RowError(
                        row_number,
                        "invoice_id",
                        f"Duplicate invoice_id '{record.external_id}' "
                        f"(first seen on row {first_seen[record.external_id]}).",
                    )
                )
            else:
                first_seen[record.external_id] = row_number

        if errors:
            result.rejected_rows += 1
            result.errors.extend(errors)
        elif record is not None:
            result.records.append(record)

    return result


def _parse_header(header: list[Any], row: int) -> tuple[dict[str, int], list[RowError]]:
    names = [_text(value).lower() for value in header]
    errors: list[RowError] = []
    columns: dict[str, int] = {}

    for expected in EXPECTED_HEADERS:
        positions = [index for index, name in enumerate(names) if name == expected]
        if not positions:
            errors.append(RowError(row, expected, f"Required column '{expected}' is missing."))
        elif len(positions) > 1:
            errors.append(RowError(row, expected, f"Column '{expected}' appears more than once."))
        else:
            columns[expected] = positions[0]

    return columns, errors


def _parse_row(cells: dict[str, Any], row: int) -> tuple[InvoiceRecord | None, list[RowError]]:
    errors: list[RowError] = []

    def fail(field_name: str, message: str) -> None:
        errors.append(RowError(row, field_name, message))

    external_id = _identifier(cells["invoice_id"])
    if not external_id:
        fail("invoice_id", "invoice_id is required.")
    elif len(external_id) > 100:
        fail("invoice_id", "invoice_id must be at most 100 characters.")

    client_name = _text(cells["client_name"])
    if not client_name:
        fail("client_name", "client_name is required.")
    elif len(client_name) > 255:
        fail("client_name", "client_name must be at most 255 characters.")

    client_email = _text(cells["client_email"])
    if not client_email:
        fail("client_email", "client_email is required.")
    else:
        try:
            validate_email(client_email)
        except ValidationError:
            fail("client_email", f"'{client_email}' is not a valid email address.")

    amount = _parse_amount(cells["amount"], fail)

    currency = _text(cells["currency"]).upper()
    if not currency:
        fail("currency", "currency is required.")
    elif not CURRENCY_CODE.match(currency):
        fail("currency", f"'{currency}' is not a three-letter currency code.")

    due_date = _parse_date(cells["due_date"], fail)

    status = _text(cells["status"]).upper()
    if not status:
        fail("status", "status is required.")
    elif status not in Invoice.Status.values:
        fail("status", f"status must be one of {', '.join(Invoice.Status.values)}; got '{status}'.")

    if errors or amount is None or due_date is None:
        return None, errors

    return (
        InvoiceRecord(
            row=row,
            external_id=external_id,
            client_name=client_name,
            client_email=client_email.lower(),
            amount=amount,
            currency=currency,
            due_date=due_date,
            status=status,
        ),
        errors,
    )


def _parse_amount(value: Any, fail: Any) -> Decimal | None:
    if _is_blank(value):
        fail("amount", "amount is required.")
        return None
    if isinstance(value, bool):
        fail("amount", "amount must be a number.")
        return None
    try:
        # str() first so floats such as 1200.5 become Decimal("1200.5"), not binary noise.
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        fail("amount", f"'{value}' is not a valid number (use digits only, e.g. 1250.00).")
        return None
    if not amount.is_finite():
        fail("amount", "amount must be a finite number.")
        return None
    if amount <= 0:
        fail("amount", "amount must be greater than zero.")
        return None
    if amount != amount.quantize(Decimal("0.01")):
        fail("amount", "amount must have at most two decimal places.")
        return None
    if amount > MAX_AMOUNT:
        fail("amount", "amount is too large.")
        return None
    return amount.quantize(Decimal("0.01"))


def _parse_date(value: Any, fail: Any) -> dt.date | None:
    if _is_blank(value):
        fail("due_date", "due_date is required.")
        return None
    # Date-formatted cells arrive as serial numbers (days since 1899-12-30).
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value != int(value) or not 1 <= value <= 2958465:  # 2958465 == 9999-12-31
            fail("due_date", f"'{value}' is not a valid date.")
            return None
        return SHEETS_EPOCH + dt.timedelta(days=int(value))
    text = _text(value)
    if ISO_DATE.match(text):
        try:
            return dt.date.fromisoformat(text)
        except ValueError:
            pass
    fail("due_date", f"'{text}' is not a valid date (use YYYY-MM-DD).")
    return None


def _cell(row: list[Any], index: int) -> Any:
    # The Sheets API omits trailing empty cells.
    return row[index] if index < len(row) else ""


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _is_empty(row: list[Any]) -> bool:
    return all(_is_blank(value) for value in row)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _identifier(value: Any) -> str:
    # A numeric invoice_id cell (e.g. 1001) arrives as 1001 or 1001.0.
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _text(value)

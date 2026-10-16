import datetime as dt
from decimal import Decimal

from django.test import SimpleTestCase

from apps.invoices.sheet_parser import parse_sheet

from .factories import HEADER, row


def errors_for(result, field: str) -> list:  # type: ignore[no-untyped-def]
    return [error for error in result.errors if error.field == field]


class HeaderValidationTests(SimpleTestCase):
    def test_empty_sheet_is_rejected(self) -> None:
        result = parse_sheet([])

        self.assertEqual(len(result.errors), 1)
        self.assertIn("Header row is missing", result.errors[0].message)

    def test_missing_required_column_is_rejected(self) -> None:
        header = [name for name in HEADER if name != "client_email"]

        result = parse_sheet([header, ["INV-1", "Acme", 100, "USD", "2026-01-01", "UNPAID"]])

        self.assertEqual([(e.row, e.field) for e in result.errors], [(1, "client_email")])
        self.assertEqual(result.records, [])

    def test_duplicate_column_is_rejected(self) -> None:
        result = parse_sheet([HEADER + ["amount"], row()])

        self.assertEqual([e.field for e in result.errors], ["amount"])

    def test_header_is_case_insensitive_and_order_independent(self) -> None:
        header = [name.upper() for name in reversed(HEADER)]

        result = parse_sheet([header, list(reversed(row()))])

        self.assertEqual(result.errors, [])
        self.assertEqual(result.records[0].external_id, "INV-001")


class RowValidationTests(SimpleTestCase):
    def test_valid_row_is_parsed(self) -> None:
        result = parse_sheet([HEADER, row(amount="1250.5", currency="usd", status="paid")])

        self.assertEqual(result.errors, [])
        record = result.records[0]
        self.assertEqual(record.amount, Decimal("1250.50"))
        self.assertEqual(record.currency, "USD")
        self.assertEqual(record.status, "PAID")
        self.assertEqual(record.due_date, dt.date(2026, 1, 15))
        self.assertEqual(record.row, 2)

    def test_sheets_date_serial_number_is_parsed(self) -> None:
        # 46037 is the Google Sheets serial number for 2026-01-15.
        result = parse_sheet([HEADER, row(due_date=46037)])

        self.assertEqual(result.records[0].due_date, dt.date(2026, 1, 15))

    def test_numeric_invoice_id_is_normalized(self) -> None:
        result = parse_sheet([HEADER, row(invoice_id=1001.0)])

        self.assertEqual(result.records[0].external_id, "1001")

    def test_empty_rows_are_skipped(self) -> None:
        result = parse_sheet([HEADER, [], ["", "  "], row(), [None]])

        self.assertEqual(result.errors, [])
        self.assertEqual(result.rows_processed, 1)
        self.assertEqual(result.records[0].row, 4)

    def test_invalid_amounts_are_rejected(self) -> None:
        for bad in (0, -10, "abc", "$1,200", "10.005", "NaN", True):
            with self.subTest(amount=bad):
                result = parse_sheet([HEADER, row(amount=bad)])

                self.assertEqual(len(errors_for(result, "amount")), 1)
                self.assertEqual(result.errors[0].row, 2)
                self.assertEqual(result.records, [])

    def test_invalid_dates_are_rejected(self) -> None:
        for bad in ("2026-02-30", "15/01/2026", "tomorrow", 1.5, -3):
            with self.subTest(due_date=bad):
                result = parse_sheet([HEADER, row(due_date=bad)])

                self.assertEqual(len(errors_for(result, "due_date")), 1)
                self.assertEqual(result.records, [])

    def test_missing_required_fields_are_reported_with_row_numbers(self) -> None:
        values = [HEADER, row(), row(invoice_id="INV-002", client_name="", client_email=" ", status="")]

        result = parse_sheet(values)

        self.assertEqual(
            sorted((e.row, e.field) for e in result.errors),
            [(3, "client_email"), (3, "client_name"), (3, "status")],
        )
        self.assertEqual(result.rejected_rows, 1)
        self.assertEqual(result.rows_processed, 2)

    def test_trailing_missing_cells_are_reported_as_missing(self) -> None:
        result = parse_sheet([HEADER, ["INV-1", "Acme", "a@b.test", 10, "USD"]])

        self.assertEqual({e.field for e in result.errors}, {"due_date", "status"})

    def test_invalid_email_currency_and_status_are_rejected(self) -> None:
        result = parse_sheet([HEADER, row(client_email="not-an-email", currency="US", status="OPEN")])

        self.assertEqual({e.field for e in result.errors}, {"client_email", "currency", "status"})

    def test_duplicate_invoice_ids_are_rejected(self) -> None:
        values = [HEADER, row(invoice_id="INV-7"), row(), row(invoice_id="INV-7")]

        result = parse_sheet(values)

        self.assertEqual(len(result.errors), 1)
        error = result.errors[0]
        self.assertEqual((error.row, error.field), (4, "invoice_id"))
        self.assertIn("first seen on row 2", error.message)

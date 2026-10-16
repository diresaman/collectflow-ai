from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase

from apps.invoices.models import AutomationRun, Invoice
from apps.invoices.sync import sync_invoices

from .factories import HEADER, FakeSheetsClient, make_invoice, row


class SyncTests(TestCase):
    def test_initial_sync_creates_invoices(self) -> None:
        client = FakeSheetsClient([HEADER, row("INV-1"), row("INV-2", amount="99.90", currency="EUR")])

        run = sync_invoices(client=client)

        self.assertEqual(run.status, AutomationRun.Status.SUCCEEDED)
        self.assertEqual(
            (run.records_processed, run.created_count, run.updated_count, run.unchanged_count),
            (2, 2, 0, 0),
        )
        self.assertIsNotNone(run.completed_at)
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertEqual(Invoice.objects.get(external_id="INV-2").amount, Decimal("99.90"))

    def test_repeated_sync_does_not_duplicate(self) -> None:
        client = FakeSheetsClient([HEADER, row("INV-1"), row("INV-2")])
        sync_invoices(client=client)
        first_synced_at = Invoice.objects.get(external_id="INV-1").last_synced_at

        run = sync_invoices(client=client)

        self.assertEqual((run.created_count, run.updated_count, run.unchanged_count), (0, 0, 2))
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertGreater(Invoice.objects.get(external_id="INV-1").last_synced_at, first_synced_at)

    def test_existing_invoice_is_updated_by_external_id(self) -> None:
        original = make_invoice(external_id="INV-1", amount=Decimal("100.00"), client_name="Old Name")

        run = sync_invoices(client=FakeSheetsClient([HEADER, row("INV-1", client_name="New Name", amount=150)]))

        self.assertEqual((run.created_count, run.updated_count), (0, 1))
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.pk, original.pk)
        self.assertEqual(invoice.client_name, "New Name")
        self.assertEqual(invoice.amount, Decimal("150.00"))

    def test_paid_status_from_sheet_updates_local_invoice(self) -> None:
        make_invoice(external_id="INV-1", status=Invoice.Status.UNPAID)

        run = sync_invoices(client=FakeSheetsClient([HEADER, row("INV-1", status="Paid")]))

        self.assertEqual(run.updated_count, 1)
        invoice = Invoice.objects.get(external_id="INV-1")
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertFalse(invoice.is_overdue)

    def test_invoices_missing_from_sheet_are_kept(self) -> None:
        make_invoice(external_id="LEGACY-1")

        sync_invoices(client=FakeSheetsClient([HEADER, row("INV-1")]))

        self.assertTrue(Invoice.objects.filter(external_id="LEGACY-1").exists())
        self.assertEqual(Invoice.objects.count(), 2)

    def test_google_api_failure_records_failed_run(self) -> None:
        make_invoice(external_id="INV-1", client_name="Unchanged")
        client = FakeSheetsClient(error="Google Sheets denied access (HTTP 403).")

        run = sync_invoices(client=client)

        self.assertEqual(run.status, AutomationRun.Status.FAILED)
        self.assertIn("HTTP 403", run.error_summary)
        self.assertEqual(run.created_count + run.updated_count, 0)
        self.assertEqual(Invoice.objects.get().client_name, "Unchanged")

    def test_unexpected_fetch_exception_records_failed_run(self) -> None:
        client = mock.Mock()
        client.fetch_values.side_effect = RuntimeError("boom")

        with self.assertLogs("apps.invoices.sync", level="ERROR"):
            run = sync_invoices(client=client)

        self.assertEqual(run.status, AutomationRun.Status.FAILED)
        self.assertNotIn("boom", run.error_summary)

    def test_invalid_row_rejects_entire_batch(self) -> None:
        make_invoice(external_id="INV-1", client_name="Original", amount=Decimal("100.00"))
        values = [
            HEADER,
            row("INV-1", client_name="Changed", amount=500),  # valid update
            row("INV-2"),  # valid create
            row("INV-3", amount=-5),  # invalid
        ]

        run = sync_invoices(client=FakeSheetsClient(values))

        self.assertEqual(run.status, AutomationRun.Status.FAILED)
        self.assertEqual((run.records_processed, run.rejected_count), (3, 1))
        self.assertEqual((run.created_count, run.updated_count), (0, 0))
        self.assertEqual(run.errors, [{"row": 4, "field": "amount", "message": "amount must be greater than zero."}])
        self.assertIn("Row 4 [amount]", run.error_summary)
        invoice = Invoice.objects.get()
        self.assertEqual((invoice.client_name, invoice.amount), ("Original", Decimal("100.00")))

    def test_duplicate_ids_reject_batch(self) -> None:
        run = sync_invoices(client=FakeSheetsClient([HEADER, row("INV-1"), row("INV-1")]))

        self.assertEqual(run.status, AutomationRun.Status.FAILED)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_missing_configuration_fails_without_calling_google(self) -> None:
        with self.settings(GOOGLE_SHEETS_SPREADSHEET_ID="", GOOGLE_APPLICATION_CREDENTIALS=""):
            run = sync_invoices()

        self.assertEqual(run.status, AutomationRun.Status.FAILED)
        self.assertIn("GOOGLE_SHEETS_SPREADSHEET_ID", run.error_summary)


class SyncTransactionTests(TransactionTestCase):
    def test_google_request_runs_outside_a_transaction(self) -> None:
        observed: list[bool] = []

        class ObservingClient(FakeSheetsClient):
            def fetch_values(self):  # type: ignore[no-untyped-def]
                observed.append(connection.in_atomic_block)
                return super().fetch_values()

        run = sync_invoices(client=ObservingClient([HEADER, row()]))

        self.assertEqual(observed, [False])
        self.assertEqual(run.status, AutomationRun.Status.SUCCEEDED)

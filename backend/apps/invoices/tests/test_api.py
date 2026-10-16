from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.invoices.models import AutomationRun, Invoice

from .factories import HEADER, FakeSheetsClient, days_from_today, make_invoice, row

TOKEN = "test-operator-token"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


@override_settings(OPERATOR_TOKEN=TOKEN)
class AccessControlTests(TestCase):
    def business_requests(self) -> list[tuple[str, str]]:
        invoice = make_invoice()
        return [
            ("post", reverse("sync")),
            ("get", reverse("invoice-list")),
            ("get", reverse("invoice-detail", args=[invoice.pk])),
            ("get", reverse("dashboard")),
            ("get", reverse("automation-run-list")),
        ]

    def test_business_endpoints_require_token(self) -> None:
        for method, url in self.business_requests():
            with self.subTest(url=url):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 401)

    def test_wrong_token_is_rejected(self) -> None:
        for method, url in self.business_requests():
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, HTTP_AUTHORIZATION="Bearer wrong")
                self.assertEqual(response.status_code, 401)

    @override_settings(OPERATOR_TOKEN="")
    def test_unconfigured_token_rejects_everything(self) -> None:
        response = self.client.get(reverse("dashboard"), HTTP_AUTHORIZATION="Bearer ")
        self.assertEqual(response.status_code, 401)

    def test_sync_does_not_run_without_token(self) -> None:
        with mock.patch("apps.invoices.views.sync_invoices") as sync:
            self.client.post(reverse("sync"))
        sync.assert_not_called()

    def test_health_stays_public(self) -> None:
        self.assertEqual(self.client.get(reverse("health")).status_code, 200)


@override_settings(OPERATOR_TOKEN=TOKEN)
class InvoiceEndpointTests(TestCase):
    def test_list_and_detail(self) -> None:
        invoice = make_invoice(due_date=days_from_today(-3))

        listing = self.client.get(reverse("invoice-list"), **AUTH)
        detail = self.client.get(reverse("invoice-detail", args=[invoice.pk]), **AUTH)

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()), 1)
        body = detail.json()
        self.assertEqual(body["id"], str(invoice.pk))
        self.assertEqual(body["amount"], "1250.00")
        self.assertEqual((body["is_overdue"], body["days_overdue"]), (True, 3))

    def test_unknown_invoice_returns_404(self) -> None:
        url = reverse("invoice-detail", args=["00000000-0000-0000-0000-000000000000"])
        self.assertEqual(self.client.get(url, **AUTH).status_code, 404)


@override_settings(OPERATOR_TOKEN=TOKEN)
class DashboardTests(TestCase):
    def test_totals_are_grouped_by_currency(self) -> None:
        make_invoice(external_id="U1", amount=Decimal("100.00"), currency="USD", due_date=days_from_today(-2))
        make_invoice(external_id="U2", amount=Decimal("50.25"), currency="USD", due_date=days_from_today(5))
        make_invoice(external_id="E1", amount=Decimal("200.00"), currency="EUR", due_date=days_from_today(-1))
        make_invoice(external_id="E2", amount=Decimal("80.00"), currency="EUR", due_date=days_from_today(0))
        make_invoice(
            external_id="P1", amount=Decimal("999.00"), currency="USD",
            due_date=days_from_today(-10), status=Invoice.Status.PAID,
        )

        body = self.client.get(reverse("dashboard"), **AUTH).json()

        self.assertEqual((body["total_invoices"], body["unpaid_invoices"], body["overdue_invoices"]), (5, 4, 2))
        self.assertEqual(
            body["outstanding_amounts_by_currency"],
            [{"currency": "EUR", "amount": "280.00"}, {"currency": "USD", "amount": "150.25"}],
        )
        self.assertEqual(
            body["overdue_amounts_by_currency"],
            [{"currency": "EUR", "amount": "200.00"}, {"currency": "USD", "amount": "100.00"}],
        )

    def test_empty_dashboard(self) -> None:
        body = self.client.get(reverse("dashboard"), **AUTH).json()

        self.assertEqual(body["total_invoices"], 0)
        self.assertEqual(body["outstanding_amounts_by_currency"], [])


@override_settings(OPERATOR_TOKEN=TOKEN)
class SyncEndpointTests(TestCase):
    def post_sync(self, client: FakeSheetsClient):  # type: ignore[no-untyped-def]
        with mock.patch("apps.invoices.sync.GoogleSheetsClient.from_settings", return_value=client):
            return self.client.post(reverse("sync"), **AUTH)

    def test_successful_sync(self) -> None:
        response = self.post_sync(FakeSheetsClient([HEADER, row()]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "SUCCEEDED")
        self.assertEqual(response.json()["created_count"], 1)

    def test_invalid_sheet_returns_422(self) -> None:
        response = self.post_sync(FakeSheetsClient([HEADER, row(amount=0)]))

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"][0]["row"], 2)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_google_failure_returns_502_and_is_not_reported_as_success(self) -> None:
        response = self.post_sync(FakeSheetsClient(error="Could not reach the Google Sheets API."))

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["status"], "FAILED")

    def test_automation_runs_are_listed_newest_first(self) -> None:
        self.post_sync(FakeSheetsClient([HEADER, row()]))
        self.post_sync(FakeSheetsClient(error="down"))

        runs = self.client.get(reverse("automation-run-list"), **AUTH).json()

        self.assertEqual([run["status"] for run in runs], ["FAILED", "SUCCEEDED"])
        self.assertEqual(AutomationRun.objects.count(), 2)

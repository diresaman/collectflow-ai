from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from apps.accounts.testing import SessionApiTestCase, make_user, run_jobs
from apps.automation.models import OutboxJob
from apps.invoices.models import AutomationRun, Invoice
from apps.timeline.models import TimelineEvent

from .factories import HEADER, FakeSheetsClient, days_from_today, make_invoice, row

AUTH: dict[str, str] = {}


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

    def test_business_endpoints_require_a_session(self) -> None:
        for method, url in self.business_requests():
            with self.subTest(url=url):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 401)

    def test_users_without_a_role_are_refused(self) -> None:
        self.client.force_login(make_user(username="no-role", role=None))
        for method, url in self.business_requests():
            with self.subTest(url=url):
                self.assertEqual(getattr(self.client, method)(url).status_code, 403)

    def test_sync_is_not_queued_without_a_session(self) -> None:
        self.client.post(reverse("sync"))
        self.assertFalse(OutboxJob.objects.exists())
        self.assertFalse(AutomationRun.objects.exists())

    def test_health_stays_public(self) -> None:
        self.assertEqual(self.client.get(reverse("health")).status_code, 200)


class InvoiceEndpointTests(SessionApiTestCase):
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


class DashboardTests(SessionApiTestCase):
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


class SyncEndpointTests(SessionApiTestCase):
    def post_sync(self, client: FakeSheetsClient):  # type: ignore[no-untyped-def]
        """POST /sync/, then let the worker run the queued job; returns (response, finished run)."""
        response = self.client.post(reverse("sync"), **AUTH)
        with mock.patch("apps.invoices.sync.GoogleSheetsClient.from_settings", return_value=client):
            run_jobs()
        run = self.client.get(reverse("automation-run-detail", args=[response.json()["run_id"]]), **AUTH).json()
        return response, run

    def test_sync_is_queued_and_returns_202(self) -> None:
        with mock.patch("apps.invoices.sync.GoogleSheetsClient.from_settings", side_effect=AssertionError):
            response = self.client.post(reverse("sync"), **AUTH)

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["run"]["status"], "RUNNING")
        job = OutboxJob.objects.get(pk=body["job_id"])
        self.assertEqual((job.kind, job.status, job.payload), ("invoices.sync", "PENDING", {"run_id": body["run_id"]}))

    def test_successful_sync(self) -> None:
        response, run = self.post_sync(FakeSheetsClient([HEADER, row()]))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(run["status"], "SUCCEEDED")
        self.assertEqual(run["created_count"], 1)
        event = TimelineEvent.objects.get(type="INVOICE_CREATED")
        self.assertEqual((event.actor_type, event.actor), ("USER", self.user))

    def test_invalid_sheet_is_rejected_with_row_errors(self) -> None:
        _, run = self.post_sync(FakeSheetsClient([HEADER, row(amount=0)]))

        self.assertEqual(run["status"], "FAILED")
        self.assertEqual(run["errors"][0]["row"], 2)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_google_failure_is_not_reported_as_success(self) -> None:
        _, run = self.post_sync(FakeSheetsClient(error="Could not reach the Google Sheets API."))

        self.assertEqual(run["status"], "FAILED")
        self.assertEqual(OutboxJob.objects.get().status, "SUCCEEDED")  # the job ran; the sync itself failed

    def test_automation_runs_are_listed_newest_first(self) -> None:
        self.post_sync(FakeSheetsClient([HEADER, row()]))
        self.post_sync(FakeSheetsClient(error="down"))

        runs = self.client.get(reverse("automation-run-list"), **AUTH).json()

        self.assertEqual([run["status"] for run in runs], ["FAILED", "SUCCEEDED"])
        self.assertEqual(AutomationRun.objects.count(), 2)

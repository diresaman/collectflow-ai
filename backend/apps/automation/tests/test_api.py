from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.automation.lease import Lease
from apps.invoices.models import AutomationRun
from apps.invoices.tests.factories import FakeSheetsClient

from .helpers import RoutingGemini, overdue_row, sheet

TOKEN = "test-operator-token"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


@override_settings(OPERATOR_TOKEN=TOKEN)
class AutomationApiTests(TestCase):
    def post_run(self, sheets: FakeSheetsClient, gemini: RoutingGemini | None = None):  # type: ignore[no-untyped-def]
        with mock.patch("apps.invoices.sync.GoogleSheetsClient.from_settings", return_value=sheets), \
                mock.patch("apps.followups.services.GeminiClient.from_settings", return_value=gemini or RoutingGemini()), \
                mock.patch("integrations.gmail.GmailClient.send", side_effect=AssertionError("no Gmail")):
            return self.client.post(reverse("automation-run"), **AUTH)

    def test_manual_run_returns_run_with_items(self) -> None:
        response = self.post_run(sheet(overdue_row("INV-1"), overdue_row("INV-2")))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["kind"], body["trigger"], body["status"]), ("AUTOMATION", "MANUAL", "COMPLETED"))
        self.assertEqual(body["drafts_generated"], 2)
        self.assertEqual({item["outcome"] for item in body["items"]}, {"GENERATED"})
        self.assertIsNotNone(body["items"][0]["follow_up_id"])

    def test_sync_failure_returns_502_with_run(self) -> None:
        response = self.post_run(FakeSheetsClient(error="Google Sheets denied access (HTTP 403)."))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["status"], "FAILED")
        self.assertNotIn("Traceback", response.content.decode())

    def test_concurrent_trigger_returns_409(self) -> None:
        lease = Lease.acquire()
        try:
            response = self.post_run(sheet(overdue_row("INV-1")))
        finally:
            lease.release()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "automation_running")

    def test_run_history_and_detail(self) -> None:
        self.post_run(sheet(overdue_row("INV-1")))
        self.post_run(sheet(overdue_row("INV-1")))

        automation = self.client.get(reverse("automation-run-list"), {"kind": "AUTOMATION"}, **AUTH).json()
        syncs = self.client.get(reverse("automation-run-list"), {"kind": "SYNC"}, **AUTH).json()

        self.assertEqual([(run["drafts_generated"], run["invoices_skipped"]) for run in automation], [(0, 1), (1, 0)])
        self.assertEqual(len(syncs), 2)
        self.assertEqual({run["kind"] for run in syncs}, {"SYNC"})
        detail = self.client.get(reverse("automation-run-detail", args=[automation[0]["id"]]), **AUTH).json()
        self.assertEqual(detail["items"][0]["reason_code"], "draft_awaiting_review")
        self.assertEqual(detail["sync_run"], automation[0]["sync_run"])
        self.assertEqual(self.client.get(reverse("automation-run-detail", args=[9999]), **AUTH).status_code, 404)

    def test_endpoints_require_token(self) -> None:
        gemini = RoutingGemini()
        with mock.patch("apps.followups.services.GeminiClient.from_settings", return_value=gemini), \
                mock.patch("apps.invoices.sync.GoogleSheetsClient.from_settings", side_effect=AssertionError):
            for method, url in [
                ("post", reverse("automation-run")),
                ("get", reverse("automation-run-list")),
                ("get", reverse("automation-run-detail", args=[1])),
            ]:
                with self.subTest(url=url):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 401)
                    self.assertEqual(getattr(self.client, method)(url, HTTP_AUTHORIZATION="Bearer x").status_code, 401)
        self.assertEqual(gemini.calls, [])
        self.assertFalse(AutomationRun.objects.exists())

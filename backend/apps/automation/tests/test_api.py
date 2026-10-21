from unittest import mock

from django.urls import reverse

from apps.accounts.testing import SessionApiTestCase, run_jobs
from apps.automation.lease import Lease
from apps.invoices.models import AutomationRun
from apps.invoices.tests.factories import FakeSheetsClient

from .helpers import RoutingGemini, overdue_row, sheet

AUTH: dict[str, str] = {}


class AutomationApiTests(SessionApiTestCase):
    def post_run(self, sheets: FakeSheetsClient, gemini: RoutingGemini | None = None):  # type: ignore[no-untyped-def]
        """POST /automation/run/ (queues the run), then lets the worker execute it."""
        with mock.patch("apps.invoices.sync.GoogleSheetsClient.from_settings", return_value=sheets), \
                mock.patch("apps.followups.services.GeminiClient.from_settings", return_value=gemini or RoutingGemini()), \
                mock.patch("integrations.gmail.GmailClient.send", side_effect=AssertionError("no Gmail")):
            response = self.client.post(reverse("automation-run"), **AUTH)
            run_jobs()
        return response

    def finished_run(self, response) -> dict:  # type: ignore[no-untyped-def,type-arg]
        job = self.client.get(reverse("job-detail", args=[response.json()["job_id"]]), **AUTH).json()
        self.assertEqual(job["status"], "SUCCEEDED")
        return self.client.get(reverse("automation-run-detail", args=[job["result"]["run_id"]]), **AUTH).json()

    def test_manual_run_is_queued_and_returns_202(self) -> None:
        with mock.patch("apps.invoices.sync.GoogleSheetsClient.from_settings", side_effect=AssertionError):
            response = self.client.post(reverse("automation-run"), **AUTH)
            again = self.client.post(reverse("automation-run"), **AUTH)
        self.assertEqual(response.status_code, 202)
        self.assertEqual((again.status_code, again.json()["code"]), (409, "automation_running"))
        self.assertFalse(AutomationRun.objects.exists())

    def test_manual_run_returns_run_with_items(self) -> None:
        response = self.post_run(sheet(overdue_row("INV-1"), overdue_row("INV-2")))

        self.assertEqual(response.status_code, 202)
        body = self.finished_run(response)
        self.assertEqual((body["kind"], body["trigger"], body["status"]), ("AUTOMATION", "MANUAL", "COMPLETED"))
        self.assertEqual(body["drafts_generated"], 2)
        self.assertEqual({item["outcome"] for item in body["items"]}, {"GENERATED"})
        self.assertIsNotNone(body["items"][0]["follow_up_id"])

    def test_sync_failure_is_reported_on_the_run(self) -> None:
        response = self.post_run(FakeSheetsClient(error="Google Sheets denied access (HTTP 403)."))
        run = self.finished_run(response)
        self.assertEqual(run["status"], "FAILED")
        self.assertNotIn("Traceback", str(run))

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

    def test_endpoints_require_a_session(self) -> None:
        self.client.logout()
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
        self.assertEqual(gemini.calls, [])
        self.assertFalse(AutomationRun.objects.exists())

import uuid
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import TestCase
from django.urls import reverse

from apps.accounts.testing import SessionApiTestCase, run_jobs
from apps.automation.models import OutboxJob
from apps.followups.models import FollowUp
from apps.invoices.models import Invoice
from apps.timeline.models import TimelineEvent
from integrations.gemini import GeminiUnavailable

from .helpers import FakeGemini, ai_output, make_follow_up, overdue_invoice

AUTH: dict[str, str] = {}


def patch_gemini(gemini: FakeGemini):  # type: ignore[no-untyped-def]
    return mock.patch("apps.followups.services.GeminiClient.from_settings", return_value=gemini)


class AnalyzeEndpointTests(SessionApiTestCase):
    def analyze(self, invoice_id: object, **data: object):  # type: ignore[no-untyped-def]
        return self.client.post(
            reverse("invoice-analyze", args=[invoice_id]), data=data, content_type="application/json", **AUTH
        )

    def job(self, response) -> dict:  # type: ignore[no-untyped-def,type-arg]
        return self.client.get(reverse("job-detail", args=[response.json()["job_id"]]), **AUTH).json()

    def test_generation_is_queued_and_the_job_reports_the_draft(self) -> None:
        invoice = overdue_invoice(days=10)
        gemini = FakeGemini(ai_output("PROFESSIONAL"))
        with patch_gemini(gemini):
            response = self.analyze(invoice.pk)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(gemini.calls, [])  # no AI call inside the web request
            self.assertEqual(self.job(response)["status"], "PENDING")
            run_jobs()

        job = self.job(response)
        self.assertEqual(job["status"], "SUCCEEDED")
        follow_up = FollowUp.objects.get(pk=job["result"]["follow_up_id"])
        self.assertEqual((follow_up.status, follow_up.tone, follow_up.prompt_version), ("DRAFT", "PROFESSIONAL", "draft-v1"))
        event = TimelineEvent.objects.get(type="DRAFT_GENERATED")
        self.assertEqual((event.actor, event.invoice_id, event.payload["follow_up_id"]), (self.user, invoice.pk, str(follow_up.pk)))

    def test_status_codes(self) -> None:
        paid = overdue_invoice(external_id="PAID", status=Invoice.Status.PAID)
        self.assertEqual(self.analyze(paid.pk).status_code, 409)
        self.assertEqual(self.analyze(uuid.uuid4()).status_code, 404)
        self.assertEqual(self.analyze(paid.pk, regenerate="yes").status_code, 400)
        self.assertFalse(OutboxJob.objects.exists())

    def test_duplicate_returns_409_with_existing_id(self) -> None:
        invoice = overdue_invoice()
        existing = make_follow_up(invoice)

        response = self.analyze(invoice.pk)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["follow_up_id"], str(existing.pk))

    def test_second_request_while_queued_returns_409(self) -> None:
        invoice = overdue_invoice()
        self.assertEqual(self.analyze(invoice.pk).status_code, 202)

        response = self.analyze(invoice.pk)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "generation_in_progress")
        self.assertEqual(OutboxJob.objects.count(), 1)

    def test_upstream_failure_is_reported_on_the_job_without_trace(self) -> None:
        invoice = overdue_invoice()
        with patch_gemini(FakeGemini(error=GeminiUnavailable("Gemini did not respond in time. Try again."))):
            response = self.analyze(invoice.pk)
            with self.assertLogs("apps.followups.services"):
                run_jobs()

        job = self.job(response)
        self.assertEqual((job["status"], job["error_code"]), ("FAILED", "ai_unavailable"))
        self.assertEqual((job["result"]["code"], job["result"]["http_status"]), ("ai_unavailable", 503))
        self.assertEqual(job["attempts"], 1)  # never re-run automatically
        self.assertNotIn("Traceback", str(job))
        self.assertTrue(TimelineEvent.objects.filter(type="DRAFT_GENERATION_FAILED").exists())

    def test_invalid_ai_output_fails_the_job_with_502(self) -> None:
        invoice = overdue_invoice()
        with patch_gemini(FakeGemini(ai_output(tone="FIRM"))):
            response = self.analyze(invoice.pk)
            with self.assertLogs("apps.followups.services"):
                run_jobs()
        self.assertEqual(self.job(response)["result"]["http_status"], 502)
        self.assertFalse(FollowUp.objects.exists())


class ReviewEndpointTests(SessionApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.invoice = overdue_invoice()
        self.follow_up = make_follow_up(self.invoice)

    def url(self, name: str) -> str:
        return reverse(name, args=[self.follow_up.pk])

    def patch(self, **data: object):  # type: ignore[no-untyped-def]
        return self.client.patch(self.url("follow-up-detail"), data=data, content_type="application/json", **AUTH)

    def test_list_and_detail(self) -> None:
        listing = self.client.get(reverse("follow-up-list"), {"invoice": str(self.invoice.pk)}, **AUTH)
        detail = self.client.get(self.url("follow-up-detail"), **AUTH)

        self.assertEqual([item["id"] for item in listing.json()], [str(self.follow_up.pk)])
        self.assertEqual(detail.json()["invoice_external_id"], "INV-100")
        self.assertEqual(self.client.get(reverse("follow-up-detail", args=[uuid.uuid4()]), **AUTH).status_code, 404)

    def test_edit_draft(self) -> None:
        response = self.patch(subject="  Updated subject  ", body="Updated body text that is long enough.")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["last_edited_by"], self.user.username)
        self.follow_up.refresh_from_db()
        self.assertEqual(self.follow_up.subject, "Updated subject")
        self.assertEqual(TimelineEvent.objects.get(type="DRAFT_EDITED").payload["fields"], ["subject", "body"])
        self.assertEqual(self.follow_up.body, "Updated body text that is long enough.")

    def test_invalid_edits_return_400(self) -> None:
        cases = [
            {"subject": ""},
            {"subject": "x" * 151},
            {"subject": "two\nlines"},
            {"body": "too short"},
            {"body": "x" * 5001},
            {"status": "APPROVED"},
            {},
        ]
        for data in cases:
            with self.subTest(data=str(data)[:40]):
                self.assertEqual(self.patch(**data).status_code, 400)
        self.follow_up.refresh_from_db()
        self.assertEqual(self.follow_up.status, FollowUp.Status.DRAFT)

    def test_approve(self) -> None:
        response = self.client.post(self.url("follow-up-approve"), **AUTH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "APPROVED")
        self.assertEqual(response.json()["approved_by"], self.user.username)
        self.assertEqual(TimelineEvent.objects.get(type="FOLLOW_UP_APPROVED").actor, self.user)
        self.assertIsNotNone(response.json()["approved_at"])
        self.assertIsNone(response.json()["sent_at"])

    def test_approval_never_sends_email(self) -> None:
        with mock.patch("django.core.mail.EmailMessage.send") as send, mock.patch("smtplib.SMTP") as smtp:
            response = self.client.post(self.url("follow-up-approve"), **AUTH)

        self.assertEqual(response.status_code, 200)
        send.assert_not_called()
        smtp.assert_not_called()
        self.assertEqual(mail.outbox, [])
        self.follow_up.refresh_from_db()
        self.assertIsNone(self.follow_up.sent_at)
        self.assertNotEqual(self.follow_up.status, FollowUp.Status.SENT)

    def test_reject(self) -> None:
        response = self.client.post(self.url("follow-up-reject"), **AUTH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "REJECTED")

    def test_invalid_transitions_return_409(self) -> None:
        self.client.post(self.url("follow-up-approve"), **AUTH)

        self.assertEqual(self.client.post(self.url("follow-up-approve"), **AUTH).status_code, 409)
        response = self.patch(subject="Edited after approval")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "invalid_status_transition")

    def test_unsent_approval_can_be_withdrawn_once(self) -> None:
        self.client.post(self.url("follow-up-approve"), **AUTH)

        withdrawn = self.client.post(self.url("follow-up-reject"), **AUTH)

        self.assertEqual(withdrawn.status_code, 200)
        self.assertEqual(withdrawn.json()["status"], "REJECTED")
        self.assertTrue(TimelineEvent.objects.filter(type="APPROVAL_WITHDRAWN").exists())
        self.assertEqual(self.client.post(self.url("follow-up-reject"), **AUTH).status_code, 409)
        self.follow_up.refresh_from_db()
        self.assertEqual(self.follow_up.subject, "Reminder: invoice INV-100")  # content untouched

    def test_sent_reminder_cannot_be_withdrawn(self) -> None:
        FollowUp.objects.filter(pk=self.follow_up.pk).update(status=FollowUp.Status.SENT)
        self.assertEqual(self.client.post(self.url("follow-up-reject"), **AUTH).status_code, 409)

    def test_rejected_cannot_be_approved(self) -> None:
        self.client.post(self.url("follow-up-reject"), **AUTH)
        self.assertEqual(self.client.post(self.url("follow-up-approve"), **AUTH).status_code, 409)

    def test_stale_snapshot_blocks_approval(self) -> None:
        Invoice.objects.filter(pk=self.invoice.pk).update(amount=Decimal("1500.00"))

        response = self.client.post(self.url("follow-up-approve"), **AUTH)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "stale_invoice")
        self.assertEqual(response.json()["changed_fields"], ["amount"])
        self.follow_up.refresh_from_db()
        self.assertEqual(self.follow_up.status, FollowUp.Status.DRAFT)

    def test_paid_invoice_blocks_approval(self) -> None:
        Invoice.objects.filter(pk=self.invoice.pk).update(status=Invoice.Status.PAID)

        response = self.client.post(self.url("follow-up-approve"), **AUTH)

        self.assertEqual(response.status_code, 409)
        self.assertIn("no longer eligible", response.json()["detail"])


class FollowUpAccessControlTests(TestCase):
    def test_endpoints_require_a_session(self) -> None:
        invoice = overdue_invoice()
        follow_up = make_follow_up(invoice)
        requests = [
            ("post", reverse("invoice-analyze", args=[invoice.pk])),
            ("get", reverse("follow-up-list")),
            ("get", reverse("follow-up-detail", args=[follow_up.pk])),
            ("patch", reverse("follow-up-detail", args=[follow_up.pk])),
            ("post", reverse("follow-up-approve", args=[follow_up.pk])),
            ("post", reverse("follow-up-reject", args=[follow_up.pk])),
        ]
        gemini = FakeGemini(ai_output())
        with patch_gemini(gemini):
            for method, url in requests:
                with self.subTest(method=method, url=url):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 401)
        self.assertEqual(gemini.calls, [])
        self.assertFalse(OutboxJob.objects.exists())
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, FollowUp.Status.DRAFT)

import json
from typing import Any

from django.test import TestCase, override_settings

from apps.accounts.models import Role
from apps.accounts.testing import SessionApiTestCase, make_user
from apps.automation.models import OutboxJob
from apps.followups.eligibility import ineligibility
from apps.inbox import classification, triage
from apps.inbox.models import InboundMessage, ReplyClassification, Task
from apps.invoices.models import ChaseHold
from apps.sequences import engine
from apps.timeline.models import ActorType, TimelineEvent

from .helpers import FakeClassifier, classification as proposal, collectflow_thread
from .test_classification import receive


def classified(label: str, confidence: float = 0.95, text: str = "We will pay Friday.", **extracted: Any) -> InboundMessage:
    reply = receive(text)
    classification.classify(reply.pk, gemini=FakeClassifier(proposal(label, confidence, **extracted)))
    reply.refresh_from_db()
    return reply


def active(reply: InboundMessage) -> list[str]:
    return list(ChaseHold.objects.filter(reply_id=reply.pk, released_at__isnull=True).values_list("reason", flat=True))


class TriageServiceTests(TestCase):
    def setUp(self) -> None:
        self.invoice, _ = collectflow_thread()
        engine.reconcile(actor=ActorType.SYSTEM)
        self.operator = make_user("op", Role.OPERATOR)
        self.approver = make_user("ap", Role.APPROVER)

    def test_operator_confirms_a_promise_and_chasing_stays_held(self) -> None:
        reply = classified("PROMISE_TO_PAY", promised_date="2026-11-01")
        triage.classify(reply.pk, label="PROMISE_TO_PAY", actor=self.operator)
        reply.refresh_from_db()
        self.assertEqual((reply.state, reply.label_source, reply.resolved_by), ("RESOLVED", "HUMAN", self.operator))
        self.assertEqual(active(reply), ["PROMISE_TO_PAY"])
        self.assertEqual(reply.extracted["promised_date"], "2026-11-01")

    def test_operator_cannot_resume_chasing_after_a_dispute(self) -> None:
        reply = classified("DISPUTE", text="This is wrong")
        with self.assertRaises(triage.TriageForbidden):
            triage.classify(reply.pk, label="OTHER", note="Not a dispute", actor=self.operator)
        with self.assertRaises(triage.TriageForbidden):
            triage.release_hold(reply.pk, note="ok", actor=self.operator)
        self.assertEqual(active(reply), ["DISPUTE"])

    def test_approver_resumes_chasing_with_a_note(self) -> None:
        reply = classified("DISPUTE", text="This is wrong")
        with self.assertRaises(triage.InvalidTriage):
            triage.release_hold(reply.pk, note="", actor=self.approver)
        triage.release_hold(reply.pk, note="Called the client; resolved.", actor=self.approver)
        self.assertEqual(active(reply), [])
        self.assertEqual(ineligibility(self.invoice), [])
        self.assertEqual(ChaseHold.objects.get().released_by, self.approver)

    def test_correction_from_other_to_dispute_escalates_without_approver(self) -> None:
        reply = classified("OTHER", text="hmm")
        triage.classify(reply.pk, label="DISPUTE", extracted={"dispute_reason": "quality"}, actor=self.operator)
        reply.refresh_from_db()
        self.assertEqual(active(reply), ["DISPUTE"])
        self.assertEqual(reply.extracted["dispute_reason"], "quality")

    def test_correcting_to_a_harmless_label_releases_a_non_protected_hold(self) -> None:
        reply = classified("OTHER", text="thanks")
        triage.classify(reply.pk, label="OTHER", actor=self.operator)
        self.assertEqual(active(reply), [])

    def test_correction_reapplies_the_action_once(self) -> None:
        reply = classified("REQUESTS_INFO", confidence=0.5, text="send a copy")
        triage.classify(reply.pk, label="REQUESTS_INFO", actor=self.operator)
        triage.classify(reply.pk, label="REQUESTS_INFO", actor=self.operator)
        self.assertEqual(Task.objects.filter(reply=reply, kind="SEND_INFO").count(), 1)

    def test_audit_trail_keeps_ai_proposal_and_human_correction(self) -> None:
        reply = classified("OTHER", text="hmm")
        triage.classify(reply.pk, label="CLAIMS_PAID", note="They paid by card", actor=self.operator)
        decisions = list(ReplyClassification.objects.filter(message=reply).values_list("source", "label", "decision"))
        self.assertEqual(decisions, [("AI", "OTHER", "triage:no_automatic_action"), ("HUMAN", "CLAIMS_PAID", "human")])
        event = TimelineEvent.objects.get(type="REPLY_REVIEWED")
        self.assertEqual((event.payload["previous_label"], event.payload["corrected"]), ("OTHER", True))

    def test_audit_trail_is_append_only(self) -> None:
        from django.db import DatabaseError, transaction

        reply = classified("OTHER", text="hmm")
        record = ReplyClassification.objects.get(message=reply)
        with self.assertRaises(DatabaseError), transaction.atomic():
            ReplyClassification.objects.filter(pk=record.pk).update(label="DISPUTE")
        with self.assertRaises(DatabaseError), transaction.atomic():
            ReplyClassification.objects.filter(pk=record.pk).delete()

    def test_invalid_input(self) -> None:
        reply = classified("OTHER", text="hmm")
        for kwargs in ({"label": "PAID"}, {"label": "OTHER", "extracted": {"bogus": "x"}},
                       {"label": "PROMISE_TO_PAY", "extracted": {"promised_date": "soon"}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(triage.InvalidTriage):
                triage.classify(reply.pk, actor=self.operator, **kwargs)

    def test_users_without_a_role_are_refused(self) -> None:
        reply = classified("OTHER", text="hmm")
        with self.assertRaises(triage.TriageForbidden):
            triage.classify(reply.pk, label="OTHER", actor=make_user("nobody", role=None))

    def test_retry_classification_is_for_unclassified_replies_only(self) -> None:
        reply = classified("OTHER", confidence=0.4, text="hmm")
        OutboxJob.objects.filter(kind="inbox.classify").update(status="SUCCEEDED")  # the worker ran attempt 1
        job = triage.retry_classification(reply.pk, actor=self.operator)
        self.assertEqual(job.payload["attempt"], 2)
        self.assertEqual(triage.retry_classification(reply.pk, actor=self.operator).pk, job.pk)  # already queued
        triage.classify(reply.pk, label="OTHER", actor=self.operator)
        with self.assertRaises(triage.TriageError):
            triage.retry_classification(reply.pk, actor=self.operator)


class ReplyApiTests(SessionApiTestCase):
    role = Role.OPERATOR

    def setUp(self) -> None:
        super().setUp()
        self.invoice, _ = collectflow_thread()

    def post(self, url: str, data: dict[str, Any] | None = None) -> Any:
        return self.client.post(url, data=json.dumps(data or {}), content_type="application/json")

    def test_triage_list_detail_and_summary(self) -> None:
        reply = classified("DISPUTE", text="The amount is wrong.")
        listing = self.client.get("/api/replies/?triage=true").json()
        rows = listing["results"] if isinstance(listing, dict) else listing
        self.assertEqual([(r["id"], r["on_hold"], r["label"]) for r in rows], [(str(reply.pk), True, "DISPUTE")])
        self.assertNotIn("body", rows[0])

        detail = self.client.get(f"/api/replies/{reply.pk}/").json()
        self.assertEqual(detail["body"], "The amount is wrong.")
        self.assertEqual(detail["holds"][0]["reason"], "DISPUTE")
        self.assertEqual([c["source"] for c in detail["classifications"]], ["AI"])
        self.assertIn("REPLY_RECEIVED", [h["type"] for h in detail["history"]])
        self.assertTrue(detail["gmail_url"].endswith("thread-1"))

        summary = self.client.get("/api/replies/summary/").json()
        self.assertEqual((summary["needs_review"], summary["invoices_on_hold"]), (1, 1))

        invoice = self.client.get(f"/api/invoices/{self.invoice.pk}/").json()
        self.assertEqual(invoice["holds"][0]["reason"], "DISPUTE")

    def test_classify_and_release_through_the_api(self) -> None:
        reply = classified("OTHER", text="hmm")
        response = self.post(f"/api/replies/{reply.pk}/classify/", {"label": "REQUESTS_INFO", "note": "wants a copy"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["state"], "RESOLVED")
        self.assertEqual(self.post(f"/api/replies/{reply.pk}/classify/", {"label": "nope"}).status_code, 400)

    def test_operator_gets_403_releasing_a_protected_hold(self) -> None:
        reply = classified("CLAIMS_PAID", text="Paid already.")
        response = self.post(f"/api/replies/{reply.pk}/release-hold/", {"note": "checked"})
        self.assertEqual((response.status_code, response.json()["code"]), (403, "approver_required"))

    def test_retry_returns_a_job(self) -> None:
        reply = classified("OTHER", confidence=0.3, text="hmm")
        OutboxJob.objects.filter(kind="inbox.classify").update(status="SUCCEEDED")
        response = self.post(f"/api/replies/{reply.pk}/retry-classification/")
        self.assertEqual(response.status_code, 202)
        self.assertTrue(OutboxJob.objects.filter(pk=response.json()["job_id"]).exists())

    def test_unknown_reply_is_404(self) -> None:
        self.assertEqual(self.client.get("/api/replies/00000000-0000-0000-0000-000000000000/").status_code, 404)

    def test_signed_out_users_are_refused(self) -> None:
        self.client.logout()
        self.assertIn(self.client.get("/api/replies/").status_code, (401, 403))


class IntegrationStatusApiTests(SessionApiTestCase):
    def statuses(self) -> dict[str, Any]:
        data = self.client.get("/api/integrations/status/").json()
        return {item["key"]: item for item in data["integrations"]} | {"_jobs": data["scheduled_jobs"]}

    @override_settings(GMAIL_READ_ENABLED=False)
    def test_read_access_awaits_authorization_until_enabled(self) -> None:
        data = self.statuses()
        self.assertEqual(data["gmail_read"]["status"], "awaiting_authorization")
        self.assertEqual(data["reply_polling"]["status"], "awaiting_authorization")
        self.assertIn("--with-read-access", data["gmail_read"]["action"])

    @override_settings(GEMINI_API_KEY="", GOOGLE_SHEETS_SPREADSHEET_ID="", GMAIL_SENDER_EMAIL="")
    def test_not_configured(self) -> None:
        data = self.statuses()
        for key in ("sheets", "gemini", "gmail_send"):
            self.assertEqual(data[key]["status"], "not_configured", key)
        self.assertEqual(data["worker"]["status"], "not_configured")

    @override_settings(GMAIL_READ_ENABLED=True, GMAIL_TOKEN_FILE="/nonexistent/token.json")
    def test_enabled_without_a_token_is_not_configured(self) -> None:
        self.assertEqual(self.statuses()["gmail_read"]["status"], "not_configured")

    def test_polling_health_follows_the_cursor(self) -> None:
        import datetime as dt
        import tempfile

        from django.utils import timezone

        from apps.inbox.models import MailboxCursor

        with tempfile.NamedTemporaryFile() as token, \
                override_settings(GMAIL_READ_ENABLED=True, GMAIL_TOKEN_FILE=token.name, GMAIL_SENDER_EMAIL="ar@x.test"):
            now = timezone.now()
            cursor = MailboxCursor.objects.create(name="gmail", history_id="1", last_poll_finished_at=now,
                                                  last_poll_succeeded_at=now)
            self.assertEqual(self.statuses()["reply_polling"]["status"], "healthy")
            self.assertEqual(self.statuses()["gmail_read"]["status"], "healthy")
            cursor.last_poll_finished_at = now + dt.timedelta(seconds=1)
            cursor.last_error_code, cursor.last_error = "read_not_authorized", "Gmail read access is not granted."
            cursor.save()
            data = self.statuses()
            self.assertEqual((data["reply_polling"]["status"], data["gmail_read"]["status"]), ("failed", "failed"))
            MailboxCursor.objects.update(last_error_code="", last_poll_succeeded_at=now - dt.timedelta(hours=1),
                                         last_poll_finished_at=now - dt.timedelta(hours=1))
            self.assertEqual(self.statuses()["reply_polling"]["status"], "stale")

    def test_gemini_cooldown_is_reported_as_failed(self) -> None:
        from apps.followups.models import GenerationAttempt

        invoice, _ = collectflow_thread()
        with override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="m"):
            GenerationAttempt.objects.create(invoice=invoice, outcome="FAILED", error_code="ai_quota_exhausted")
            self.assertEqual(self.statuses()["gemini"]["status"], "failed")

    @override_settings(GEMINI_API_KEY="super-secret-key-123")
    def test_no_secrets_in_the_response(self) -> None:
        body = self.client.get("/api/integrations/status/").content.decode()
        self.assertNotIn("super-secret-key-123", body)
        self.assertNotIn("refresh_token", body)
        self.assertNotIn("token.json", body)

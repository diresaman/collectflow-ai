from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.automation.lease import Lease
from apps.automation.orchestrator import AutomationOrchestrator
from apps.automation.workflow import build
from apps.followups.delivery import FollowUpDeliveryService
from apps.followups.models import DeliveryAttempt, FollowUp
from apps.followups.services import InvalidState, reject
from apps.followups.tests.helpers import FakeGmail
from apps.invoices.models import AutomationRun
from integrations.gemini import GeminiUnavailable
from integrations.gmail import GmailDeliveryUnknown, GmailRejected

from .helpers import RoutingGemini, iso, overdue_row, sheet

TOKEN = "test-operator-token"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


def steps(data: dict) -> dict[str, str]:  # type: ignore[type-arg]
    return {step["key"]: step["status"] for step in data["steps"]}


@override_settings(OPERATOR_TOKEN=TOKEN, DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[], AUTOMATION_MAX_DRAFTS_PER_RUN=10)
class WorkflowTestCase(TestCase):
    def start(self, *rows: list, gemini: RoutingGemini | None = None) -> AutomationRun:  # type: ignore[type-arg]
        with mock.patch("integrations.gmail.GmailClient.send", side_effect=AssertionError("no Gmail")):
            return AutomationOrchestrator(sheets_client=sheet(*rows), gemini=gemini or RoutingGemini()).run(trigger="MANUAL")  # type: ignore[arg-type]

    def workflow(self, run: AutomationRun) -> dict:  # type: ignore[type-arg]
        response = self.client.get(reverse("workflow-detail", args=[run.pk]), **AUTH)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def follow_up_ids(self, data: dict, queue: str) -> list[str]:  # type: ignore[type-arg]
        return [entry["follow_up"]["id"] for entry in data["pending_actions"][queue]]

    def act(self, name: str, follow_up_id: str):  # type: ignore[no-untyped-def]
        return self.client.post(reverse(name, args=[follow_up_id]), **AUTH)

    def send(self, follow_up_id: str, gmail: FakeGmail):  # type: ignore[no-untyped-def]
        with mock.patch("apps.followups.delivery.GmailClient.from_settings", return_value=gmail):
            return self.client.post(reverse("follow-up-send", args=[follow_up_id]), **AUTH)


class ProgressTests(WorkflowTestCase):
    def test_full_workflow_progress_transitions(self) -> None:
        observed: list[dict[str, str]] = []

        def peek(_: str) -> None:  # progress is persisted while generation is running
            run_ = AutomationRun.objects.get(kind="AUTOMATION")
            data = build(run_)
            observed.append({"state": data["state"], **steps(data)})

        run = self.start(overdue_row("INV-1"), overdue_row("INV-2"), gemini=RoutingGemini(on_call=peek))

        self.assertEqual(observed[0]["state"], "PROCESSING")
        self.assertEqual(
            (observed[0]["SYNCING_INVOICES"], observed[0]["EVALUATING_INVOICES"], observed[0]["GENERATING_DRAFTS"]),
            ("COMPLETED", "COMPLETED", "RUNNING"),
        )

        data = self.workflow(run)
        self.assertEqual(data["state"], "WAITING_FOR_APPROVAL")
        self.assertEqual(data["current_step"], "WAITING_FOR_APPROVAL")
        self.assertEqual(
            steps(data),
            {
                "SYNCING_INVOICES": "COMPLETED",
                "EVALUATING_INVOICES": "COMPLETED",
                "GENERATING_DRAFTS": "COMPLETED",
                "WAITING_FOR_APPROVAL": "RUNNING",
                "WAITING_FOR_SEND_CONFIRMATION": "PENDING",
                "SENDING": "PENDING",
                "FINALIZING": "PENDING",
            },
        )
        sync_step = data["steps"][0]
        self.assertIsNotNone(sync_step["started_at"])
        self.assertIsNotNone(sync_step["completed_at"])
        self.assertIn("2 rows", sync_step["detail"])

        first, second = self.follow_up_ids(data, "approvals")
        self.act("follow-up-approve", first)
        self.act("follow-up-reject", second)
        data = self.workflow(run)
        self.assertEqual(data["state"], "WAITING_FOR_SEND_CONFIRMATION")
        self.assertEqual(steps(data)["WAITING_FOR_APPROVAL"], "COMPLETED")
        self.assertEqual(steps(data)["WAITING_FOR_SEND_CONFIRMATION"], "RUNNING")

        self.assertEqual(self.send(first, FakeGmail()).status_code, 200)
        data = self.workflow(run)
        self.assertEqual(data["state"], "READY_TO_FINALIZE")
        self.assertEqual(steps(data)["SENDING"], "COMPLETED")

        data = self.client.post(reverse("workflow-finalize", args=[run.pk]), **AUTH).json()
        self.assertEqual(data["state"], "FINALIZED")
        self.assertEqual(steps(data)["FINALIZING"], "COMPLETED")
        summary = data["summary"]
        self.assertEqual(
            (summary["drafts_generated"], summary["drafts_approved"], summary["drafts_rejected"],
             summary["gmail_submissions_confirmed"], summary["approved_left_unsent"]),
            (2, 1, 1, 1, 0),
        )

    def test_no_eligible_invoices_finalizes_automatically(self) -> None:
        run = self.start(overdue_row("PAID-1", status="PAID"), overdue_row("FUTURE-1", days=-3))

        data = self.workflow(run)

        self.assertEqual(data["state"], "FINALIZED")
        self.assertEqual(steps(data)["GENERATING_DRAFTS"], "SKIPPED")
        self.assertEqual(steps(data)["WAITING_FOR_APPROVAL"], "SKIPPED")
        self.assertEqual(steps(data)["SENDING"], "SKIPPED")
        self.assertEqual(steps(data)["FINALIZING"], "COMPLETED")
        self.assertEqual(data["summary"]["invoices_overdue"], 0)
        self.assertEqual(data["pending_actions"]["approvals"], [])

    def test_sync_failure_marks_later_steps_failed(self) -> None:
        from apps.invoices.tests.factories import FakeSheetsClient

        run = AutomationOrchestrator(
            sheets_client=FakeSheetsClient(error="Google Sheets denied access (HTTP 403)."), gemini=RoutingGemini()  # type: ignore[arg-type]
        ).run(trigger="MANUAL")

        data = self.workflow(run)
        self.assertEqual(steps(data)["SYNCING_INVOICES"], "FAILED")
        self.assertIn("HTTP 403", data["steps"][0]["detail"])
        self.assertEqual(steps(data)["EVALUATING_INVOICES"], "SKIPPED")
        self.assertEqual(steps(data)["GENERATING_DRAFTS"], "SKIPPED")
        self.assertEqual(data["state"], "FINALIZED")


class ApprovalCheckpointTests(WorkflowTestCase):
    def test_drafts_pause_at_approval_and_rejection_continues(self) -> None:
        run = self.start(overdue_row("INV-1"), overdue_row("INV-2"))
        data = self.workflow(run)
        drafts = self.follow_up_ids(data, "approvals")
        self.assertEqual(len(drafts), 2)
        self.assertIn("recipient_email", data["pending_actions"]["approvals"][0]["follow_up"])

        self.act("follow-up-reject", drafts[0])
        data = self.workflow(run)
        self.assertEqual(self.follow_up_ids(data, "approvals"), [drafts[1]])
        self.assertEqual(data["state"], "WAITING_FOR_APPROVAL")

        self.act("follow-up-reject", drafts[1])
        data = self.workflow(run)
        self.assertEqual(data["state"], "READY_TO_FINALIZE")
        self.assertEqual(steps(data)["WAITING_FOR_SEND_CONFIRMATION"], "SKIPPED")
        self.assertEqual(data["summary"]["drafts_rejected"], 2)

    def test_regenerated_draft_is_still_tracked(self) -> None:
        run = self.start(overdue_row("INV-1"))
        original = self.follow_up_ids(self.workflow(run), "approvals")[0]
        invoice_id = FollowUp.objects.get(pk=original).invoice_id

        with mock.patch("apps.followups.services.GeminiClient.from_settings", return_value=RoutingGemini()):
            self.client.post(
                reverse("invoice-analyze", args=[invoice_id]), {"regenerate": True}, content_type="application/json", **AUTH
            )

        approvals = self.follow_up_ids(self.workflow(run), "approvals")
        self.assertEqual(len(approvals), 1)
        self.assertNotEqual(approvals[0], original)

    def test_approved_reminder_enters_send_queue_and_approval_never_sends(self) -> None:
        run = self.start(overdue_row("INV-1"))
        draft = self.follow_up_ids(self.workflow(run), "approvals")[0]

        with mock.patch("integrations.gmail.GmailClient.send", side_effect=AssertionError("must not send")) as send, \
                mock.patch("integrations.gmail.GmailClient.check_ready", side_effect=AssertionError("must not check")):
            self.assertEqual(self.act("follow-up-approve", draft).status_code, 200)
            data = self.workflow(run)
        send.assert_not_called()

        entry = data["pending_actions"]["send_confirmations"][0]["follow_up"]
        self.assertEqual(entry["id"], draft)
        self.assertEqual(entry["recipient_email"], "ap-inv-1@fictional.test")
        self.assertTrue(entry["subject"] and entry["body"])
        self.assertEqual(entry["status"], "APPROVED")
        self.assertFalse(DeliveryAttempt.objects.exists())
        self.assertEqual(mail.outbox, [])


class SendConfirmationTests(WorkflowTestCase):
    def approved(self, *invoice_ids: str) -> tuple[AutomationRun, list[str]]:
        run = self.start(*(overdue_row(invoice_id) for invoice_id in invoice_ids))
        drafts = self.follow_up_ids(self.workflow(run), "approvals")
        for draft in drafts:
            self.act("follow-up-approve", draft)
        return run, drafts

    def test_explicit_confirmation_uses_existing_delivery_service(self) -> None:
        run, (follow_up,) = self.approved("INV-1")
        gmail = FakeGmail()

        original = FollowUpDeliveryService.send
        with mock.patch.object(FollowUpDeliveryService, "send", autospec=True, side_effect=original) as service:
            response = self.send(follow_up, gmail)

        self.assertEqual(response.status_code, 200)
        service.assert_called_once()
        self.assertEqual(gmail.sent[0]["recipient"], "ap-inv-1@fictional.test")
        data = self.workflow(run)
        self.assertEqual(data["summary"]["gmail_submissions_confirmed"], 1)
        self.assertEqual(self.follow_up_ids(data, "deliveries"), [follow_up])
        self.assertEqual(data["pending_actions"]["send_confirmations"], [])

    def test_sends_are_individual_never_bulk(self) -> None:
        run, drafts = self.approved("INV-1", "INV-2")
        gmail = FakeGmail()

        self.send(drafts[0], gmail)

        self.assertEqual(len(gmail.sent), 1)
        data = self.workflow(run)
        self.assertEqual(self.follow_up_ids(data, "send_confirmations"), [drafts[1]])
        self.assertEqual(data["state"], "WAITING_FOR_SEND_CONFIRMATION")

    def test_gmail_failure_is_reported(self) -> None:
        run, (follow_up,) = self.approved("INV-1")
        with self.assertLogs("apps.followups.delivery"):
            self.send(follow_up, FakeGmail(error=GmailRejected("Gmail rejected the email (HTTP 400).")))

        data = self.workflow(run)
        self.assertEqual(data["summary"]["delivery_failed"], 1)
        self.assertEqual(data["summary"]["gmail_submissions_confirmed"], 0)
        self.assertEqual(steps(data)["SENDING"], "FAILED")
        self.assertEqual(data["state"], "READY_TO_FINALIZE")

    def test_unknown_delivery_is_never_counted_as_sent_or_retried(self) -> None:
        run, (follow_up,) = self.approved("INV-1")
        gmail = FakeGmail(error=GmailDeliveryUnknown("The connection to Gmail was interrupted."))
        with self.assertLogs("apps.followups.delivery"):
            self.assertEqual(self.send(follow_up, gmail).status_code, 202)

        data = self.workflow(run)
        self.assertEqual(data["summary"]["delivery_unknown"], 1)
        self.assertEqual(data["summary"]["gmail_submissions_confirmed"], 0)
        self.assertEqual(steps(data)["SENDING"], "FAILED")
        self.assertIn("reconciliation", data["steps"][5]["detail"])
        self.assertEqual(data["pending_actions"]["send_confirmations"], [])  # not offered again
        self.assertEqual(self.send(follow_up, FakeGmail()).status_code, 409)
        self.assertEqual(len(gmail.sent), 1)

        finalized = self.client.post(reverse("workflow-finalize", args=[run.pk]), **AUTH).json()
        self.assertEqual(finalized["summary"]["delivery_unknown"], 1)
        self.assertEqual(FollowUp.objects.get(pk=follow_up).status, FollowUp.Status.DELIVERY_UNKNOWN)

    def test_leave_unsent_keeps_reminder_approved(self) -> None:
        run, drafts = self.approved("INV-1", "INV-2")
        item_id = self.workflow(run)["pending_actions"]["send_confirmations"][0]["item_id"]
        url = reverse("workflow-leave-unsent", args=[run.pk, item_id])

        self.assertEqual(self.client.post(url, **AUTH).status_code, 200)
        self.assertEqual(self.client.post(url, **AUTH).status_code, 200)  # idempotent

        data = self.workflow(run)
        self.assertEqual(len(data["pending_actions"]["send_confirmations"]), 1)
        self.assertEqual(data["summary"]["approved_left_unsent"], 1)
        self.assertEqual(FollowUp.objects.filter(status="APPROVED").count(), 2)

        data = self.client.post(reverse("workflow-finalize", args=[run.pk]), **AUTH).json()
        self.assertEqual(data["summary"]["approved_left_unsent"], 2)
        self.assertEqual(FollowUp.objects.filter(status="APPROVED").count(), 2)
        self.assertFalse(DeliveryAttempt.objects.exists())

    def test_withdraw_cannot_overwrite_a_reserved_send(self) -> None:
        run, (follow_up,) = self.approved("INV-1")
        outcomes: list[str] = []

        class WithdrawDuringSend(FakeGmail):
            def send(self, **kwargs):  # type: ignore[no-untyped-def]
                try:
                    reject(follow_up)  # type: ignore[arg-type]
                    outcomes.append("withdrawn")
                except InvalidState:
                    outcomes.append("refused")
                return super().send(**kwargs)

        self.send(follow_up, WithdrawDuringSend())

        self.assertEqual(outcomes, ["refused"])
        self.assertEqual(FollowUp.objects.get(pk=follow_up).status, FollowUp.Status.SENT)


class ResumeAndDuplicateTests(WorkflowTestCase):
    def test_refresh_returns_persisted_state_without_rerunning(self) -> None:
        run = self.start(overdue_row("INV-1"))
        self.act("follow-up-approve", self.follow_up_ids(self.workflow(run), "approvals")[0])

        with mock.patch("apps.invoices.sync.GoogleSheetsClient.from_settings", side_effect=AssertionError("no sync")), \
                mock.patch("apps.followups.services.GeminiClient.from_settings", side_effect=AssertionError("no AI")):
            first = self.client.get(reverse("workflow-current"), **AUTH).json()["workflow"]
            again = self.client.get(reverse("workflow-current"), **AUTH).json()["workflow"]
            steps_only = self.client.get(reverse("workflow-steps", args=[run.pk]), **AUTH).json()
            pending = self.client.get(reverse("workflow-pending-actions", args=[run.pk]), **AUTH).json()

        self.assertEqual(first["id"], run.pk)
        self.assertEqual(first, again)
        self.assertEqual(first["state"], "WAITING_FOR_SEND_CONFIRMATION")
        self.assertEqual(steps_only["steps"], first["steps"])
        self.assertEqual(pending, first["pending_actions"])
        self.assertEqual(AutomationRun.objects.filter(kind="AUTOMATION").count(), 1)
        self.assertEqual(AutomationRun.objects.filter(kind="SYNC").count(), 1)

    def test_current_prefers_open_workflow_over_newer_finalized_one(self) -> None:
        open_run = self.start(overdue_row("INV-1"))
        self.start(overdue_row("INV-1"))  # nothing new to draft -> auto-finalized

        self.assertEqual(self.client.get(reverse("workflow-current"), **AUTH).json()["workflow"]["id"], open_run.pk)

    def test_repeated_requests_do_not_duplicate_execution(self) -> None:
        lease = Lease.acquire()
        try:
            response = self.client.post(reverse("automation-run"), **AUTH)
        finally:
            lease.release()
        self.assertEqual(response.status_code, 409)
        self.assertFalse(AutomationRun.objects.exists())

        run = self.start(overdue_row("INV-1"))
        self.act("follow-up-reject", self.follow_up_ids(self.workflow(run), "approvals")[0])
        url = reverse("workflow-finalize", args=[run.pk])
        first = self.client.post(url, **AUTH).json()
        second = self.client.post(url, **AUTH).json()
        self.assertEqual(first["run"]["finalized_at"], second["run"]["finalized_at"])

    def test_finalize_is_refused_while_processing_or_sending(self) -> None:
        run = self.start(overdue_row("INV-1"))
        draft = self.follow_up_ids(self.workflow(run), "approvals")[0]
        self.act("follow-up-approve", draft)
        FollowUp.objects.filter(pk=draft).update(status=FollowUp.Status.SENDING)
        self.assertEqual(self.client.post(reverse("workflow-finalize", args=[run.pk]), **AUTH).status_code, 409)

        AutomationRun.objects.filter(pk=run.pk).update(status="RUNNING")
        self.assertEqual(self.client.post(reverse("workflow-finalize", args=[run.pk]), **AUTH).status_code, 409)

    def test_partial_generation_failure(self) -> None:
        gemini = RoutingGemini(fail_for={"INV-2": GeminiUnavailable("Gemini did not respond in time. Try again.")})
        with self.assertLogs("apps.followups.services"):
            run = self.start(overdue_row("INV-1"), overdue_row("INV-2"), gemini=gemini)

        data = self.workflow(run)
        self.assertEqual(data["run"]["status"], "PARTIAL")
        self.assertEqual(steps(data)["GENERATING_DRAFTS"], "COMPLETED")
        self.assertIn("1 failed", data["steps"][2]["detail"])
        self.assertEqual(data["summary"]["generation_failures"], 1)
        self.assertEqual(len(data["pending_actions"]["approvals"]), 1)
        self.assertEqual(data["state"], "WAITING_FOR_APPROVAL")

    def test_step_interrupted_by_lost_lease_is_failed(self) -> None:
        from apps.automation.lease import LEASE_NAME
        from apps.automation.models import AutomationLease

        def lose_lease(_: str) -> None:
            AutomationLease.objects.filter(name=LEASE_NAME).update(owner="someone-else")

        with self.assertLogs("apps.automation.orchestrator", level="ERROR"):
            run = self.start(overdue_row("INV-1"), overdue_row("INV-2"), gemini=RoutingGemini(on_call=lose_lease))

        data = self.workflow(run)
        self.assertEqual(steps(data)["GENERATING_DRAFTS"], "FAILED")
        self.assertEqual(data["run"]["status"], "FAILED")

    def test_unknown_workflow_returns_404(self) -> None:
        self.assertEqual(self.client.get(reverse("workflow-detail", args=[999]), **AUTH).status_code, 404)
        sync = AutomationRun.objects.create(trigger="MANUAL")  # a SYNC run is not a workflow
        self.assertEqual(self.client.get(reverse("workflow-detail", args=[sync.pk]), **AUTH).status_code, 404)

    def test_workflow_endpoints_require_token(self) -> None:
        run = self.start(overdue_row("INV-1"))
        item = run.items.get()
        for method, url in [
            ("get", reverse("workflow-current")),
            ("get", reverse("workflow-detail", args=[run.pk])),
            ("get", reverse("workflow-steps", args=[run.pk])),
            ("get", reverse("workflow-pending-actions", args=[run.pk])),
            ("post", reverse("workflow-finalize", args=[run.pk])),
            ("post", reverse("workflow-leave-unsent", args=[run.pk, item.pk])),
        ]:
            with self.subTest(url=url):
                self.assertEqual(getattr(self.client, method)(url).status_code, 401)
                self.assertEqual(getattr(self.client, method)(url, HTTP_AUTHORIZATION="Bearer x").status_code, 401)
        run.refresh_from_db()
        self.assertIsNone(run.finalized_at)

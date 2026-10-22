"""
P0 acceptance (M12): the whole collections loop through the real services, with Google Sheets, Gmail
and Gemini replaced by in-memory fakes. Each test is one numbered scenario from the M12 gate:

 1 sync from the sheet          6 reply ingested once          11 call task completes its step once
 2 enrollment                   7 promise confirmed by a person 12 unknown delivery is never resent
 3 draft generated and edited   8 dispute blocks every path     13 repeated polls/restarts duplicate nothing
 4 approval by an eligible user 9 resolution re-checks holds    14 audit keeps AI and human decisions
 5 delivery via (fake) Gmail   10 payment claim: task, not PAID

No real provider is reachable: every Gmail send and Gemini call goes to a fake, and the Gmail send
client is patched to fail if anything bypasses the fakes.
"""

import datetime as dt
import threading
from typing import Any
from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.testing import make_user, run_jobs
from apps.automation import outbox
from apps.automation.models import OutboxJob
from apps.automation.scheduling import schedule_periodic_jobs
from apps.cases import disputes, promises
from apps.cases.models import DisputeCase, PromiseToPay
from apps.followups import services as followups
from apps.followups.delivery import DuplicateDelivery, FollowUpDeliveryService
from apps.followups.models import DeliveryAttempt, FollowUp
from apps.followups.tests.helpers import FakeGmail
from apps.inbox import classification, ingestion, services as tasks, triage
from apps.inbox.models import InboundMessage, MailboxCursor, ReplyClassification, Task
from apps.inbox.tests.helpers import FakeClassifier, FakeMailbox, classification as proposal, mail
from apps.invoices.models import ChaseHold, Invoice
from apps.invoices.sync import sync_invoices
from apps.invoices.tests.factories import HEADER, FakeSheetsClient, row
from apps.sequences import engine
from apps.sequences.models import SequenceEnrollment
from apps.sequences.tests.helpers import StepGemini
from apps.timeline.models import ActorType, TimelineEvent
from apps.timeline.testing import TimelineTransactionTestCase
from integrations.gmail import DeliveryResult, GmailDeliveryUnknown

NO_REAL_GMAIL = mock.patch("integrations.gmail.GmailClient.send", side_effect=AssertionError("real Gmail must not be called"))
SAFE_DELIVERY = override_settings(DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[])


def due(days_ago: int) -> str:
    return (timezone.localdate() - dt.timedelta(days=days_ago)).isoformat()


def sheet(*rows: list[Any]) -> FakeSheetsClient:
    return FakeSheetsClient([HEADER, *rows])


class Loop:
    """The shared steps, with the fakes the worker would use."""

    def __init__(self, case: TestCase) -> None:
        self.case = case
        self.operator = make_user("op", Role.OPERATOR)
        self.approver = make_user("ap", Role.APPROVER)
        self.gemini = StepGemini()
        self.box = FakeMailbox()
        MailboxCursor.objects.create(name="gmail", history_id="1")

    def sync(self, *rows: list[Any]) -> None:
        run = sync_invoices(client=sheet(*rows))  # type: ignore[arg-type]
        self.case.assertEqual(run.status, "SUCCEEDED", run.error_summary)

    def tick(self) -> list[engine.StepResult]:
        with self.case.captureOnCommitCallbacks(execute=True):
            return engine.run_tick(actor=ActorType.SCHEDULER, gemini=self.gemini)

    def draft_of(self, external_id: str) -> FollowUp:
        return FollowUp.objects.get(invoice__external_id=external_id, status=FollowUp.Status.DRAFT)

    def send(self, follow_up: FollowUp, gmail: FakeGmail) -> FollowUp:
        with self.case.captureOnCommitCallbacks(execute=True):
            FollowUpDeliveryService(gmail).request(follow_up.pk, actor=self.operator)
            with mock.patch("apps.followups.delivery.GmailClient.from_settings", return_value=gmail):
                run_jobs()
        follow_up.refresh_from_db()
        return follow_up

    def approve_and_send(self, external_id: str, thread: str) -> FollowUp:
        draft = self.draft_of(external_id)
        followups.approve(draft.pk, actor=self.approver)
        return self.send(draft, FakeGmail(result=DeliveryResult(message_id=f"gm-{external_id}", thread_id=thread)))

    def reply(self, invoice: Invoice, thread: str, text: str, label: str, confidence: float = 0.95,
              **extracted: Any) -> InboundMessage:
        self.box.add(mail(f"r-{InboundMessage.objects.count()}-{thread}", thread, sender=invoice.client_email, text=text))
        ingestion.poll(self.box, owner="worker")  # type: ignore[arg-type]
        reply = InboundMessage.objects.order_by("-created_at").first()
        assert reply is not None
        classification.classify(reply.pk, gemini=FakeClassifier(proposal(label, confidence, **extracted)))
        reply.refresh_from_db()
        return reply


@SAFE_DELIVERY
class P0AcceptanceTests(TestCase):
    def setUp(self) -> None:
        self.loop = Loop(self)
        patcher = NO_REAL_GMAIL
        patcher.start()
        self.addCleanup(patcher.stop)

    def start(self, *rows: list[Any]) -> None:
        self.loop.sync(*rows or [row("INV-500", "Acme", "ap@acme.test", 900, "USD", due(5), "UNPAID")])
        self.loop.tick()

    # 1, 2, 3, 4, 5 -------------------------------------------------------------------------------

    def test_01_to_05_sync_enroll_draft_edit_approve_deliver(self) -> None:
        self.loop.sync(row("INV-500", "Acme", "ap@acme.test", 900, "USD", due(5), "UNPAID"),
                       row("INV-599", "Paid Co", "ap@paid.test", 100, "USD", due(30), "PAID"))
        invoice = Invoice.objects.get(external_id="INV-500")
        self.assertTrue(TimelineEvent.objects.filter(type="INVOICE_CREATED", invoice=invoice).exists())        # 1

        self.loop.tick()
        enrollment = SequenceEnrollment.objects.get(invoice=invoice)
        self.assertEqual((enrollment.status, enrollment.pending_step), ("ACTIVE", 1))                           # 2
        self.assertFalse(SequenceEnrollment.objects.filter(invoice__external_id="INV-599").exists())

        draft = self.loop.draft_of("INV-500")
        self.assertEqual(draft.tone, "FRIENDLY")
        followups.update_draft(draft.pk, {"body": draft.body + "\n\nThank you."}, actor=self.loop.operator)  # 3
        draft.refresh_from_db()
        self.assertEqual(draft.last_edited_by, self.loop.operator)

        followups.approve(draft.pk, actor=self.loop.approver)                                                  # 4
        gmail = FakeGmail(result=DeliveryResult(message_id="gm-500", thread_id="t-500"))
        sent = self.loop.send(draft, gmail)                                                                    # 5
        self.assertEqual(sent.status, "SENT")
        self.assertEqual(len(gmail.sent), 1)
        enrollment.refresh_from_db()
        self.assertEqual((enrollment.last_completed_step, enrollment.pending_step), (1, None))  # recorded at once

    def test_04_four_eyes_is_enforced_for_firm_or_high_value(self) -> None:
        self.start(row("INV-510", "Big", "ap@big.test", 9000, "USD", due(5), "UNPAID"))
        draft = self.loop.draft_of("INV-510")
        with self.assertRaises(followups.ApprovalForbidden):
            followups.approve(draft.pk, actor=self.loop.operator)
        followups.update_draft(draft.pk, {"body": draft.body + " Thanks."}, actor=self.loop.approver)
        with self.assertRaises(followups.ApprovalForbidden):
            followups.approve(draft.pk, actor=self.loop.approver)  # edited it: another approver must approve
        followups.approve(draft.pk, actor=make_user("ap2", Role.APPROVER))

    # 6, 7 -----------------------------------------------------------------------------------------

    def test_06_07_reply_ingested_once_then_a_person_confirms_the_promise(self) -> None:
        self.start()
        self.loop.approve_and_send("INV-500", "t-500")
        invoice = Invoice.objects.get(external_id="INV-500")
        reply = self.loop.reply(invoice, "t-500", "We will pay on Friday.", "PROMISE_TO_PAY",
                                promised_date=(timezone.localdate() + dt.timedelta(days=4)).isoformat())
        ingestion.poll(self.loop.box, owner="worker")  # 6: polled again
        MailboxCursor.objects.update(history_id="1")
        ingestion.poll(self.loop.box, owner="worker")  # and the history replayed
        self.assertEqual(InboundMessage.objects.count(), 1)
        self.assertEqual((reply.state, reply.triage_reason), ("TRIAGE", "review_required"))                    # 7: review
        self.assertFalse(PromiseToPay.objects.exists())  # the AI alone never creates one

        promise = promises.record(invoice.pk, promised_date=timezone.localdate() + dt.timedelta(days=4),
                                  reply_id=reply.pk, actor=self.loop.operator)
        self.assertEqual(promise.status, "OPEN")
        self.assertEqual(list(ChaseHold.objects.filter(invoice=invoice, released_at__isnull=True)
                              .values_list("promise_id", flat=True)), [promise.pk])
        with self.assertRaises(followups.FollowUpError):
            followups.request_generation(invoice.pk, regenerate=False, actor=self.loop.operator)

    # 8, 9 -----------------------------------------------------------------------------------------

    def test_08_09_dispute_blocks_every_path_and_resolution_rechecks_other_holds(self) -> None:
        self.start(row("INV-520", "Disputer", "ap@disp.test", 600, "USD", due(20), "UNPAID"))
        self.loop.approve_and_send("INV-520", "t-520")
        invoice = Invoice.objects.get(external_id="INV-520")
        # A step-2 reminder is already approved when the dispute arrives.
        enrollment = SequenceEnrollment.objects.get(invoice=invoice)
        SequenceEnrollment.objects.filter(pk=enrollment.pk).update(next_action_at=timezone.now())
        FollowUp.objects.filter(invoice=invoice).update(sent_at=timezone.now() - dt.timedelta(days=10))
        self.loop.tick()
        step2 = self.loop.draft_of("INV-520")
        followups.approve(step2.pk, actor=self.loop.approver)

        reply = self.loop.reply(invoice, "t-520", "We never agreed to 600; it was 450.", "DISPUTE")
        case = disputes.open_case(invoice.pk, reason="AMOUNT", description="Agreed 450", reply_id=reply.pk,
                                  actor=self.loop.operator)
        step2.refresh_from_db()
        self.assertEqual((step2.status, step2.cancelled_reason), ("REJECTED", "dispute"))                     # withdrawn
        blocked = [
            lambda: followups.request_generation(invoice.pk, regenerate=False, actor=self.loop.operator),
            lambda: followups.FollowUpGenerationService(gemini=self.loop.gemini).generate(invoice.pk, actor=self.loop.operator),
            lambda: FollowUpDeliveryService(FakeGmail()).request(step2.pk, actor=self.loop.operator),
        ]
        for attempt in blocked:
            with self.assertRaises(followups.FollowUpError) as caught:
                attempt()
            self.assertIn(caught.exception.code, ("invoice_disputed", "invalid_status_transition"))
        calls = len(self.loop.gemini.calls)
        results = self.loop.tick()
        self.assertEqual(len(self.loop.gemini.calls), calls)                                                   # 8: tick too
        self.assertIn("invoice_disputed", [r.reason for r in results])

        other = self.loop.reply(invoice, "t-520", "Who is our account contact?", "OTHER")  # a second, unrelated hold
        _, chasing = disputes.resolve(case.pk, outcome="INVALID", resolution="Contract says 600",
                                      actor=self.loop.approver)
        self.assertFalse(chasing["resumes"])                                                                   # 9
        self.assertEqual([b["code"] for b in chasing["blocked_by"]], ["on_hold"])
        triage.classify(other.pk, label="OTHER", actor=self.loop.operator)  # its own owner releases it
        self.assertEqual(disputes.chasing_status(invoice)["resumes"], True)
        self.assertFalse(DeliveryAttempt.objects.filter(follow_up=step2).exists())  # nothing was ever sent for step 2

    # 10 -------------------------------------------------------------------------------------------

    def test_10_payment_claim_creates_a_task_and_never_marks_paid(self) -> None:
        self.start(row("INV-530", "Claimer", "ap@claim.test", 300, "USD", due(5), "UNPAID"))
        self.loop.approve_and_send("INV-530", "t-530")
        invoice = Invoice.objects.get(external_id="INV-530")
        reply = self.loop.reply(invoice, "t-530", "We paid this yesterday, ref TX-1.", "CLAIMS_PAID", 0.99,
                                payment_reference="TX-1")
        invoice.refresh_from_db()
        task = Task.objects.get(reply=reply)
        self.assertEqual((task.kind, invoice.status, reply.state), ("RECONCILE", "UNPAID", "ACTIONED"))
        self.assertTrue(ChaseHold.objects.filter(invoice=invoice, reason="CLAIMS_PAID", released_at__isnull=True).exists())
        with self.captureOnCommitCallbacks(execute=True):
            tasks.mark_done(task.pk, actor=self.loop.operator, note="Not found in the bank yet")
        self.assertTrue(ChaseHold.objects.filter(invoice=invoice, released_at__isnull=True).exists())  # task ≠ release
        with self.assertRaises(triage.TriageForbidden):
            triage.release_hold(reply.pk, note="ok", actor=self.loop.operator)  # approver only

    # 11 -------------------------------------------------------------------------------------------

    def test_11_call_task_completes_its_step_exactly_once(self) -> None:
        self.start(row("INV-540", "Caller", "ap@call.test", 300, "USD", due(40), "UNPAID"))
        invoice = Invoice.objects.get(external_id="INV-540")
        enrollment = SequenceEnrollment.objects.get(invoice=invoice)
        FollowUp.objects.filter(invoice=invoice).update(status="REJECTED")  # skip the emails for this scenario
        SequenceEnrollment.objects.filter(pk=enrollment.pk).update(last_completed_step=3, pending_step=None,
                                                                   next_action_at=timezone.now())
        self.loop.tick()
        task = Task.objects.get(kind="CALL", invoice=invoice)
        for _ in range(3):
            with self.captureOnCommitCallbacks(execute=True):
                tasks.mark_done(task.pk, actor=self.loop.operator, note="Spoke to AP")
            self.loop.tick()
        enrollment.refresh_from_db()
        self.assertEqual((enrollment.status, enrollment.last_completed_step), ("COMPLETED", 4))
        self.assertEqual(TimelineEvent.objects.filter(type="STEP_COMPLETED", invoice=invoice, payload__step=4).count(), 1)

    # 12 -------------------------------------------------------------------------------------------

    def test_12_unknown_delivery_is_never_resent(self) -> None:
        self.start()
        draft = self.loop.draft_of("INV-500")
        followups.approve(draft.pk, actor=self.loop.approver)
        gmail = FakeGmail(error=GmailDeliveryUnknown("timeout after submit"))
        result = self.loop.send(draft, gmail)
        self.assertEqual(result.status, "DELIVERY_UNKNOWN")
        self.assertEqual(len(gmail.sent), 1)

        run_jobs()
        outbox.recover_stale()
        self.loop.tick()
        with self.assertRaises(DuplicateDelivery):
            FollowUpDeliveryService(gmail).request(draft.pk, actor=self.loop.operator)
        with self.assertRaises(followups.FollowUpError):
            followups.request_generation(draft.invoice_id, regenerate=True, actor=self.loop.operator)
        self.assertEqual(len(gmail.sent), 1)                                                                   # never twice
        self.assertEqual(SequenceEnrollment.objects.get(invoice=draft.invoice).blocked_reason, "delivery_unknown")

    # 13 -------------------------------------------------------------------------------------------

    def test_13_repeated_polls_ticks_and_worker_restarts_duplicate_nothing(self) -> None:
        self.start()
        self.loop.approve_and_send("INV-500", "t-500")
        invoice = Invoice.objects.get(external_id="INV-500")
        self.loop.reply(invoice, "t-500", "Can you send the invoice PDF?", "REQUESTS_INFO", 0.95)
        for _ in range(3):
            MailboxCursor.objects.update(history_id="1", lease_owner="")
            ingestion.poll(self.loop.box, owner="worker-after-restart")
            self.loop.tick()
            now = timezone.now()
            schedule_periodic_jobs(now)
            schedule_periodic_jobs(now)
        self.assertEqual(InboundMessage.objects.count(), 1)
        self.assertEqual(Task.objects.filter(kind="SEND_INFO").count(), 1)
        self.assertEqual(OutboxJob.objects.filter(kind="inbox.classify").count(), 1)
        self.assertEqual(FollowUp.objects.filter(invoice=invoice).count(), 1)  # no second reminder drafted
        self.assertEqual(OutboxJob.objects.filter(kind="sequences.tick").values("idempotency_key").distinct().count(),
                         OutboxJob.objects.filter(kind="sequences.tick").count())

        # A delivery interrupted by a crash is never re-run by recovery.
        job = OutboxJob.objects.create(kind="followups.deliver", idempotency_key="deliver:crashed", max_attempts=1,
                                       status=OutboxJob.Status.RUNNING, attempts=1, locked_by="dead",
                                       locked_at=timezone.now() - dt.timedelta(hours=2), payload={"attempt_id": "x"})
        outbox.recover_stale()
        job.refresh_from_db()
        self.assertEqual((job.status, job.last_error_code), ("FAILED", "abandoned"))

    # 14 -------------------------------------------------------------------------------------------

    def test_14_audit_keeps_ai_proposals_and_human_decisions(self) -> None:
        self.start()
        self.loop.approve_and_send("INV-500", "t-500")
        invoice = Invoice.objects.get(external_id="INV-500")
        reply = self.loop.reply(invoice, "t-500", "Hmm, maybe next month.", "PROMISE_TO_PAY", 0.6)
        triage.classify(reply.pk, label="DISPUTE", note="It is really a dispute", actor=self.loop.operator)
        disputes.open_case(invoice.pk, reason="OTHER", description="Unclear terms", reply_id=reply.pk,
                           actor=self.loop.operator)
        log = list(ReplyClassification.objects.filter(message=reply).order_by("created_at")
                   .values_list("source", "label", "decision"))
        self.assertEqual(log, [("AI", "PROMISE_TO_PAY", "triage:low_confidence"), ("HUMAN", "DISPUTE", "human"),
                               ("HUMAN", "DISPUTE", "dispute_opened")])
        types = set(TimelineEvent.objects.filter(invoice=invoice).values_list("type", flat=True))
        for expected in ("INVOICE_CREATED", "ENROLLED", "DRAFT_GENERATED", "FOLLOW_UP_APPROVED", "FOLLOW_UP_SENT",
                         "REPLY_RECEIVED", "REPLY_TRIAGED", "REPLY_REVIEWED", "CHASE_HOLD_PLACED", "DISPUTE_OPENED"):
            self.assertIn(expected, types)
        from django.db import DatabaseError, transaction

        with self.assertRaises(DatabaseError), transaction.atomic():
            ReplyClassification.objects.filter(message=reply).update(label="OTHER")


@SAFE_DELIVERY
class P0ConcurrencyTests(TimelineTransactionTestCase):
    """State transitions that real users can trigger at the same moment."""

    def race(self, *targets: Any) -> tuple[list[Any], list[BaseException]]:
        barrier = threading.Barrier(len(targets))
        results: list[Any] = []
        errors: list[BaseException] = []

        def run(target: Any) -> None:
            try:
                barrier.wait()
                results.append(target())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(t,)) for t in targets]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results, errors

    def setUp(self) -> None:
        self.loop = Loop(self)
        self.loop.sync(row("INV-600", "Racer", "ap@race.test", 900, "USD", due(5), "UNPAID"))
        engine.run_tick(actor=ActorType.SCHEDULER, gemini=self.loop.gemini)
        self.draft = self.loop.draft_of("INV-600")

    def test_two_approvers_and_two_sends_at_once(self) -> None:
        other = make_user("ap2", Role.APPROVER)
        _, errors = self.race(lambda: followups.approve(self.draft.pk, actor=self.loop.approver),
                              lambda: followups.approve(self.draft.pk, actor=other))
        self.assertEqual(len(errors), 1)
        self.assertEqual(TimelineEvent.objects.filter(type="FOLLOW_UP_APPROVED").count(), 1)
        gmail = FakeGmail()
        _, errors = self.race(*[lambda: FollowUpDeliveryService(gmail).request(self.draft.pk, actor=self.loop.operator,
                                                                               enqueue=False)] * 3)
        self.assertEqual(len(errors), 2)
        self.assertEqual(DeliveryAttempt.objects.count(), 1)

    def test_dispute_racing_an_approval(self) -> None:
        invoice = self.draft.invoice
        _, errors = self.race(
            lambda: followups.approve(self.draft.pk, actor=self.loop.approver),
            lambda: disputes.open_case(invoice.pk, reason="AMOUNT", description="x", actor=self.loop.operator),
        )
        self.draft.refresh_from_db()
        self.assertEqual(DisputeCase.objects.get().status, "OPEN")
        self.assertIn(self.draft.status, ("REJECTED",))  # either order: the dispute withdraws it or refuses approval
        with self.assertRaises(followups.FollowUpError):
            FollowUpDeliveryService(FakeGmail()).request(self.draft.pk, actor=self.loop.operator)

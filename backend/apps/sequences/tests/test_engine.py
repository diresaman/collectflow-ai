import datetime as dt
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.automation import outbox
from apps.automation.lease import Lease
from apps.automation.models import OutboxJob
from apps.automation.scheduling import current_slot, schedule_periodic_jobs
from apps.followups.delivery import FollowUpDeliveryService
from apps.followups.models import FollowUp
from apps.followups.services import ActiveFollowUpExists, FollowUpGenerationService, SubjectLocked, update_draft
from apps.followups.tests.helpers import FakeGmail
from apps.inbox.models import Task
from apps.invoices.models import AutomationRun, Invoice
from apps.sequences import engine
from apps.sequences.models import SequenceEnrollment
from apps.timeline.models import TimelineEvent
from integrations.gemini import GeminiUnavailable

from .helpers import StepGemini, enrollment_of, invoice, sent, set_status, tick

ACTIVE, PAUSED = SequenceEnrollment.Status.ACTIVE, SequenceEnrollment.Status.PAUSED


class EnrollmentTests(TestCase):
    def test_overdue_invoice_is_enrolled_and_step_1_is_drafted_friendly(self) -> None:
        # v2 §12: "Invoice becomes overdue → Enrolled; step 1 draft at due + 1 with friendly tone".
        inv = invoice(days=1)
        gemini = StepGemini()

        results = tick(gemini)

        enrollment = enrollment_of(inv)
        self.assertEqual((enrollment.status, enrollment.last_completed_step, enrollment.pending_step), (ACTIVE, 0, 1))
        self.assertEqual(gemini.tones(), ["FRIENDLY"])
        draft = FollowUp.objects.get()
        self.assertEqual((draft.status, draft.step.order, draft.enrollment_id, draft.thread_reply_to), ("DRAFT", 1, enrollment.pk, None))
        self.assertEqual([r.outcome for r in results], ["GENERATED"])
        self.assertTrue(TimelineEvent.objects.filter(type="ENROLLED").exists())

    def test_long_overdue_never_contacted_invoice_still_starts_friendly(self) -> None:
        inv = invoice(days=40)
        gemini = StepGemini()
        tick(gemini)
        self.assertEqual(gemini.tones(), ["FRIENDLY"])
        self.assertEqual(enrollment_of(inv).pending_step, 1)

    def test_ineligible_invoices_are_not_enrolled(self) -> None:
        invoice("NOT-DUE", days=0)
        invoice("FUTURE", days=-5)
        invoice("PAID", status=Invoice.Status.PAID)
        invoice("GONE", in_sheet=False)
        gemini = StepGemini()
        tick(gemini)
        self.assertFalse(SequenceEnrollment.objects.exists())
        self.assertEqual(gemini.calls, [])

    def test_paid_or_removed_invoices_exit_their_sequence(self) -> None:
        paid, gone = invoice("PAID-LATER"), invoice("REMOVED-LATER")
        tick()
        Invoice.objects.filter(pk=paid.pk).update(status=Invoice.Status.PAID)
        Invoice.objects.filter(pk=gone.pk).update(in_sheet=False)
        tick()
        self.assertEqual((enrollment_of(paid).status, enrollment_of(paid).exit_reason), ("EXITED", "paid"))
        self.assertEqual((enrollment_of(gone).status, enrollment_of(gone).exit_reason), ("EXITED", "not_in_sheet"))

    def test_reconcile_is_idempotent(self) -> None:
        invoice()
        first = engine.reconcile(actor=None)
        second = engine.reconcile(actor=None)
        self.assertEqual((first.enrolled, second.enrolled), (1, 0))
        self.assertEqual(SequenceEnrollment.objects.count(), 1)


class HistoryTests(TestCase):
    """Invoices with reminders from before sequences: placed by what was actually sent."""

    def test_clean_history_continues_after_the_steps_already_sent(self) -> None:
        inv = invoice(days=20)
        sent(inv, "FRIENDLY", days_ago=10)
        gemini = StepGemini()

        tick(gemini)

        enrollment = enrollment_of(inv)
        self.assertEqual((enrollment.last_completed_step, enrollment.review_required), (1, False))
        self.assertEqual(gemini.tones(), ["PROFESSIONAL"])  # step 2, not another friendly reminder

    def test_ambiguous_history_is_flagged_but_continues_when_the_next_step_is_not_firm(self) -> None:
        # The real data: v1 sent a PROFESSIONAL first reminder (tone by days overdue).
        inv = invoice(days=20)
        sent(inv, "PROFESSIONAL", days_ago=10)

        tick(StepGemini())

        enrollment = enrollment_of(inv)
        self.assertEqual((enrollment.status, enrollment.last_completed_step, enrollment.review_required), (ACTIVE, 1, True))
        self.assertIn("professional", enrollment.review_reason)
        self.assertEqual(FollowUp.objects.filter(step__order=2).count(), 1)

    def test_ambiguous_history_never_moves_into_a_firm_step_automatically(self) -> None:
        inv = invoice(days=30)
        sent(inv, "PROFESSIONAL", days_ago=20)
        sent(inv, "PROFESSIONAL", days_ago=10)
        gemini = StepGemini()

        results = tick(gemini)

        enrollment = enrollment_of(inv)
        self.assertEqual((enrollment.status, enrollment.pause_reason), (PAUSED, "history_review"))
        self.assertTrue(enrollment.review_required)
        self.assertEqual(gemini.calls, [])
        self.assertEqual(results, [])  # paused enrollments are not ticked

    def test_clean_history_may_reach_a_firm_step(self) -> None:
        inv = invoice(days=30)
        sent(inv, "FRIENDLY", days_ago=20)
        sent(inv, "PROFESSIONAL", days_ago=10)
        gemini = StepGemini()
        tick(gemini)
        self.assertEqual(gemini.tones(), ["FIRM"])
        self.assertFalse(enrollment_of(inv).review_required)

    def test_unresolved_delivery_pauses_for_review(self) -> None:
        inv = invoice(days=20)
        unknown = sent(inv, "FRIENDLY", days_ago=10)
        set_status(unknown, FollowUp.Status.DELIVERY_UNKNOWN)
        tick()
        enrollment = enrollment_of(inv)
        self.assertEqual((enrollment.status, enrollment.blocked_reason), (PAUSED, "delivery_unknown"))

    def test_draft_in_progress_is_adopted_as_the_next_step(self) -> None:
        inv = invoice(days=3)
        from apps.followups.tests.helpers import make_follow_up

        draft = make_follow_up(inv, FollowUp.Status.DRAFT, tone="FRIENDLY")
        gemini = StepGemini()

        results = tick(gemini)

        enrollment = enrollment_of(inv)
        self.assertEqual((enrollment.pending_step, enrollment.review_required), (1, False))
        self.assertEqual(gemini.calls, [])
        self.assertEqual((results[0].reason, results[0].follow_up), ("draft_awaiting_review", draft))

    def test_recent_rejection_is_not_redrafted_during_the_cooldown(self) -> None:
        inv = invoice(days=3)
        from apps.followups.tests.helpers import make_follow_up

        make_follow_up(inv, FollowUp.Status.REJECTED, tone="FRIENDLY")
        results = tick(StepGemini())
        self.assertEqual(results[0].reason if results else engine.preview()[0][2], "recently_rejected")
        self.assertFalse(FollowUp.objects.filter(status="DRAFT").exists())

    def test_all_history_is_preserved(self) -> None:
        inv = invoice(days=20)
        old = sent(inv, "PROFESSIONAL", days_ago=10)
        tick(StepGemini())
        old.refresh_from_db()
        self.assertEqual((old.status, old.enrollment_id, old.step_id), ("SENT", None, None))


class ProgressionTests(TestCase):
    def start(self, days: int = 3) -> tuple[Invoice, FollowUp]:
        inv = invoice(days=days)
        tick(StepGemini())
        return inv, FollowUp.objects.get(invoice=inv, status="DRAFT")

    def test_waiting_states_do_not_advance(self) -> None:
        inv, draft = self.start()
        for status, reason in [
            (FollowUp.Status.DRAFT, "draft_awaiting_review"),
            (FollowUp.Status.APPROVED, "approved_awaiting_send"),
            (FollowUp.Status.SENDING, "delivery_unresolved"),
            (FollowUp.Status.DELIVERY_UNKNOWN, "delivery_unresolved"),
            (FollowUp.Status.FAILED, "delivery_failed"),
        ]:
            with self.subTest(status=status):
                set_status(draft, status)
                gemini = StepGemini()
                results = tick(gemini)
                enrollment = enrollment_of(inv)
                self.assertEqual((enrollment.last_completed_step, enrollment.pending_step), (0, 1))
                self.assertEqual(results[0].reason, reason)
                self.assertEqual(gemini.calls, [])  # creating a draft never completes a step
        self.assertEqual(enrollment_of(inv).blocked_reason, "delivery_failed")

    def test_sent_completes_the_step_and_the_next_waits_for_cooldown(self) -> None:
        inv, draft = self.start()
        set_status(draft, FollowUp.Status.SENT, sent_at=timezone.now())

        results = tick(StepGemini())

        enrollment = enrollment_of(inv)
        self.assertEqual((enrollment.last_completed_step, enrollment.pending_step), (1, None))
        self.assertEqual(results[0].reason, "cooldown")
        self.assertGreater(enrollment.next_action_at, timezone.now() + dt.timedelta(days=6))
        self.assertTrue(TimelineEvent.objects.filter(type="STEP_COMPLETED", payload__outcome="sent").exists())

    def test_rejected_completes_the_step_without_sending(self) -> None:
        inv, draft = self.start(days=12)
        set_status(draft, FollowUp.Status.REJECTED)
        gemini = StepGemini()

        tick(gemini)

        self.assertEqual(enrollment_of(inv).last_completed_step, 1)
        self.assertEqual(gemini.tones(), ["PROFESSIONAL"])  # step 2 is due (day 8 passed, nothing was sent)

    def test_machine_off_for_days_advances_one_step_per_tick(self) -> None:
        # v2 §12: "Machine off for 3 days → next tick advances each enrollment by one step only".
        inv, draft = self.start(days=40)
        set_status(draft, FollowUp.Status.SENT, sent_at=timezone.now() - dt.timedelta(days=10))
        gemini = StepGemini()

        tick(gemini)
        tick(gemini)

        self.assertEqual(gemini.tones(), ["PROFESSIONAL"])  # one new step; step 3 waits for step 2's outcome
        self.assertEqual(enrollment_of(inv).pending_step, 2)

    def test_tick_is_idempotent(self) -> None:
        inv, _ = self.start()
        before = SequenceEnrollment.objects.values().get()
        gemini = StepGemini()
        tick(gemini)
        tick(gemini)
        after = SequenceEnrollment.objects.values().get()
        before.pop("updated_at"), after.pop("updated_at")
        self.assertEqual(before, after)
        self.assertEqual((FollowUp.objects.count(), gemini.calls), (1, []))

    def test_generation_failure_leaves_the_step_pending_for_the_next_tick(self) -> None:
        inv = invoice()
        results = tick(StepGemini(fail=GeminiUnavailable("busy")))
        enrollment = enrollment_of(inv)
        self.assertEqual((results[0].outcome, enrollment.pending_step, enrollment.drafting_started_at), ("FAILED", 1, None))

        tick(StepGemini())
        self.assertEqual(FollowUp.objects.get().step.order, 1)

    @override_settings(AUTOMATION_MAX_DRAFTS_PER_RUN=1)
    def test_draft_limit_per_tick(self) -> None:
        invoice("A"), invoice("B")
        gemini = StepGemini()
        results = tick(gemini)
        self.assertEqual(len(gemini.calls), 1)
        self.assertEqual(sorted(r.reason for r in results), ["", "run_limit_reached"])
        tick(gemini)
        self.assertEqual(len(gemini.calls), 2)

    def test_call_task_step_and_completion(self) -> None:
        inv = invoice(days=40)
        sent(inv, "FRIENDLY", days_ago=30)
        sent(inv, "PROFESSIONAL", days_ago=20)
        sent(inv, "FIRM", days_ago=10)

        tick(StepGemini())
        tick(StepGemini())  # repeated ticks never create a second task

        task = Task.objects.get()
        self.assertEqual((task.kind, task.step.order, enrollment_of(inv).pending_step), ("CALL", 4, 4))
        Task.objects.filter(pk=task.pk).update(done_at=timezone.now())
        tick(StepGemini())
        enrollment = enrollment_of(inv)
        self.assertEqual((enrollment.status, enrollment.last_completed_step), ("COMPLETED", 4))
        tick(StepGemini())
        self.assertEqual(SequenceEnrollment.objects.count(), 1)  # a completed invoice is not re-enrolled

    def test_escalation_is_capped_at_firm(self) -> None:
        self.assertEqual([engine.escalate("FRIENDLY", n) for n in range(4)], ["FRIENDLY", "PROFESSIONAL", "FIRM", "FIRM"])

    def test_ineligible_invoice_gets_no_draft_even_when_due(self) -> None:
        inv = invoice()
        engine.reconcile(actor=None)
        Invoice.objects.filter(pk=inv.pk).update(client_email="not-an-email")
        gemini = StepGemini()
        results = tick(gemini)
        self.assertEqual((results[0].reason, gemini.calls), ("invalid_recipient", []))


@override_settings(DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[], GMAIL_SENDER_EMAIL="ar@sender.test")
class ThreadingTests(TestCase):
    def test_step_2_replies_in_the_step_1_thread(self) -> None:
        # v2 §12: "Step 1 sent, no reply, 7 days pass → Step 2 draft replies in the same Gmail thread".
        inv = invoice(days=20)
        first = sent(inv, "FRIENDLY", days_ago=8, thread="thread-xyz", message_id="<step1@sender.test>")

        tick(StepGemini())

        draft = FollowUp.objects.get(status="DRAFT")
        self.assertEqual(draft.thread_reply_to.follow_up, first)
        self.assertEqual(draft.subject, f"Re: {first.subject}")

        with self.assertRaises(SubjectLocked):
            update_draft(draft.pk, {"subject": "Something else entirely"})
        update_draft(draft.pk, {"body": "A different body that is long enough to keep."})  # the body stays editable

        set_status(draft, FollowUp.Status.APPROVED)
        gmail = FakeGmail()
        outcome = FollowUpDeliveryService(gmail).send(draft.pk)

        call = gmail.sent[0]
        self.assertEqual((call["thread_id"], call["in_reply_to"], call["references"]),
                         ("thread-xyz", "<step1@sender.test>", "<step1@sender.test>"))
        self.assertEqual(call["subject"], f"Re: {first.subject}")
        self.assertTrue(outcome.attempt.rfc_message_id.endswith("@sender.test>"))
        self.assertEqual(call["message_id"], outcome.attempt.rfc_message_id)

    def test_references_chain_grows_with_each_step(self) -> None:
        inv = invoice(days=20)
        first = sent(inv, "FRIENDLY", days_ago=8, message_id="<s1@x>")
        tick(StepGemini())
        draft = FollowUp.objects.get(status="DRAFT")
        set_status(draft, FollowUp.Status.APPROVED)
        attempt = FollowUpDeliveryService(FakeGmail()).request(draft.pk, enqueue=False)
        self.assertEqual(attempt.rfc_references, "<s1@x>")
        self.assertNotEqual(attempt.rfc_message_id, first.delivery_attempts.get().rfc_message_id)


class SchedulingTests(TestCase):
    def test_scheduled_tick_job_never_sends_or_approves(self) -> None:
        invoice()
        with mock.patch("integrations.gmail.GmailClient.send", side_effect=AssertionError("must not send")), \
                mock.patch("apps.followups.services.GeminiClient.from_settings", return_value=StepGemini()), \
                mock.patch("apps.followups.services.approve", side_effect=AssertionError("must not approve")):
            schedule_periodic_jobs()
            outbox.run_pending()

        job = OutboxJob.objects.get(kind="sequences.tick")
        self.assertEqual((job.status, job.result["generated"]), ("SUCCEEDED", 1))
        self.assertEqual(AutomationRun.objects.get(kind="SEQUENCE_TICK").drafts_generated, 1)
        self.assertEqual(FollowUp.objects.get().status, "DRAFT")

    def test_slots_are_idempotent_and_aligned(self) -> None:
        now = timezone.now().replace(minute=37, second=12)
        schedule_periodic_jobs(now)
        schedule_periodic_jobs(now + dt.timedelta(minutes=5))  # same 15-minute slot (restart, second worker)
        self.assertEqual(OutboxJob.objects.filter(kind="sequences.tick").count(), 1)
        self.assertEqual(current_slot(now).minute, 30)
        schedule_periodic_jobs(now + dt.timedelta(minutes=10))
        self.assertEqual(OutboxJob.objects.filter(kind="sequences.tick").count(), 2)

    def test_tick_skips_while_an_automation_run_holds_the_lease(self) -> None:
        invoice()
        lease = Lease.acquire()
        try:
            schedule_periodic_jobs()
            outbox.run_pending()
        finally:
            lease.release()
        self.assertEqual(OutboxJob.objects.get(kind="sequences.tick").result, {"skipped": "automation_running"})
        self.assertFalse(SequenceEnrollment.objects.exists())

    def test_successful_sync_queues_a_tick(self) -> None:
        from apps.invoices.sync import handle_sync_job
        from apps.invoices.tests.factories import HEADER, FakeSheetsClient, row

        run = AutomationRun.objects.create(trigger="MANUAL")
        job = outbox.enqueue("invoices.sync", {"run_id": run.pk})
        with mock.patch("apps.invoices.sync.GoogleSheetsClient.from_settings", return_value=FakeSheetsClient([HEADER, row()])):
            handle_sync_job(job)
        self.assertTrue(OutboxJob.objects.filter(idempotency_key=f"sequences.tick:after-sync:{run.pk}").exists())


class ManualDraftTests(TestCase):
    def generate(self, inv: Invoice, regenerate: bool = False) -> FollowUp:
        return FollowUpGenerationService(gemini=StepGemini()).generate(inv.pk, regenerate=regenerate)  # type: ignore[arg-type]

    def test_analyze_drafts_the_next_step_even_before_it_is_due(self) -> None:
        inv = invoice(days=20)
        sent(inv, "FRIENDLY", days_ago=1)  # step 2 is in cooldown, but a person asked
        draft = self.generate(inv)
        self.assertEqual((draft.step.order, draft.tone), (2, "PROFESSIONAL"))
        self.assertEqual(enrollment_of(inv).pending_step, 2)

    def test_regenerate_keeps_the_step(self) -> None:
        inv = invoice()
        first = self.generate(inv)
        second = self.generate(inv, regenerate=True)
        first.refresh_from_db()
        self.assertEqual((first.status, second.step_id), ("REJECTED", first.step_id))
        self.assertEqual(enrollment_of(inv).last_completed_step, 0)  # a replaced draft does not complete the step

    def test_analyze_refuses_while_history_review_is_pending(self) -> None:
        inv = invoice(days=30)
        sent(inv, "PROFESSIONAL", days_ago=20)
        sent(inv, "PROFESSIONAL", days_ago=10)
        engine.reconcile(actor=None)
        with self.assertRaises(engine.SequenceReviewRequired):
            self.generate(inv)

    def test_analyze_refuses_an_unresolved_step(self) -> None:
        inv = invoice()
        draft = self.generate(inv)
        set_status(draft, FollowUp.Status.DELIVERY_UNKNOWN)
        # Refused before any AI call: the reminder is still active (unknown outcome).
        with self.assertRaises(ActiveFollowUpExists):
            self.generate(inv)
        self.assertEqual(enrollment_of(inv).pending_step, 1)

    def test_failed_delivery_is_redrafted_for_the_same_step(self) -> None:
        inv = invoice()
        draft = self.generate(inv)
        set_status(draft, FollowUp.Status.FAILED)
        again = self.generate(inv)
        self.assertEqual(again.step.order, 1)

    def test_high_value_threshold_setting(self) -> None:
        from django.conf import settings

        self.assertEqual(settings.HIGH_VALUE_THRESHOLD, Decimal("5000"))

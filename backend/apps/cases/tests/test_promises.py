import datetime as dt
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.cases import monitoring, promises
from apps.cases.common import ApproverRequired, CaseForbidden, InvalidCase, InvoiceNotChaseable
from apps.cases.models import PromiseToPay
from apps.followups.eligibility import ineligibility
from apps.followups.models import FollowUp
from apps.followups.tests.helpers import make_follow_up
from apps.inbox.models import InboundMessage, ReplyClassification
from apps.invoices.models import ChaseHold
from apps.sequences import engine
from apps.sequences.models import SequenceEnrollment
from apps.sequences.tests.helpers import StepGemini
from apps.timeline.models import ActorType, TimelineEvent

from .helpers import chased, paid_event, reply, today_plus, users


def active_holds(invoice) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    return sorted((h.source, h.reason) for h in ChaseHold.objects.filter(invoice=invoice, released_at__isnull=True))


class RecordPromiseTests(TestCase):
    def setUp(self) -> None:
        self.operator, self.approver = users()
        self.invoice = chased()

    def test_from_a_reply_hands_the_hold_to_the_promise_and_keeps_the_ai_proposal(self) -> None:
        message = reply(self.invoice, "We will pay on the 30th.", promised_date=today_plus(8).isoformat())
        self.assertEqual(active_holds(self.invoice), [("reply", "PROMISE_TO_PAY")])

        promise = promises.record(self.invoice.pk, promised_date=today_plus(10).isoformat(), amount="600",
                                  reply_id=message.pk, note="Confirmed by phone", actor=self.operator)

        self.assertEqual((promise.status, promise.promised_date, promise.amount, promise.currency, promise.owner),
                         ("OPEN", today_plus(10), Decimal("600.00"), "USD", self.operator))
        self.assertEqual(active_holds(self.invoice), [("promise", "PROMISE_TO_PAY")])
        message.refresh_from_db()
        self.assertEqual((message.state, message.label_source), ("RESOLVED", "HUMAN"))
        decisions = list(ReplyClassification.objects.filter(message=message).values_list("source", "decision"))
        self.assertEqual(decisions, [("AI", "triage:review_required"), ("HUMAN", "promise_recorded")])
        self.assertTrue(TimelineEvent.objects.filter(type="PROMISE_RECORDED", payload__promise_id=str(promise.pk)).exists())

    def test_confirming_the_same_reply_twice_creates_one_promise(self) -> None:
        message = reply(self.invoice, "We will pay soon.")
        first = promises.record(self.invoice.pk, promised_date=today_plus(5), reply_id=message.pk, actor=self.operator)
        second = promises.record(self.invoice.pk, promised_date=today_plus(9), reply_id=message.pk, actor=self.operator)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PromiseToPay.objects.count(), 1)

    def test_one_open_promise_per_invoice(self) -> None:
        promises.record(self.invoice.pk, promised_date=today_plus(5), actor=self.operator)
        with self.assertRaises(promises.PromiseExists):
            promises.record(self.invoice.pk, promised_date=today_plus(6), actor=self.operator)

    def test_date_and_amount_validation(self) -> None:
        cases = [
            {"promised_date": today_plus(-1)}, {"promised_date": today_plus(61)}, {"promised_date": "soon"},
            {"promised_date": today_plus(5), "amount": "-5"}, {"promised_date": today_plus(5), "amount": "1.234"},
            {"promised_date": today_plus(5), "amount": "999999"}, {"promised_date": today_plus(5), "amount": "abc"},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(InvalidCase):
                promises.record(self.invoice.pk, actor=self.operator, **kwargs)
        self.assertFalse(PromiseToPay.objects.exists())
        # Boundaries are inclusive: today and today + 60 are accepted.
        promises.record(self.invoice.pk, promised_date=today_plus(0), actor=self.operator)

    def test_defaults_to_the_invoice_amount_and_allows_partial(self) -> None:
        promise = promises.record(self.invoice.pk, promised_date=today_plus(60), amount="100.5", actor=self.operator)
        self.assertEqual(promise.amount, Decimal("100.50"))

    def test_paid_invoices_and_signed_out_users_are_refused(self) -> None:
        from apps.accounts.testing import make_user

        with self.assertRaises(CaseForbidden):
            promises.record(self.invoice.pk, promised_date=today_plus(5), actor=make_user("nobody", role=None))
        paid_event(self.invoice)
        with self.assertRaises(InvoiceNotChaseable):
            promises.record(self.invoice.pk, promised_date=today_plus(5), actor=self.operator)

    def test_withdraws_unsent_reminders_but_keeps_the_step_pending(self) -> None:
        enrollment = SequenceEnrollment.objects.get(invoice=self.invoice)
        SequenceEnrollment.objects.filter(pk=enrollment.pk).update(pending_step=2)
        step = engine.steps_of(enrollment.sequence)[2]
        draft = make_follow_up(self.invoice, enrollment=enrollment, step=step, tone="PROFESSIONAL")

        promises.record(self.invoice.pk, promised_date=today_plus(5), actor=self.operator)

        draft.refresh_from_db()
        enrollment.refresh_from_db()
        self.assertEqual((draft.status, draft.cancelled_reason), ("REJECTED", "promise"))
        self.assertEqual((enrollment.pending_step, enrollment.last_completed_step), (2, 1))
        # While the promise is open, ticks skip the invoice and never complete the step.
        result = engine.advance(enrollment.pk, actor=ActorType.SCHEDULER, now=timezone.now(), gemini=StepGemini())
        enrollment.refresh_from_db()
        self.assertEqual((result.reason, enrollment.last_completed_step), ("on_hold", 1))  # type: ignore[union-attr]

    def test_only_an_approver_cancels_with_a_note(self) -> None:
        promise = promises.record(self.invoice.pk, promised_date=today_plus(5), actor=self.operator)
        with self.assertRaises(ApproverRequired):
            promises.cancel(promise.pk, note="no", actor=self.operator)
        with self.assertRaises(InvalidCase):
            promises.cancel(promise.pk, note="", actor=self.approver)
        promises.cancel(promise.pk, note="Client withdrew", actor=self.approver)
        promise.refresh_from_db()
        self.assertEqual((promise.status, promise.resolved_by), ("CANCELLED", self.approver))
        self.assertEqual(active_holds(self.invoice), [])


@override_settings(PROMISE_GRACE_DAYS=2)
class MonitoringTests(TestCase):
    def setUp(self) -> None:
        self.operator, _ = users()
        self.invoice = chased()
        self.promise = promises.record(self.invoice.pk, promised_date=today_plus(3), actor=self.operator)

    def run_on(self, day: dt.date) -> dict:  # type: ignore[type-arg]
        now = timezone.make_aware(dt.datetime.combine(day, dt.time(12, 0)))
        return monitoring.check_promises(now=now)

    def test_stays_open_through_the_last_grace_day(self) -> None:
        self.assertEqual(self.run_on(today_plus(5))["open"], 1)  # promised + 2
        self.promise.refresh_from_db()
        self.assertEqual(self.promise.status, "OPEN")

    def test_broken_the_day_after_grace_escalates_one_tone_and_releases_only_its_hold(self) -> None:
        other = reply(self.invoice, "Also, who is our account manager?", label="OTHER")
        counts = self.run_on(today_plus(6))
        self.promise.refresh_from_db()
        enrollment = SequenceEnrollment.objects.get(invoice=self.invoice)
        self.assertEqual((counts["broken"], counts["escalated"], self.promise.status), (1, 1, "BROKEN"))
        self.assertEqual(enrollment.escalation_level, 1)
        self.assertEqual(enrollment.history["broken_promise_date"], today_plus(3).isoformat())
        self.assertEqual(active_holds(self.invoice), [("reply", "NEEDS_REVIEW")])  # the other reply's hold stays
        self.assertTrue(ineligibility(self.invoice))
        self.assertEqual(other.state, "TRIAGE")

    def test_kept_when_paid_within_grace(self) -> None:
        paid_event(self.invoice, at=timezone.make_aware(dt.datetime.combine(today_plus(5), dt.time(23, 0))))
        counts = self.run_on(today_plus(9))
        self.promise.refresh_from_db()
        self.assertEqual((counts["kept"], self.promise.status), (1, "KEPT"))
        self.assertIsNotNone(self.promise.paid_seen_at)
        self.assertEqual(SequenceEnrollment.objects.get(invoice=self.invoice).escalation_level, 0)

    def test_paid_after_grace_is_broken_without_escalation(self) -> None:
        paid_event(self.invoice, at=timezone.make_aware(dt.datetime.combine(today_plus(6), dt.time(9, 0))))
        counts = self.run_on(today_plus(7))
        self.promise.refresh_from_db()
        self.assertEqual((counts["broken"], counts["escalated"], self.promise.status), (1, 0, "BROKEN"))

    def test_repeated_runs_apply_a_transition_once(self) -> None:
        self.run_on(today_plus(6))
        again = self.run_on(today_plus(7))
        self.assertEqual(again, {"kept": 0, "broken": 0, "escalated": 0, "open": 0})
        self.assertEqual(TimelineEvent.objects.filter(type="PROMISE_BROKEN").count(), 1)
        self.assertEqual(SequenceEnrollment.objects.get(invoice=self.invoice).escalation_level, 1)

    def test_invoice_missing_from_the_sheet_stays_open(self) -> None:
        from apps.invoices.models import Invoice

        Invoice.objects.filter(pk=self.invoice.pk).update(in_sheet=False)
        self.assertEqual(self.run_on(today_plus(20))["open"], 1)

    def test_next_draft_after_a_broken_promise_is_firmer_and_mentions_the_date(self) -> None:
        self.run_on(today_plus(6))
        enrollment = SequenceEnrollment.objects.get(invoice=self.invoice)
        SequenceEnrollment.objects.filter(pk=enrollment.pk).update(next_action_at=timezone.now())
        gemini = StepGemini()
        with mock.patch("integrations.gmail.GmailClient.send", side_effect=AssertionError("must not send")):
            engine.run_tick(actor=ActorType.SCHEDULER, gemini=gemini, now=timezone.now() + dt.timedelta(days=30))
        self.assertEqual(gemini.tones(), ["FIRM"])  # step 2 is professional; one tone firmer
        self.assertIn('"broken_promise_date"', gemini.calls[0]["prompt"])
        draft = FollowUp.objects.get(status="DRAFT")
        self.assertEqual(draft.tone, "FIRM")

    def test_monitor_job_is_scheduled_hourly_and_after_sync(self) -> None:
        from apps.automation.models import OutboxJob
        from apps.automation.scheduling import schedule_periodic_jobs

        now = timezone.now()
        schedule_periodic_jobs(now)
        schedule_periodic_jobs(now)
        self.assertEqual(OutboxJob.objects.filter(kind="cases.monitor").count(), 1)

    def test_classification_never_creates_a_promise(self) -> None:
        reply(self.invoice, "We will pay next week, promise.", promised_date=today_plus(7).isoformat())
        self.assertEqual(PromiseToPay.objects.count(), 1)  # only the one a person recorded in setUp
        self.assertEqual(InboundMessage.objects.get().state, "TRIAGE")

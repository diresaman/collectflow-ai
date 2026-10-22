"""M09.1: automatic drafting must not burn the Gemini quota."""

import datetime as dt

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.testing import SessionApiTestCase
from apps.followups import ai_budget
from apps.followups.models import FollowUp, GenerationAttempt
from apps.followups.services import FollowUpGenerationService
from apps.sequences.models import SequenceEnrollment
from integrations.gemini import GeminiNotConfigured, GeminiQuotaExceeded, GeminiResponseError, GeminiUnavailable

from .helpers import StepGemini, enrollment_of, invoice, tick

MINUTE = dt.timedelta(minutes=1)


def attempts(count: int, *, error_code: str = "", ago: dt.timedelta = dt.timedelta(hours=1)) -> None:
    inv = invoice("BUDGET-FILLER", days=-5)  # not due: never drafted itself
    for _ in range(count):
        GenerationAttempt.objects.create(
            invoice=inv, outcome="FAILED" if error_code else "SUCCEEDED", error_code=error_code,
        )
    GenerationAttempt.objects.filter(invoice=inv).update(created_at=timezone.now() - ago)


@override_settings(AI_RETRY_BASE_MINUTES=15, AI_QUOTA_BACKOFF_MINUTES=30, AI_RETRY_MAX_HOURS=24)
class BackoffTests(TestCase):
    def test_backoff_doubles_and_is_capped(self) -> None:
        self.assertEqual([ai_budget.backoff(n, base_minutes=15) for n in (1, 2, 3)],
                         [15 * MINUTE, 30 * MINUTE, 60 * MINUTE])
        self.assertEqual(ai_budget.backoff(20, base_minutes=15), dt.timedelta(hours=24))

    def test_transient_failure_backs_off_per_step(self) -> None:
        inv = invoice()
        tick(StepGemini(fail=GeminiUnavailable("busy")))
        enrollment = enrollment_of(inv)
        self.assertEqual((enrollment.generation_failures, enrollment.blocked_reason), (1, "ai_retry_scheduled"))
        self.assertAlmostEqual(enrollment.retry_after.timestamp(), (timezone.now() + 15 * MINUTE).timestamp(), delta=60)

        gemini = StepGemini()
        results = tick(gemini)
        self.assertEqual((gemini.calls, results[0].reason), ([], "ai_retry_scheduled"))

        SequenceEnrollment.objects.filter(pk=enrollment.pk).update(retry_after=timezone.now() - MINUTE)
        tick(StepGemini(fail=GeminiUnavailable("busy")))
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.generation_failures, 2)
        self.assertAlmostEqual(enrollment.retry_after.timestamp(), (timezone.now() + 30 * MINUTE).timestamp(), delta=60)

        SequenceEnrollment.objects.filter(pk=enrollment.pk).update(retry_after=timezone.now() - MINUTE)
        tick(StepGemini())
        enrollment.refresh_from_db()
        self.assertEqual((enrollment.generation_failures, enrollment.retry_after, enrollment.blocked_reason), (0, None, ""))
        self.assertEqual(FollowUp.objects.filter(status="DRAFT").count(), 1)

    def test_quota_error_pauses_all_automatic_drafting(self) -> None:
        first, second = invoice("A"), invoice("B")
        failing = StepGemini(fail=GeminiQuotaExceeded("429"))
        results = tick(failing)

        # The first attempt hit the quota; the second invoice was not even tried in the same tick.
        self.assertEqual(len(failing.calls), 1)
        self.assertEqual(sorted(r.reason for r in results), ["ai_quota_cooldown", "ai_quota_exhausted"])
        status = ai_budget.status()
        self.assertEqual(status.blocked_reason(timezone.now()), "ai_quota_cooldown")
        self.assertAlmostEqual(status.cooldown_until.timestamp(), (timezone.now() + 30 * MINUTE).timestamp(), delta=60)

        # Later ticks during the cooldown never call Gemini, for any invoice.
        gemini = StepGemini()
        tick(gemini)
        self.assertEqual(gemini.calls, [])

        # A person can still draft by hand; a success ends the cooldown.
        FollowUpGenerationService(gemini=StepGemini()).generate(second.pk)
        self.assertEqual(ai_budget.status().blocked_reason(timezone.now()), "")
        self.assertIsNotNone(enrollment_of(first).retry_after)

    def test_consecutive_quota_errors_double_the_cooldown(self) -> None:
        attempts(3, error_code="ai_quota_exhausted", ago=MINUTE)
        status = ai_budget.status()
        # Third consecutive quota error: 30 → 60 → 120 minutes after the latest one.
        self.assertAlmostEqual(status.cooldown_until.timestamp(), (timezone.now() + 119 * MINUTE).timestamp(), delta=90)

    def test_non_retryable_failures_wait_for_a_person(self) -> None:
        for error in (GeminiResponseError("bad output"), GeminiNotConfigured("no key")):
            with self.subTest(error=type(error).__name__):
                FollowUp.objects.all().delete()
                SequenceEnrollment.objects.all().delete()
                inv = invoice(f"NR-{type(error).__name__}"[:20])
                tick(StepGemini(fail=error))
                enrollment = enrollment_of(inv)
                self.assertEqual((enrollment.blocked_reason, enrollment.retry_after), ("generation_failed", None))

                gemini = StepGemini()
                results = tick(gemini)
                self.assertEqual((gemini.calls, results[0].reason), ([], "generation_failed"))

                # Drafting manually is allowed and clears the hold.
                FollowUpGenerationService(gemini=StepGemini()).generate(inv.pk)
                enrollment.refresh_from_db()
                self.assertEqual(enrollment.blocked_reason, "")


@override_settings(AI_DAILY_GENERATION_BUDGET=15)
class DailyBudgetTests(TestCase):
    def test_budget_stops_automatic_drafting_but_not_people(self) -> None:
        attempts(15)
        inv = invoice()
        gemini = StepGemini()

        results = tick(gemini)

        self.assertEqual((gemini.calls, results[0].reason), ([], "ai_budget_exhausted"))
        enrollment = enrollment_of(inv)
        self.assertEqual((enrollment.pending_step, enrollment.generation_failures), (None, 0))  # step not started

        draft = FollowUpGenerationService(gemini=StepGemini()).generate(inv.pk)  # a person may still draft
        self.assertEqual(draft.step.order, 1)

    def test_budget_is_a_rolling_24_hours(self) -> None:
        attempts(15, ago=dt.timedelta(hours=25))
        inv = invoice()
        gemini = StepGemini()
        tick(gemini)
        self.assertEqual(len(gemini.calls), 1)
        self.assertEqual(enrollment_of(inv).pending_step, 1)

    def test_unconfigured_attempts_do_not_count(self) -> None:
        attempts(15, error_code="ai_not_configured")
        self.assertEqual(ai_budget.status().used_24h, 0)


class DashboardBudgetTests(SessionApiTestCase):
    @override_settings(AI_DAILY_GENERATION_BUDGET=2)
    def test_dashboard_reports_the_budget(self) -> None:
        attempts(2)
        body = self.client.get(reverse("dashboard")).json()["ai_budget"]
        self.assertEqual((body["used_24h"], body["limit"], body["automatic_drafting_blocked"]), (2, 2, "ai_budget_exhausted"))

import datetime as dt
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.followups.eligibility import ineligibility_reasons, tone_for_days_overdue
from apps.followups.models import FollowUp, InvalidTransition
from apps.invoices.models import Invoice

from .helpers import make_follow_up, overdue_invoice


class EligibilityTests(TestCase):
    def test_unpaid_overdue_invoice_is_eligible(self) -> None:
        self.assertEqual(ineligibility_reasons(overdue_invoice(days=3)), [])

    def test_paid_invoice_is_not_eligible(self) -> None:
        reasons = ineligibility_reasons(overdue_invoice(status=Invoice.Status.PAID))
        self.assertEqual(reasons, ["Invoice is not unpaid."])

    def test_future_due_date_is_not_eligible(self) -> None:
        invoice = overdue_invoice(due_date=timezone.localdate() + dt.timedelta(days=3))
        self.assertEqual(ineligibility_reasons(invoice), ["Invoice is not overdue yet."])

    def test_due_today_is_not_eligible(self) -> None:
        invoice = overdue_invoice(due_date=timezone.localdate())
        self.assertEqual(ineligibility_reasons(invoice), ["Invoice is not overdue yet."])

    def test_invalid_email_and_amount_are_not_eligible(self) -> None:
        invoice = overdue_invoice()
        invoice.client_email = "not-an-email"
        invoice.amount = Decimal("0")  # not saved: the database forbids it
        self.assertEqual(
            ineligibility_reasons(invoice),
            ["Client email address is invalid.", "Invoice amount must be positive."],
        )


class TonePolicyTests(SimpleTestCase):
    def test_tone_boundaries(self) -> None:
        cases = {1: "FRIENDLY", 7: "FRIENDLY", 8: "PROFESSIONAL", 14: "PROFESSIONAL", 15: "FIRM", 90: "FIRM"}
        for days, tone in cases.items():
            with self.subTest(days=days):
                self.assertEqual(tone_for_days_overdue(days), tone)

    def test_not_overdue_has_no_tone(self) -> None:
        with self.assertRaises(ValueError):
            tone_for_days_overdue(0)


class TransitionTests(TestCase):
    def test_allowed_transitions(self) -> None:
        invoice = overdue_invoice()
        follow_up = make_follow_up(invoice)
        follow_up.transition_to(FollowUp.Status.APPROVED)
        self.assertEqual(follow_up.status, FollowUp.Status.APPROVED)
        self.assertIsNotNone(follow_up.approved_at)

    def test_approval_can_be_withdrawn(self) -> None:
        follow_up = FollowUp(invoice=overdue_invoice(), status=FollowUp.Status.APPROVED)
        follow_up.transition_to(FollowUp.Status.REJECTED)
        self.assertEqual(follow_up.status, FollowUp.Status.REJECTED)

    def test_invalid_transitions_raise(self) -> None:
        invoice = overdue_invoice()
        cases = [
            (FollowUp.Status.APPROVED, FollowUp.Status.DRAFT),
            (FollowUp.Status.SENT, FollowUp.Status.REJECTED),
            (FollowUp.Status.DELIVERY_UNKNOWN, FollowUp.Status.REJECTED),
            (FollowUp.Status.REJECTED, FollowUp.Status.APPROVED),
            (FollowUp.Status.DRAFT, FollowUp.Status.SENT),  # reserved for Milestone 04
        ]
        for start, target in cases:
            with self.subTest(start=start, target=target):
                follow_up = FollowUp(invoice=invoice, status=start)
                with self.assertRaises(InvalidTransition):
                    follow_up.transition_to(target)

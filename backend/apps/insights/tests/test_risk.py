import datetime as dt
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.cases.models import DisputeCase, PromiseToPay
from apps.invoices.models import Invoice
from apps.insights import risk

from .helpers import client, dispute, factors, inv, paid, promise, replied, send


class PointsTests(TestCase):
    def test_linear_capped_half_up(self) -> None:
        self.assertEqual([risk.points(d, 90, 35) for d in (0, -4, 1, 30, 45, 60, 90, 400)], [0, 0, 0, 12, 18, 23, 35, 35])
        self.assertEqual(risk.points(Decimal("1.5"), 60, 20), 1)  # 0.5 rounds up
        self.assertEqual(risk.points(Decimal("4.5"), 60, 20), 2)  # 1.5 rounds up


class FactorTests(TestCase):
    def test_minimum_score_for_a_clean_client(self) -> None:
        inv("INV-1", days=-10)  # not due yet
        risk.recompute()
        c = client()
        self.assertEqual(c.risk_score, 0)
        self.assertEqual(factors(c), {"oldest_overdue": 0, "broken_promises": 0, "days_late": 0, "no_reply": 0,
                                      "open_dispute": 0})
        self.assertTrue(c.risk_low_confidence)  # no payment history at all
        self.assertIsNone(c.avg_days_late)
        self.assertEqual(c.risk_factors["outstanding"], {"USD": "1000.00"})

    def test_oldest_overdue_uses_the_oldest_unpaid_invoice_in_the_sheet(self) -> None:
        inv("INV-1", days=10)
        oldest = inv("INV-2", days=45)
        inv("INV-3", days=200, status=Invoice.Status.PAID)  # paid: ignored
        inv("INV-4", days=300, in_sheet=False)  # removed from the sheet: ignored
        risk.recompute()
        c = client()
        factor = c.risk_factors["factors"][0]
        self.assertEqual((factor["points"], factor["facts"]["external_id"], factor["facts"]["days_overdue"]),
                         (18, "INV-2", 45))
        self.assertEqual(factor["facts"]["invoice_id"], str(oldest.pk))
        self.assertEqual(c.risk_factors["oldest_days_overdue"], 45)
        self.assertEqual(c.risk_factors["outstanding"], {"USD": "2000.00"})  # INV-1 + INV-2

    def test_broken_promises_inside_and_outside_the_window(self) -> None:
        a, b, c_ = inv("INV-1"), inv("INV-2"), inv("INV-3")
        promise(a, PromiseToPay.Status.BROKEN, resolved_days_ago=200)  # outside 180 days
        promise(b, PromiseToPay.Status.KEPT)
        risk.recompute()
        self.assertEqual(factors(client())["broken_promises"], 0)
        promise(c_, PromiseToPay.Status.BROKEN, resolved_days_ago=10)
        risk.recompute()
        self.assertEqual(factors(client())["broken_promises"], 15)
        promise(b, PromiseToPay.Status.BROKEN, resolved_days_ago=179)
        risk.recompute()
        c = client()
        self.assertEqual(factors(c)["broken_promises"], 25)
        self.assertEqual(c.risk_factors["factors"][1]["facts"]["count"], 2)
        self.assertEqual(c.promise_kept_rate, Decimal("0.2500"))  # 1 kept of 4 settled (all time)

    def test_days_late_with_enough_history_uses_the_client_average(self) -> None:
        for n, late in enumerate((10, 20, 30)):
            paid(inv(f"P-{n}", days=60), days_late=late)
        inv("INV-1")
        risk.recompute()
        c = client()
        self.assertEqual(c.avg_days_late, Decimal("20.00"))
        self.assertEqual(factors(c)["days_late"], 7)  # 20 x 20 / 60 = 6.67
        self.assertFalse(c.risk_low_confidence)
        self.assertEqual(c.risk_factors["factors"][2]["facts"]["source"], "client")

    def test_short_history_falls_back_to_the_portfolio_average(self) -> None:
        for n, late in enumerate((60, 60, 60)):
            paid(inv(f"O-{n}", days=90, email="other@client.test"), days_late=late)
        paid(inv("P-1", days=60), days_late=0)  # this client: one payment, on time
        inv("INV-1")
        risk.recompute()
        c = client()
        facts = c.risk_factors["factors"][2]["facts"]
        self.assertTrue(c.risk_low_confidence)
        self.assertEqual((facts["source"], facts["portfolio_average"], facts["portfolio_payments"]), ("portfolio", "45.00", 4))
        self.assertEqual(factors(c)["days_late"], 15)  # 20 x 45 / 60
        self.assertEqual(c.avg_days_late, Decimal("0.00"))  # the metric is the client's own history

    def test_missing_payment_history_scores_nothing(self) -> None:
        inv("INV-1")
        inv("INV-2", status=Invoice.Status.PAID)  # paid when first imported: no paid date known
        risk.recompute()
        c = client()
        self.assertEqual(factors(c)["days_late"], 0)
        self.assertEqual(c.risk_factors["factors"][2]["facts"]["source"], "none")
        self.assertTrue(c.risk_low_confidence)

    def test_no_reply_counts_the_last_two_sends_after_the_wait(self) -> None:
        a = inv("INV-1", days=30)
        send(a, days_ago=20)
        replied(a, days_ago=19)
        send(a, days_ago=10)
        send(a, days_ago=5)
        risk.recompute()
        c = client()
        self.assertEqual(factors(c)["no_reply"], 10)  # the last two (10 and 5 days ago) unanswered
        self.assertEqual(c.response_rate, Decimal("0.3333"))
        replied(a, days_ago=4)  # answers the latest send (and so everything before it)
        risk.recompute()
        self.assertEqual(factors(client())["no_reply"], 0)

    def test_a_recent_send_is_not_unanswered_yet(self) -> None:
        a = inv("INV-1")
        send(a, days_ago=8)
        send(a, days_ago=1)  # inside the 3-day reply window
        risk.recompute()
        c = client()
        self.assertEqual(factors(c)["no_reply"], 5)
        self.assertIn("inside the reply window", c.risk_factors["factors"][3]["detail"])

    def test_open_and_resolved_disputes(self) -> None:
        a, b = inv("INV-1"), inv("INV-2")
        dispute(a, DisputeCase.Status.RESOLVED_INVALID)
        risk.recompute()
        self.assertEqual(factors(client())["open_dispute"], 0)
        dispute(b)
        risk.recompute()
        self.assertEqual(factors(client())["open_dispute"], 10)

    def test_maximum_score_is_100(self) -> None:
        a, b, c_ = inv("INV-1", days=120), inv("INV-2", days=95), inv("INV-3", days=91)
        for n in range(3):
            paid(inv(f"P-{n}", days=150), days_late=90)
        promise(a, PromiseToPay.Status.BROKEN)
        promise(b, PromiseToPay.Status.BROKEN)
        send(c_, days_ago=10)
        send(c_, days_ago=6)
        dispute(a)
        risk.recompute()
        c = client()
        self.assertEqual(factors(c), {"oldest_overdue": 35, "broken_promises": 25, "days_late": 20, "no_reply": 10,
                                      "open_dispute": 10})
        self.assertEqual(c.risk_score, 100)

    def test_multiple_invoices_and_currencies_stay_separate(self) -> None:
        inv("INV-1", amount="100.00", currency="USD")
        inv("INV-2", amount="250.50", currency="EUR")
        inv("INV-3", amount="900.00", currency="USD")
        risk.recompute()
        self.assertEqual(client().risk_factors["outstanding"], {"EUR": "250.50", "USD": "1000.00"})

    def test_reproducible_and_explained(self) -> None:
        a = inv("INV-1", days=33)
        send(a, days_ago=9)
        now = timezone.now()
        first = risk.compute_all(now)
        second = risk.compute_all(now)
        result = first[client().pk]
        self.assertEqual(result, second[client().pk])
        self.assertEqual(result.score, sum(f["points"] for f in result.factors))
        for factor in result.factors:
            self.assertTrue(factor["detail"])
            self.assertLessEqual(factor["points"], factor["max"])
        self.assertEqual(sum(risk.FACTOR_MAX.values()), 100)

    def test_time_alone_moves_the_overdue_factor(self) -> None:
        inv("INV-1", days=10)
        later = timezone.now() + dt.timedelta(days=80)
        risk.recompute()
        self.assertEqual(factors(client())["oldest_overdue"], 4)
        risk.recompute(later)
        c = client()
        self.assertEqual(factors(c)["oldest_overdue"], 35)
        self.assertEqual(c.metrics_computed_at, later)

    @override_settings(RISK_OVERDUE_FULL_DAYS=30)
    def test_mapping_is_settings_backed(self) -> None:
        inv("INV-1", days=15)
        risk.recompute()
        self.assertEqual(factors(client())["oldest_overdue"], 18)  # 35 x 15 / 30 = 17.5 -> 18


class RecomputeTests(TestCase):
    def test_repeated_recompute_is_stable(self) -> None:
        inv("INV-1", days=20)
        now = timezone.now()
        self.assertEqual(risk.recompute(now), {"clients": 1, "changed": 1})
        snapshot = (client().risk_score, client().risk_factors)
        self.assertEqual(risk.recompute(now), {"clients": 1, "changed": 0})
        self.assertEqual((client().risk_score, client().risk_factors), snapshot)

    def test_never_touches_holds_follow_ups_or_the_timeline(self) -> None:
        from apps.followups.models import FollowUp
        from apps.invoices.models import ChaseHold
        from apps.timeline.models import TimelineEvent

        a = inv("INV-1", days=40)
        send(a, days_ago=5)
        dispute(a)
        before = (TimelineEvent.objects.count(), FollowUp.objects.count(), ChaseHold.objects.count(),
                  list(Invoice.objects.values_list("status", flat=True)))
        risk.recompute()
        after = (TimelineEvent.objects.count(), FollowUp.objects.count(), ChaseHold.objects.count(),
                 list(Invoice.objects.values_list("status", flat=True)))
        self.assertEqual(before, after)

import datetime as dt
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.testing import SessionApiTestCase, make_user
from apps.automation import outbox
from apps.cases.models import PromiseToPay
from apps.followups.models import FollowUp, StatementLine
from apps.followups.tests.helpers import make_follow_up
from apps.insights import analytics, jobs
from apps.insights.models import AnalyticsSnapshot
from apps.invoices.models import ChaseHold, Invoice
from apps.invoices.sync import sync_invoices
from apps.invoices.tests.factories import HEADER, FakeSheetsClient, row
from apps.timeline.models import ActorType, EventType, TimelineEvent

from .helpers import dispute, inv, paid, promise, replied

NOW = timezone.now()


def by_currency(data: dict, currency: str = "USD") -> dict:  # type: ignore[type-arg]
    return next(c for c in data["currencies"] if c["currency"] == currency)


def seen(invoice: Invoice, days_ago: int) -> None:
    """CollectFlow first saw the invoice `days_ago` days ago (the import event and row time)."""
    at = NOW - dt.timedelta(days=days_ago)
    Invoice.objects.filter(pk=invoice.pk).update(created_at=at)
    TimelineEvent.objects.create(type=EventType.INVOICE_CREATED, invoice=invoice, client=invoice.client,
                                 actor_type=ActorType.SYSTEM, occurred_at=at)


class AgingTests(TestCase):
    def test_bucket_boundaries_per_currency(self) -> None:
        for days in (-5, 0, 1, 30, 31, 60, 61, 90, 91):
            inv(f"U-{days}", days=days, amount="10.00")
        inv("E-1", days=45, currency="EUR", amount="7.00")
        inv("P-1", days=45, status=Invoice.Status.PAID)
        inv("R-1", days=45, in_sheet=False)
        data = analytics.aging(NOW)
        usd = {b["key"]: (b["count"], b["amount"]) for b in by_currency(data)["buckets"]}
        self.assertEqual(usd, {"current": (2, "20.00"), "1_30": (2, "20.00"), "31_60": (2, "20.00"),
                               "61_90": (2, "20.00"), "90_plus": (1, "10.00")})
        self.assertEqual((by_currency(data)["total"], by_currency(data)["overdue"]), ("90.00", "70.00"))
        self.assertEqual(by_currency(data, "EUR")["total"], "7.00")  # never added to USD


class DsoCeiTests(TestCase):
    def test_short_history_is_reported_not_estimated(self) -> None:
        inv("A", days=10)
        data = analytics.dso_and_cei(NOW)
        self.assertEqual(data["status"], "insufficient_history")
        self.assertIn("Opening balances are never estimated", data["reason"])
        self.assertIsNone(by_currency(data)["dso_days"])
        self.assertIsNone(by_currency(data)["cei_percent"])

    def test_formulas_from_observed_history(self) -> None:
        # Opening AR (seen before the 30-day period, unpaid at its start): 1000 + 500 (paid during it).
        opening = inv("OPEN", days=50, amount="1000.00")
        collected = inv("COLL", days=40, amount="500.00")
        seen(opening, 60)
        seen(collected, 45)
        paid(collected, days_late=35)  # paid seen 5 days ago, within the period
        # Billed in the period: 400 (still unpaid, overdue) + 200 (not due yet).
        billed_overdue, billed_current = inv("NEW", days=3, amount="400.00"), inv("CUR", days=-10, amount="200.00")
        seen(billed_overdue, 20)
        seen(billed_current, 5)
        data = analytics.dso_and_cei(NOW)
        usd = by_currency(data)
        self.assertEqual(usd["inputs"], {"opening_ar": "1500.00", "billed": "600.00", "closing_ar": "1600.00",
                                         "closing_current_ar": "200.00"})
        self.assertEqual(usd["dso_days"], "80.0")  # 1600 / 600 x 30
        self.assertEqual(usd["cei_percent"], "26.3")  # (1500 + 600 - 1600) / (1500 + 600 - 200) x 100
        self.assertEqual(data["status"], "ok")

    def test_nothing_billed_leaves_dso_undefined(self) -> None:
        old = inv("OLD", days=50, amount="100.00")
        seen(old, 60)
        usd = by_currency(analytics.dso_and_cei(NOW))
        self.assertIsNone(usd["dso_days"])
        self.assertIn("Nothing was billed", usd["dso_note"])


class PaidObservationTests(TestCase):
    def test_paid_date_is_the_first_sync_that_showed_paid(self) -> None:
        a = inv("A", days=20)
        paid(a, days_late=5)
        TimelineEvent.objects.create(type=EventType.INVOICE_UPDATED, invoice=a, client=a.client,
                                     actor_type=ActorType.SYSTEM, occurred_at=NOW,
                                     payload={"changes": {"status": {"from": "UNPAID", "to": "PAID"}}})
        from apps.insights.risk import first_paid_seen

        self.assertEqual(timezone.localdate(first_paid_seen()[a.pk]), a.due_date + dt.timedelta(days=5))
        inv("B", days=20, status=Invoice.Status.PAID)  # paid when first imported: no observed date
        self.assertEqual(analytics.attribution(NOW)["unmeasured_paid_invoices"], 1)


class AttributionTests(TestCase):
    def test_payment_within_the_window_after_a_reminder(self) -> None:
        a, b, c = inv("A", days=20), inv("B", days=20), inv("C", days=20)
        for invoice in (a, b):
            make_follow_up(invoice, FollowUp.Status.SENT, sent_at=NOW - dt.timedelta(days=8), tone="FRIENDLY")
        paid(a, days_late=15)  # seen 5 days ago: 3 days after the reminder
        paid(b, days_late=21)  # seen 1 day from now: 9 days after the reminder
        paid(c, days_late=15)  # never reminded
        data = analytics.attribution(NOW + dt.timedelta(days=2))
        rows = {p["external_id"]: p for p in data["payments"]}
        self.assertTrue(rows["A"]["after_reminder"])
        self.assertAlmostEqual(rows["A"]["days_after_reminder"], 3.0, delta=1.0)  # paid seen at local noon
        self.assertFalse(rows["B"]["after_reminder"])  # 9 days: outside the window
        self.assertIsNone(rows["C"]["last_reminder_at"])
        self.assertIn("does not prove", data["note"])

    def test_a_statement_counts_for_each_invoice_it_lists(self) -> None:
        a, b = inv("A", days=20), inv("B", days=20)
        statement = make_follow_up(a, FollowUp.Status.SENT, sent_at=NOW - dt.timedelta(days=6), kind="STATEMENT")
        for n, invoice in enumerate((a, b), 1):
            StatementLine.objects.create(follow_up=statement, invoice=invoice, amount=invoice.amount, currency="USD",
                                         due_date=invoice.due_date, invoice_snapshot={}, position=n)
        paid(b, days_late=18)
        row = analytics.attribution(NOW)["payments"][0]
        self.assertEqual((row["external_id"], row["last_reminder"]), ("B", "statement"))


class ForecastTests(TestCase):
    def rows(self) -> dict[str, dict]:  # type: ignore[type-arg]
        return {r["external_id"]: r for r in analytics.forecast(NOW)["invoices"]}

    def test_aging_probability_and_low_confidence_without_history(self) -> None:
        inv("CUR", days=-5, amount="100.00")
        inv("OLD", days=95, amount="100.00")
        rows = self.rows()
        self.assertEqual((rows["CUR"]["probability"], rows["CUR"]["expected"]), ("0.8000", "80.00"))
        self.assertEqual(rows["OLD"]["probability"], "0.1000")
        self.assertTrue(rows["CUR"]["low_confidence"])
        usd = by_currency(analytics.forecast(NOW))
        self.assertEqual((usd["outstanding"], usd["expected"]), ("200.00", "90.00"))
        self.assertIn("estimate", analytics.forecast(NOW)["note"])

    def test_lateness_pushes_payment_beyond_the_window(self) -> None:
        for n in range(3):
            paid(inv(f"P{n}", days=100), days_late=60)  # this client pays 60 days late
        inv("CUR", days=-5, amount="100.00")  # due in 5 days, expected in 55: after the window
        row = self.rows()["CUR"]
        self.assertEqual(row["probability"], "0.2000")  # 0.80 x 0.25
        self.assertFalse(row["low_confidence"])

    def test_promises_use_the_kept_rate(self) -> None:
        a = inv("A", days=10, amount="100.00")
        promise(a, PromiseToPay.Status.OPEN)
        PromiseToPay.objects.filter(invoice=a).update(promised_date=timezone.localdate() + dt.timedelta(days=4))
        row = self.rows()["A"]
        self.assertEqual((row["source"], row["probability"], row["low_confidence"]), ("promise", "0.5000", True))
        for n, status in enumerate(["KEPT", "KEPT", "KEPT", "BROKEN"]):
            promise(inv(f"H{n}", days=60, status=Invoice.Status.PAID), status)
        row = self.rows()["A"]
        self.assertEqual((row["probability"], row["low_confidence"]), ("0.7500", False))
        self.assertEqual(by_currency(analytics.forecast(NOW))["promise_expected"], "75.00")

    def test_disputes_broken_promises_and_claims(self) -> None:
        disputed, broken, claimed = inv("D", days=10, amount="100.00"), inv("B", days=10, amount="100.00"), inv("C", days=10, amount="100.00")
        dispute(disputed)
        promise(broken, PromiseToPay.Status.BROKEN, resolved_days_ago=5)
        reply = replied(claimed, days_ago=1)
        ChaseHold.objects.create(invoice=claimed, reason=ChaseHold.Reason.CLAIMS_PAID, reply_id=reply.pk)
        rows = self.rows()
        self.assertEqual((rows["D"]["probability"], rows["D"]["expected"]), ("0.0000", "0.00"))
        self.assertEqual(rows["B"]["probability"], "0.3000")  # 0.60 x 0.5
        self.assertEqual(rows["C"]["probability"], "0.6000")  # a claim is not a payment
        self.assertTrue(any("not treated as paid" in e for e in rows["C"]["explanation"]))
        self.assertEqual(by_currency(analytics.forecast(NOW))["excluded_disputed"], "100.00")

    def test_currencies_and_reproducibility(self) -> None:
        inv("U", days=10, amount="100.00")
        inv("E", days=10, amount="100.00", currency="EUR")
        first, second = analytics.forecast(NOW), analytics.forecast(NOW)
        self.assertEqual(first, second)
        self.assertEqual([c["currency"] for c in first["currencies"]], ["EUR", "USD"])
        self.assertEqual(sum(Decimal(w) for w in by_currency(first)["weeks"]), Decimal(by_currency(first)["expected"]))


@override_settings(GOOGLE_SHEETS_SPREADSHEET_ID="sheet", GMAIL_READ_ENABLED=False)
class RefreshTests(TestCase):
    def test_snapshots_are_replaced_and_the_forecast_moves_after_payment(self) -> None:
        # Roadmap acceptance: the forecast moves after an invoice is marked PAID in the sheet and re-synced.
        due = (timezone.localdate() - dt.timedelta(days=10)).isoformat()
        sheet = lambda status: FakeSheetsClient([HEADER, row("INV-1", "Co", "ap@client.test", 500, "USD", due, status),  # noqa: E731
                                                 row("INV-2", "Co", "ap@client.test", 300, "USD", due, "UNPAID")])
        with self.captureOnCommitCallbacks(execute=True):
            sync_invoices(client=sheet("UNPAID"))
        outbox.run_pending()
        self.assertEqual(AnalyticsSnapshot.objects.count(), 5)
        before = AnalyticsSnapshot.objects.get(kind="forecast").data
        self.assertEqual(by_currency(before)["outstanding"], "800.00")
        with self.captureOnCommitCallbacks(execute=True):
            sync_invoices(client=sheet("PAID"))
        outbox.run_pending()
        after = AnalyticsSnapshot.objects.get(kind="forecast").data
        self.assertEqual(by_currency(after)["outstanding"], "300.00")
        self.assertEqual(AnalyticsSnapshot.objects.count(), 5)  # replaced, never duplicated
        snapshot = AnalyticsSnapshot.objects.get(kind="forecast")
        analytics.refresh(snapshot.computed_at)  # a repeated or restarted job: same result
        self.assertEqual(AnalyticsSnapshot.objects.get(kind="forecast").data, after)

    def test_the_job_never_drafts_or_sends(self) -> None:
        inv("A", days=40)
        before = (FollowUp.objects.count(), TimelineEvent.objects.count())
        jobs.handle_recompute_job(None)
        self.assertEqual((FollowUp.objects.count(), TimelineEvent.objects.count()), before)


class AnalyticsApiTests(SessionApiTestCase):
    role = Role.OPERATOR

    def test_reads_snapshots_and_requires_a_role(self) -> None:
        self.assertEqual(self.client.get("/api/analytics/forecast/").json()["status"], "not_computed")
        inv("A", days=10)
        analytics.refresh()
        for kind in ("aging", "dso", "cei", "attribution", "forecast", "kpis"):
            response = self.client.get(f"/api/analytics/{kind}/")
            self.assertEqual(response.status_code, 200, kind)
            self.assertIsNotNone(response.json()["computed_at"])
        self.assertEqual(self.client.get("/api/analytics/unknown/").status_code, 404)
        overview = self.client.get("/api/analytics/").json()
        self.assertEqual(set(overview["snapshots"]), {"aging", "dso_cei", "attribution", "forecast", "kpis"})
        self.client.force_login(make_user("nobody", role=None))
        self.assertEqual(self.client.get("/api/analytics/").status_code, 403)

    def test_client_profile_separates_missing_from_zero(self) -> None:
        from apps.invoices.models import Client

        from apps.insights import risk

        a = inv("A", days=20)
        inv("B", days=20, status=Invoice.Status.PAID)  # no observed paid date
        paid(inv("C", days=30), days_late=0)  # paid on time: zero days late
        make_follow_up(a, FollowUp.Status.SENT, sent_at=NOW - dt.timedelta(days=4))
        risk.recompute()
        data = self.client.get(f"/api/clients/{Client.objects.get().pk}/").json()
        history = {p["external_id"]: p for p in data["paid_history"]}
        self.assertIsNone(history["B"]["days_late"])  # missing, not zero
        self.assertEqual(history["C"]["days_late"], 0)
        self.assertEqual(data["communications"][0]["type"], "sent")
        self.assertEqual(data["promise_summary"], {"kept": 0, "broken": 0, "open": 0, "cancelled": 0})
        self.assertIsNone(data["promise_kept_rate"])  # no settled promise: no rate, not 0%

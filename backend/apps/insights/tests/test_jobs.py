import datetime as dt
import threading

from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.testing import make_user
from apps.automation import outbox, scheduling
from apps.automation.models import OutboxJob
from apps.cases import disputes, promises
from apps.insights import jobs, risk
from apps.invoices.sync import sync_invoices
from apps.invoices.tests.factories import HEADER, FakeSheetsClient, row
from apps.timeline import services as timeline
from apps.timeline.models import ActorType, EventType
from apps.timeline.testing import TimelineTransactionTestCase

from .helpers import client, factors, inv

RECOMPUTE = jobs.RECOMPUTE_JOB_KIND


def recompute_jobs() -> int:
    return OutboxJob.objects.filter(kind=RECOMPUTE).count()


def sheet(status: str = "UNPAID", days: int = 20) -> FakeSheetsClient:
    due = (timezone.localdate() - dt.timedelta(days=days)).isoformat()
    return FakeSheetsClient([HEADER, row("INV-1", "Client Co", "ap@client.test", 500, "USD", due, status)])


@override_settings(GOOGLE_SHEETS_SPREADSHEET_ID="sheet", GMAIL_READ_ENABLED=False)
class TriggerTests(TestCase):
    def test_relevant_events_request_one_recompute_after_commit(self) -> None:
        invoice = inv("INV-1")
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            timeline.record(EventType.REPLY_RECEIVED, actor=ActorType.SYSTEM, invoice=invoice)
            timeline.record(EventType.PROMISE_RECORDED, actor=ActorType.SYSTEM, invoice=invoice)
            self.assertEqual(recompute_jobs(), 0)  # nothing before the commit
        self.assertEqual(len(callbacks), 2)
        self.assertEqual(recompute_jobs(), 1)  # coalesced: one job waiting

    def test_unrelated_events_do_not(self) -> None:
        invoice = inv("INV-1")
        with self.captureOnCommitCallbacks(execute=True):
            timeline.record(EventType.DISPUTE_NOTE_ADDED, actor=ActorType.SYSTEM, invoice=invoice)
        self.assertEqual(recompute_jobs(), 0)

    def test_a_running_recompute_does_not_absorb_new_requests(self) -> None:
        first = jobs.request_recompute()
        OutboxJob.objects.filter(pk=first.pk).update(status=OutboxJob.Status.RUNNING)
        second = jobs.request_recompute()
        self.assertNotEqual(first.pk, second.pk)  # the running one may have read older data
        self.assertEqual(jobs.request_recompute().pk, second.pk)

    def test_sync_payment_and_cases_recompute_through_the_worker(self) -> None:
        operator, approver = make_user("op", Role.OPERATOR), make_user("ap", Role.APPROVER)
        with self.captureOnCommitCallbacks(execute=True):
            sync_invoices(client=sheet())
        self.assertEqual(recompute_jobs(), 1)
        outbox.run_pending()
        c = client()
        self.assertEqual((c.risk_score, c.risk_factors["outstanding"]), (factors(c)["oldest_overdue"], {"USD": "500.00"}))

        invoice = c.invoices.get()
        with self.captureOnCommitCallbacks(execute=True):
            case = disputes.open_case(invoice.pk, reason="AMOUNT", description="x", actor=operator)
        outbox.run_pending()
        self.assertEqual(factors(client())["open_dispute"], 10)

        with self.captureOnCommitCallbacks(execute=True):
            disputes.resolve(case.pk, outcome="INVALID", resolution="Fine", actor=approver)
        outbox.run_pending()
        self.assertEqual(factors(client())["open_dispute"], 0)

        with self.captureOnCommitCallbacks(execute=True):
            promises.record(invoice.pk, promised_date=timezone.localdate() + dt.timedelta(days=3), actor=operator)
        self.assertTrue(OutboxJob.objects.filter(kind=RECOMPUTE, status=OutboxJob.Status.PENDING).exists())
        outbox.run_pending()

        with self.captureOnCommitCallbacks(execute=True):
            sync_invoices(client=sheet(status="PAID"))  # the sheet, not any email, makes it paid
        outbox.run_pending()
        c = client()
        self.assertEqual(c.risk_factors["outstanding"], {})  # nothing outstanding: off the worklist
        self.assertEqual(factors(c)["oldest_overdue"], 0)
        # The payment is now measured history: 20 days late (portfolio average, low confidence) -> 7 points.
        self.assertEqual((factors(c)["days_late"], c.risk_low_confidence, c.avg_days_late), (7, True, 20))
        self.assertFalse(OutboxJob.objects.filter(kind=RECOMPUTE, status__in=("PENDING", "RUNNING")).exists())


class ScheduleTests(TestCase):
    def test_one_scheduled_recompute_per_slot_despite_restarts(self) -> None:
        now = timezone.now().replace(minute=7)
        for _ in range(3):  # worker restarts and repeated scheduling
            scheduling.schedule_periodic_jobs(now)
            scheduling.schedule_periodic_jobs(now + dt.timedelta(minutes=30))
        slot = scheduling.current_slot(now, 60).isoformat()
        jobs_ = OutboxJob.objects.filter(kind=RECOMPUTE)
        self.assertEqual(list(jobs_.values_list("idempotency_key", flat=True)), [f"{RECOMPUTE}:{slot}"])
        self.assertEqual(jobs_.get().max_attempts, 1)

    def test_rerunning_a_job_is_harmless(self) -> None:
        inv("INV-1", days=30)
        job = jobs.request_recompute()
        outbox.run_pending()
        first = client().risk_factors
        # A restart re-queues the same work: same result, no extra records.
        OutboxJob.objects.filter(pk=job.pk).update(status=OutboxJob.Status.PENDING, attempts=0)
        outbox.run_pending()
        self.assertEqual(client().risk_factors, first)
        self.assertEqual(OutboxJob.objects.get(pk=job.pk).status, OutboxJob.Status.SUCCEEDED)


class ConcurrentRecomputeTests(TimelineTransactionTestCase):
    """PostgreSQL: parallel recomputes serialize on the advisory lock and agree."""

    def test_parallel_recomputes(self) -> None:
        for n in range(5):
            inv(f"INV-{n}", days=10 * n, email=f"c{n}@client.test")
        errors: list[BaseException] = []
        barrier = threading.Barrier(4)

        def run() -> None:
            try:
                barrier.wait()
                risk.recompute()
            except BaseException as error:  # noqa: BLE001
                errors.append(error)
            finally:
                connection.close()

        threads = [threading.Thread(target=run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(client(f"c{n}@client.test").risk_score for n in range(5)), [0, 4, 8, 12, 16])

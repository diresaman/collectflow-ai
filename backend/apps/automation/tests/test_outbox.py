import datetime as dt
import threading
import time
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.testing import SessionApiTestCase
from apps.automation import outbox
from apps.automation.models import OutboxJob, WorkerHeartbeat
from apps.followups import services
from apps.followups.delivery import DuplicateDelivery, FollowUpDeliveryService
from apps.followups.models import DeliveryAttempt, FollowUp
from apps.followups.tests.helpers import FakeGmail, make_follow_up, overdue_invoice
from apps.timeline.models import TimelineEvent
from apps.timeline.testing import TimelineTransactionTestCase

calls: list[str] = []


def ok_handler(job: OutboxJob) -> dict:  # type: ignore[type-arg]
    calls.append(job.payload["name"])
    return {"done": job.payload["name"]}


def failing_handler(job: OutboxJob) -> dict:  # type: ignore[type-arg]
    raise outbox.JobFailed("not_eligible", {"detail": "nope"})


def crashing_handler(job: OutboxJob) -> dict:  # type: ignore[type-arg]
    raise RuntimeError("secret provider text")


class OutboxTestCase(TestCase):
    def setUp(self) -> None:
        calls.clear()
        outbox.register("test.ok", ok_handler)
        outbox.register("test.fail", failing_handler)
        outbox.register("test.crash", crashing_handler)


@override_settings(JOB_MAX_ATTEMPTS=3, JOB_RETRY_BASE_SECONDS=30)
class OutboxTests(OutboxTestCase):
    def test_success_records_the_result(self) -> None:
        job = outbox.enqueue("test.ok", {"name": "a"})

        self.assertEqual(outbox.run_pending(), 1)

        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts, job.result), ("SUCCEEDED", 1, {"done": "a"}))
        self.assertIsNotNone(job.completed_at)
        self.assertEqual(job.locked_by, "")

    def test_idempotency_key_returns_the_existing_job(self) -> None:
        first = outbox.enqueue("test.ok", {"name": "a"}, idempotency_key="k1")
        second = outbox.enqueue("test.ok", {"name": "b"}, idempotency_key="k1")
        self.assertEqual(first.pk, second.pk)
        outbox.run_pending()
        self.assertEqual(calls, ["a"])

    def test_permanent_failure_is_not_retried(self) -> None:
        job = outbox.enqueue("test.fail", {})
        outbox.run_pending()
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts, job.last_error_code), ("FAILED", 1, "not_eligible"))
        self.assertEqual(job.result, {"detail": "nope"})

    def test_unexpected_errors_retry_with_backoff_then_fail(self) -> None:
        job = outbox.enqueue("test.crash", {})

        with self.assertLogs("apps.automation.outbox", "ERROR"):
            outbox.run_pending()
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts, job.last_error_code), ("PENDING", 1, "RuntimeError"))
        self.assertGreater(job.run_after, timezone.now() + dt.timedelta(seconds=25))
        self.assertNotIn("secret", str(job.result))

        self.assertEqual(outbox.run_pending(), 0)  # not due yet
        for expected_status in ("PENDING", "FAILED"):
            OutboxJob.objects.filter(pk=job.pk).update(run_after=timezone.now())
            with self.assertLogs("apps.automation.outbox", "ERROR"):
                outbox.run_pending()
            job.refresh_from_db()
            self.assertEqual(job.status, expected_status)
        self.assertEqual(job.attempts, 3)

    def test_ping_job_round_trips_without_providers(self) -> None:
        job = outbox.enqueue("core.ping", {})
        outbox.run_pending()
        job.refresh_from_db()
        self.assertEqual((job.status, job.result), ("SUCCEEDED", {"pong": True}))

    def test_unknown_kind_fails(self) -> None:
        job = outbox.enqueue("test.nobody-handles-this", {})
        with self.assertLogs("apps.automation.outbox", "ERROR"):
            outbox.run_pending()
        job.refresh_from_db()
        self.assertEqual((job.status, job.last_error_code), ("FAILED", "unknown_job_kind"))

    def test_has_active_matches_payload(self) -> None:
        outbox.enqueue("test.ok", {"name": "a"})
        self.assertTrue(outbox.has_active("test.ok", name="a"))
        self.assertFalse(outbox.has_active("test.ok", name="b"))
        outbox.run_pending()
        self.assertFalse(outbox.has_active("test.ok", name="a"))

    def test_a_worker_that_lost_its_job_cannot_record_an_outcome(self) -> None:
        job = outbox.enqueue("test.ok", {"name": "a"})
        claimed = outbox.claim_next("worker-1")
        assert claimed is not None
        OutboxJob.objects.filter(pk=job.pk).update(locked_by="worker-2")  # recovered and re-claimed elsewhere

        with self.assertLogs("apps.automation.outbox", "ERROR"):
            outbox.execute(claimed, "worker-1")
        job.refresh_from_db()
        self.assertEqual((job.status, job.locked_by), ("RUNNING", "worker-2"))


@override_settings(JOB_LOCK_TIMEOUT_SECONDS=60, DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[])
class RecoveryTests(OutboxTestCase):
    def test_stale_retryable_jobs_are_requeued_and_delivery_jobs_are_failed(self) -> None:
        retryable = outbox.enqueue("test.ok", {"name": "a"})
        follow_up = make_follow_up(overdue_invoice(), status=FollowUp.Status.APPROVED)
        attempt = FollowUpDeliveryService(FakeGmail()).request(follow_up.pk)  # queues one delivery job
        delivery = OutboxJob.objects.get(kind="followups.deliver")
        while outbox.claim_next("dead-worker"):
            pass
        OutboxJob.objects.update(locked_at=timezone.now() - dt.timedelta(minutes=5))

        with self.assertLogs("apps.automation.outbox", "WARNING"):
            self.assertEqual(outbox.recover_stale(), 2)

        retryable.refresh_from_db()
        delivery.refresh_from_db()
        self.assertEqual((retryable.status, retryable.last_error_code), ("PENDING", "abandoned"))
        self.assertEqual((delivery.status, delivery.last_error_code), ("FAILED", "abandoned"))
        # The send is never retried: the reminder stays SENDING until an operator reconciles it.
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, FollowUp.Status.SENDING)
        self.assertEqual(DeliveryAttempt.objects.get(pk=attempt.pk).status, "SENDING")

    def test_fresh_running_jobs_are_left_alone(self) -> None:
        outbox.enqueue("test.ok", {"name": "a"})
        outbox.claim_next("live-worker")
        self.assertEqual(outbox.recover_stale(), 0)


class WorkerCommandTests(OutboxTestCase):
    def test_once_runs_due_jobs_and_records_a_heartbeat(self) -> None:
        outbox.enqueue("test.ok", {"name": "a"})
        outbox.enqueue("test.ok", {"name": "b"})
        out = StringIO()

        call_command("run_worker", "--once", stdout=out)

        # The two queued jobs plus the current 15-minute sequence tick slot.
        self.assertIn("Ran 3 job(s).", out.getvalue())
        self.assertEqual(OutboxJob.objects.get(kind="sequences.tick").status, "SUCCEEDED")
        self.assertEqual(sorted(calls), ["a", "b"])
        self.assertTrue(outbox.worker_alive())
        WorkerHeartbeat.objects.update(last_seen_at=timezone.now() - dt.timedelta(minutes=5))
        self.assertFalse(outbox.worker_alive())


@override_settings(DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[])
class RollbackTests(OutboxTestCase):
    def test_a_failed_enqueue_rolls_back_the_send_reservation(self) -> None:
        follow_up = make_follow_up(overdue_invoice(), status=FollowUp.Status.APPROVED)
        with mock.patch("apps.followups.delivery.outbox.enqueue", side_effect=RuntimeError("queue down")):
            with self.assertRaises(RuntimeError):
                FollowUpDeliveryService(FakeGmail()).request(follow_up.pk)
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, FollowUp.Status.APPROVED)
        self.assertFalse(DeliveryAttempt.objects.exists())
        self.assertFalse(TimelineEvent.objects.filter(type="SEND_REQUESTED").exists())


class JobApiTests(SessionApiTestCase):
    def test_job_status(self) -> None:
        job = outbox.enqueue("test.nobody", {"name": "a"})
        body = self.client.get(reverse("job-detail", args=[job.pk])).json()
        self.assertEqual((body["status"], body["kind"], body["worker_alive"]), ("PENDING", "test.nobody", False))
        self.assertEqual(self.client.get(reverse("job-detail", args=["00000000-0000-0000-0000-000000000000"])).status_code, 404)


@override_settings(DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[])
class ConcurrencyTests(TimelineTransactionTestCase):
    """Real threads, each with its own PostgreSQL connection: row locks and conditional UPDATEs must decide every race."""

    def run_threads(self, target, count: int = 4) -> None:  # type: ignore[no-untyped-def]
        barrier = threading.Barrier(count)
        errors: list[BaseException] = []

        def wrapper(index: int) -> None:
            try:
                barrier.wait()
                target(index)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=wrapper, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])

    def test_parallel_workers_claim_each_job_once(self) -> None:
        outbox.register("test.ok", ok_handler)
        calls.clear()
        for index in range(20):
            outbox.enqueue("test.ok", {"name": str(index)})

        self.run_threads(lambda index: outbox.run_pending(f"worker-{index}"))

        self.assertEqual(sorted(calls, key=int), [str(index) for index in range(20)])
        self.assertEqual(OutboxJob.objects.filter(status="SUCCEEDED", attempts=1).count(), 20)

    def test_claiming_skips_a_row_locked_by_another_transaction(self) -> None:
        outbox.register("test.ok", ok_handler)
        first = outbox.enqueue("test.ok", {"name": "first"})
        second = outbox.enqueue("test.ok", {"name": "second"})
        locked, release = threading.Event(), threading.Event()

        def hold_lock() -> None:
            try:
                with transaction.atomic():
                    OutboxJob.objects.select_for_update().get(pk=first.pk)
                    locked.set()
                    release.wait(10)
            finally:
                connection.close()

        holder = threading.Thread(target=hold_lock)
        holder.start()
        try:
            self.assertTrue(locked.wait(10))
            started = time.monotonic()
            claimed = outbox.claim_next("worker-b")  # must not wait for the other transaction
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(claimed.pk if claimed else None, second.pk)
        finally:
            release.set()
            holder.join()

    def test_parallel_approvals_have_exactly_one_winner(self) -> None:
        follow_up = make_follow_up(overdue_invoice())
        outcomes: list[str] = []

        def approve_it(_: int) -> None:
            try:
                services.approve(follow_up.pk)
                outcomes.append("approved")
            except services.InvalidState:
                outcomes.append("refused")

        self.run_threads(approve_it)
        self.assertEqual(sorted(outcomes), ["approved", "refused", "refused", "refused"])
        self.assertEqual(TimelineEvent.objects.filter(type="FOLLOW_UP_APPROVED").count(), 1)

    def test_parallel_send_requests_and_workers_call_gmail_at_most_once(self) -> None:
        follow_up = make_follow_up(overdue_invoice(), status=FollowUp.Status.APPROVED)
        gmail = FakeGmail()
        outcomes: list[str] = []

        def request(_: int) -> None:
            try:
                FollowUpDeliveryService(gmail).request(follow_up.pk)
                outcomes.append("queued")
            except DuplicateDelivery:
                outcomes.append("duplicate")

        self.run_threads(request)
        self.assertEqual(sorted(outcomes), ["duplicate", "duplicate", "duplicate", "queued"])

        with mock.patch("apps.followups.delivery.GmailClient.from_settings", return_value=gmail):
            self.run_threads(lambda index: outbox.run_pending(f"worker-{index}"))
            # Even if the job were somehow run again, a resolved attempt is never resubmitted.
            FollowUpDeliveryService(gmail).deliver(DeliveryAttempt.objects.get().pk)

        self.assertEqual(len(gmail.sent), 1)
        self.assertEqual(FollowUp.objects.get(pk=follow_up.pk).status, FollowUp.Status.SENT)
        self.assertEqual(DeliveryAttempt.objects.count(), 1)

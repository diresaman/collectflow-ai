"""Real PostgreSQL connections racing on promises, disputes and tasks (M11)."""

import threading
from typing import Any

from django.db import connection
from django.test import override_settings

from apps.cases import disputes, monitoring, promises
from apps.cases.models import DisputeCase, PromiseToPay
from apps.followups.delivery import FollowUpDeliveryService
from apps.followups.models import FollowUp
from apps.followups.services import InvoiceDisputed
from apps.followups.tests.helpers import FakeGmail, make_follow_up
from apps.inbox import services
from apps.inbox.models import Task
from apps.invoices.models import ChaseHold
from apps.timeline.models import TimelineEvent
from apps.timeline.testing import TimelineTransactionTestCase

from .helpers import chased, paid_event, today_plus, users


class CaseRaceTests(TimelineTransactionTestCase):
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
        self.operator, self.approver = users()
        self.invoice = chased()

    def test_concurrent_promises_record_one(self) -> None:
        _, errors = self.race(*[lambda: promises.record(self.invoice.pk, promised_date=today_plus(5), actor=self.operator)] * 4)
        self.assertEqual(PromiseToPay.objects.count(), 1)
        self.assertTrue(all(isinstance(e, promises.PromiseExists) for e in errors), errors)
        self.assertEqual(ChaseHold.objects.filter(promise_id__isnull=False).count(), 1)

    def test_concurrent_dispute_resolution_resolves_once(self) -> None:
        case = disputes.open_case(self.invoice.pk, reason="AMOUNT", description="x", actor=self.operator)
        _, errors = self.race(
            lambda: disputes.resolve(case.pk, outcome="VALID", resolution="a", actor=self.approver),
            lambda: disputes.resolve(case.pk, outcome="INVALID", resolution="b", actor=self.approver),
        )
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], disputes.CaseError)
        self.assertEqual(TimelineEvent.objects.filter(type="DISPUTE_RESOLVED").count(), 1)
        self.assertNotEqual(DisputeCase.objects.get().status, "OPEN")

    def test_concurrent_task_completion_completes_once(self) -> None:
        task = services.create(self.invoice.pk, title="x", actor=self.operator)
        _, errors = self.race(*[lambda: services.mark_done(task.pk, actor=self.operator)] * 4)
        self.assertEqual(errors, [])
        self.assertEqual(TimelineEvent.objects.filter(type="TASK_DONE", payload__task_id=str(task.pk)).count(), 1)
        self.assertEqual(Task.objects.get(pk=task.pk).status, "DONE")

    def test_parallel_promise_checks_settle_once(self) -> None:
        promise = promises.record(self.invoice.pk, promised_date=today_plus(0), actor=self.operator)
        PromiseToPay.objects.filter(pk=promise.pk).update(promised_date=today_plus(-5))
        self.race(*[monitoring.check_promises] * 3)
        self.assertEqual(PromiseToPay.objects.get().status, "BROKEN")
        self.assertEqual(TimelineEvent.objects.filter(type="PROMISE_BROKEN").count(), 1)
        self.assertEqual(TimelineEvent.objects.filter(type="SEQUENCE_ESCALATED").count(), 1)

    def test_promise_check_racing_a_sync_that_records_payment(self) -> None:
        promise = promises.record(self.invoice.pk, promised_date=today_plus(1), actor=self.operator)
        self.race(lambda: paid_event(self.invoice), monitoring.check_promises)
        monitoring.check_promises()  # whichever order they ran in, the next check settles it
        promise.refresh_from_db()
        self.assertEqual(promise.status, "KEPT")

    @override_settings(DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[])
    def test_dispute_versus_send_reservation(self) -> None:
        approved = make_follow_up(self.invoice, FollowUp.Status.APPROVED)
        gmail = FakeGmail()
        results, errors = self.race(
            lambda: FollowUpDeliveryService(gmail).request(approved.pk, enqueue=False),
            lambda: disputes.open_case(self.invoice.pk, reason="AMOUNT", description="x", actor=self.operator),
        )
        approved.refresh_from_db()
        case = DisputeCase.objects.get()
        self.assertEqual(case.status, "OPEN")
        if approved.status == FollowUp.Status.SENDING:
            # The reservation won the lock: the queued delivery is still refused before Gmail is called.
            attempt = approved.delivery_attempts.get()
            with self.assertRaises(InvoiceDisputed):
                FollowUpDeliveryService(gmail).deliver(attempt.pk)
        else:
            # The dispute won: the approval was withdrawn and the reservation refused.
            self.assertEqual((approved.status, approved.cancelled_reason), ("REJECTED", "dispute"))
            self.assertEqual(len(errors), 1)
        self.assertEqual(gmail.sent, [])

"""Real threads, each with its own PostgreSQL connection, racing on the same enrollment."""

import threading
from typing import Any

from django.db import connection
from django.utils import timezone

from apps.followups.models import FollowUp
from apps.inbox.models import Task
from apps.sequences import engine
from apps.sequences.models import SequenceEnrollment
from apps.timeline.models import ActorType
from apps.timeline.testing import TimelineTransactionTestCase

from .helpers import StepGemini, invoice, sent


class SequenceConcurrencyTests(TimelineTransactionTestCase):
    def race(self, target: Any, count: int = 4) -> list[Any]:
        barrier = threading.Barrier(count)
        results: list[Any] = []
        errors: list[BaseException] = []

        def run(_: int) -> None:
            try:
                barrier.wait()
                results.append(target())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        return results

    def test_parallel_enrollment_creates_one_live_enrollment(self) -> None:
        inv = invoice()
        results = self.race(lambda: engine.enroll(inv, actor=ActorType.SYSTEM))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(SequenceEnrollment.objects.count(), 1)

    def test_parallel_ticks_draft_a_step_once(self) -> None:
        inv = invoice()
        engine.reconcile(actor=None)
        enrollment_id = SequenceEnrollment.objects.get(invoice=inv).pk
        gemini = StepGemini()

        self.race(lambda: engine.advance(enrollment_id, actor=ActorType.SYSTEM, now=timezone.now(), gemini=gemini))

        self.assertEqual(len(gemini.calls), 1)
        self.assertEqual(FollowUp.objects.filter(invoice=inv).count(), 1)

    def test_parallel_ticks_create_a_task_once(self) -> None:
        inv = invoice(days=40)
        for tone, days in (("FRIENDLY", 30), ("PROFESSIONAL", 20), ("FIRM", 10)):
            sent(inv, tone, days_ago=days)
        engine.reconcile(actor=None)
        enrollment_id = SequenceEnrollment.objects.get(invoice=inv).pk

        self.race(lambda: engine.advance(enrollment_id, actor=ActorType.SYSTEM, now=timezone.now()))

        self.assertEqual(Task.objects.count(), 1)

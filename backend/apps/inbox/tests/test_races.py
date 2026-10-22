"""
PostgreSQL races between customer replies and the chasing workflow. Each thread has its own
connection, so the reply is committed while the other path is in flight, as in production.
"""

import threading
from typing import Any

from django.db import connection
from django.test import override_settings
from django.utils import timezone

from apps.followups.delivery import FollowUpDeliveryService
from apps.followups.models import FollowUp
from apps.followups.services import FollowUpError, approve
from apps.followups.tests.helpers import FakeGmail, make_follow_up
from apps.inbox import ingestion
from apps.inbox.models import InboundMessage, MailboxCursor
from apps.invoices.models import ChaseHold
from apps.sequences import engine
from apps.sequences.models import SequenceEnrollment
from apps.sequences.tests.helpers import StepGemini
from apps.timeline.models import ActorType
from apps.timeline.testing import TimelineTransactionTestCase
from integrations.mailbox import MessageRef

from .helpers import FakeMailbox, collectflow_thread, mail

CUSTOMER = "ap-inv-1@fictional.test"


def in_other_connection(target: Any) -> None:
    """Runs `target` in another thread (own connection, own commit) and waits for it."""
    errors: list[BaseException] = []

    def run() -> None:
        try:
            target()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]


class ReplyRaceTests(TimelineTransactionTestCase):
    def setUp(self) -> None:
        self.invoice, _ = collectflow_thread(days=20)  # step 2 is due
        MailboxCursor.objects.create(name="gmail", history_id="100")
        self.box = FakeMailbox()
        self.box.add(mail("m1", "thread-1", sender=CUSTOMER, text="We paid yesterday."))

    def reply_arrives(self) -> None:
        in_other_connection(lambda: ingestion.poll(self.box, owner="poller"))  # type: ignore[arg-type]

    def test_reply_during_a_running_tick_stops_its_draft(self) -> None:
        engine.reconcile(actor=ActorType.SYSTEM)
        enrollment = SequenceEnrollment.objects.get()
        SequenceEnrollment.objects.filter(pk=enrollment.pk).update(next_action_at=timezone.now())
        # The tick already decided to draft and is waiting on Gemini when the reply is stored.
        FollowUp.objects.filter(status="SENT").update(sent_at=timezone.now() - timezone.timedelta(days=30))

        class GeminiWhileReplyArrives(StepGemini):
            def generate_json(inner, **kwargs: Any) -> dict[str, Any]:  # noqa: N805
                self.reply_arrives()
                return super().generate_json(**kwargs)

        gemini = GeminiWhileReplyArrives()
        result = engine.advance(enrollment.pk, actor=ActorType.SCHEDULER, now=timezone.now(), gemini=gemini)

        self.assertEqual(len(gemini.calls), 1)
        self.assertEqual((result.outcome, result.reason), ("SKIPPED", "not_eligible"))  # type: ignore[union-attr]
        self.assertFalse(FollowUp.objects.filter(status="DRAFT").exists())
        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.drafting_started_at)
        # Later ticks skip the invoice while the hold lasts.
        later = engine.advance(enrollment.pk, actor=ActorType.SCHEDULER, now=timezone.now(), gemini=StepGemini())
        self.assertEqual(later.reason, "on_hold")  # type: ignore[union-attr]

    def test_reply_blocks_approval_of_an_existing_draft(self) -> None:
        draft = make_follow_up(self.invoice)
        self.reply_arrives()
        with self.assertRaises(FollowUpError) as caught:
            approve(draft.pk)
        self.assertIn("on hold", caught.exception.message)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "DRAFT")

    @override_settings(DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[])
    def test_reply_blocks_send_reservation(self) -> None:
        approved = make_follow_up(self.invoice, FollowUp.Status.APPROVED)
        self.reply_arrives()
        gmail = FakeGmail()
        with self.assertRaises(FollowUpError):
            FollowUpDeliveryService(gmail).request(approved.pk)
        approved.refresh_from_db()
        self.assertEqual((approved.status, gmail.sent), ("APPROVED", []))

    @override_settings(DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[])
    def test_reply_after_reservation_cancels_delivery_before_gmail(self) -> None:
        approved = make_follow_up(self.invoice, FollowUp.Status.APPROVED)
        gmail = FakeGmail()
        attempt = FollowUpDeliveryService(gmail).request(approved.pk, enqueue=False)
        self.reply_arrives()
        with self.assertRaises(FollowUpError):
            FollowUpDeliveryService(gmail).deliver(attempt.pk)
        approved.refresh_from_db()
        self.assertEqual((approved.status, gmail.sent), ("APPROVED", []))

    def test_concurrent_polls_store_a_reply_once(self) -> None:
        barrier = threading.Barrier(4)
        errors: list[BaseException] = []

        def run(index: int) -> None:
            try:
                barrier.wait()
                MailboxCursor.objects.filter(name="gmail").update(lease_owner="")  # defeat the lease: worst case
                ingestion.ingest(self.box, MessageRef("m1", "thread-1"), ingestion.known_threads(),  # type: ignore[arg-type]
                                 ingestion.PollStats())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(InboundMessage.objects.count(), 1)
        self.assertEqual(ChaseHold.objects.count(), 1)

    def test_parallel_poll_jobs_never_overlap(self) -> None:
        results: list[dict[str, Any]] = []
        barrier = threading.Barrier(3)

        def run(index: int) -> None:
            try:
                barrier.wait()
                results.append(ingestion.poll(self.box, owner=f"worker-{index}"))  # type: ignore[arg-type]
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(i,)) for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(r.get("ingested", 0) for r in results), 1)
        self.assertEqual(InboundMessage.objects.count(), 1)
        self.assertEqual(MailboxCursor.objects.get().lease_owner, "")

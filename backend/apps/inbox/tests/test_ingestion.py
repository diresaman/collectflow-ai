import datetime as dt
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.testing import run_jobs
from apps.automation.models import OutboxJob
from apps.automation.scheduling import schedule_periodic_jobs
from apps.followups.eligibility import ineligibility
from apps.inbox import ingestion
from apps.inbox.models import InboundMessage, MailboxCursor
from apps.invoices.models import ChaseHold
from apps.timeline.models import TimelineEvent

from .helpers import SENDER, FakeMailbox, collectflow_thread, mail

CUSTOMER = "ap-inv-1@fictional.test"


def poll(box: FakeMailbox, owner: str = "test") -> dict:
    return ingestion.poll(box, owner=owner)  # type: ignore[arg-type]


class BootstrapAndCursorTests(TestCase):
    def setUp(self) -> None:
        self.invoice, _ = collectflow_thread()

    def test_first_poll_scans_known_threads_and_sets_the_cursor(self) -> None:
        box = FakeMailbox(history_id="500")
        box.add(mail("m1", "thread-1", sender=CUSTOMER), in_history=False)

        result = poll(box)

        self.assertEqual((result["ingested"], result["rescan"]), (1, "bootstrap"))
        cursor = MailboxCursor.objects.get()
        self.assertEqual(cursor.history_id, "500")
        self.assertIsNotNone(cursor.bootstrapped_at)
        self.assertEqual(cursor.lease_owner, "")
        self.assertEqual(box.history_starts, [])  # no history read on the first poll

    def test_incremental_poll_pages_through_history_and_advances_the_cursor(self) -> None:
        MailboxCursor.objects.create(name="gmail", history_id="100")
        box = FakeMailbox(history_id="140")
        collectflow_thread("INV-2", thread="thread-2")
        box.add(mail("m1", "thread-1", sender=CUSTOMER))
        box.add(mail("m2", "thread-2", sender="ap-inv-2@fictional.test"))
        box.add(mail("m3", "thread-1", sender=CUSTOMER, text="Second reply"))

        result = poll(box)

        self.assertEqual((result["ingested"], result["pages"]), (3, 3))
        self.assertEqual(MailboxCursor.objects.get().history_id, "140")
        self.assertEqual(InboundMessage.objects.count(), 3)
        self.assertEqual(InboundMessage.objects.get(gmail_message_id="m2").invoice.external_id, "INV-2")

    def test_cursor_does_not_move_when_a_message_cannot_be_stored(self) -> None:
        MailboxCursor.objects.create(name="gmail", history_id="100")
        box = FakeMailbox(history_id="140")
        box.add(mail("m1", "thread-1", sender=CUSTOMER))
        box.add(mail("m2", "thread-1", sender=CUSTOMER, text="later"))
        box.fail_on_get = "m2"

        result = poll(box)

        self.assertEqual(result["error"], "mailbox_unavailable")
        cursor = MailboxCursor.objects.get()
        self.assertEqual(cursor.history_id, "100")  # unchanged: m2 is re-read next time
        self.assertEqual(cursor.last_error_code, "mailbox_unavailable")
        self.assertEqual(cursor.lease_owner, "")

        box.fail_on_get = ""
        result = poll(box)
        self.assertEqual((result["ingested"], result["duplicates"]), (1, 1))
        self.assertEqual(MailboxCursor.objects.get().history_id, "140")
        self.assertEqual(MailboxCursor.objects.get().last_error_code, "")

    def test_expired_cursor_rescans_known_threads_then_resets(self) -> None:
        MailboxCursor.objects.create(name="gmail", history_id="7")
        box = FakeMailbox(history_id="900")
        box.add(mail("m1", "thread-1", sender=CUSTOMER), in_history=False)
        box.expired = True

        result = poll(box)

        self.assertEqual((result["ingested"], result["rescan"]), (1, "history_expired"))
        cursor = MailboxCursor.objects.get()
        self.assertEqual((cursor.history_id, cursor.last_rescan_reason), ("900", "history_expired"))

    def test_overlapping_polls_are_refused_by_the_lease(self) -> None:
        MailboxCursor.objects.create(name="gmail", history_id="1", lease_owner="other",
                                     lease_expires_at=timezone.now() + dt.timedelta(minutes=5))
        self.assertEqual(poll(FakeMailbox()), {"skipped": "poll_running"})

    def test_an_expired_lease_is_taken_over(self) -> None:
        MailboxCursor.objects.create(name="gmail", history_id="1", lease_owner="crashed",
                                     lease_expires_at=timezone.now() - dt.timedelta(minutes=1))
        self.assertNotIn("skipped", poll(FakeMailbox()))

    def test_authorization_errors_are_recorded_and_nothing_is_read(self) -> None:
        box = FakeMailbox()
        box.not_authorized = True
        box.add(mail("m1", "thread-1", sender=CUSTOMER))

        result = poll(box)

        self.assertEqual(result["error"], "read_not_authorized")
        self.assertEqual(box.fetched, [])
        self.assertEqual(MailboxCursor.objects.get().last_error_code, "read_not_authorized")


class MessageFilteringTests(TestCase):
    def setUp(self) -> None:
        self.invoice, self.follow_up = collectflow_thread()
        MailboxCursor.objects.create(name="gmail", history_id="100")
        self.box = FakeMailbox(history_id="101")

    def test_unrelated_threads_are_never_fetched_or_stored(self) -> None:
        self.box.add(mail("x1", "someone-elses-thread", sender="friend@example.test"))
        result = poll(self.box)
        self.assertEqual((result["unrelated"], result["ingested"]), (1, 0))
        self.assertEqual(self.box.fetched, [])
        self.assertFalse(InboundMessage.objects.exists())

    def test_own_messages_are_skipped(self) -> None:
        self.box.add(mail("s1", "thread-1", sender=SENDER, labels=("SENT",)))       # SENT label
        self.box.add(mail("s2", "thread-1", sender=SENDER))                          # own address
        self.box.add(mail(self.follow_up.delivery_attempts.get().gmail_message_id, "thread-1", sender=SENDER))
        result = poll(self.box)
        self.assertEqual((result["own"], result["ingested"]), (3, 0))

    def test_the_same_reply_is_stored_once_across_polls(self) -> None:
        self.box.add(mail("m1", "thread-1", sender=CUSTOMER))
        poll(self.box)
        MailboxCursor.objects.update(history_id="100")  # history replayed (e.g. crash before the cursor moved)
        result = poll(self.box)
        self.assertEqual((result["ingested"], result["duplicates"]), (0, 1))
        self.assertEqual(InboundMessage.objects.count(), 1)
        self.assertEqual(OutboxJob.objects.filter(kind="inbox.classify").count(), 1)
        self.assertEqual(ChaseHold.objects.count(), 1)

    def test_messages_older_than_collectflows_first_send_are_ignored(self) -> None:
        self.box.add(mail("old", "thread-1", sender=CUSTOMER, received=timezone.now() - dt.timedelta(days=30)))
        self.assertEqual(poll(self.box)["unrelated"], 1)

    def test_sender_validation(self) -> None:
        self.box.add(mail("a", "thread-1", sender=CUSTOMER))
        self.box.add(mail("b", "thread-1", sender="colleague@fictional.test"))
        self.box.add(mail("c", "thread-1", sender="stranger@elsewhere.test"))
        poll(self.box)
        checks = dict(InboundMessage.objects.values_list("gmail_message_id", "sender_check"))
        self.assertEqual(checks, {"a": "MATCH", "b": "SAME_DOMAIN", "c": "MISMATCH"})


class StoredReplyEffectsTests(TestCase):
    def test_a_stored_reply_holds_chasing_and_queues_classification_in_one_step(self) -> None:
        invoice, follow_up = collectflow_thread()
        MailboxCursor.objects.create(name="gmail", history_id="100")
        box = FakeMailbox()
        box.add(mail("m1", "thread-1", sender=CUSTOMER,
                     text="We sent the payment today.\n\nOn Mon, AR <ar@x.test> wrote:\n> Please pay"))

        poll(box)

        reply = InboundMessage.objects.get()
        self.assertEqual(reply.body, "We sent the payment today.")
        self.assertEqual(reply.state, "RECEIVED")
        self.assertEqual(reply.replied_to, follow_up.delivery_attempts.get())
        hold = ChaseHold.objects.get()
        self.assertEqual((hold.invoice, hold.reason, hold.reply_id), (invoice, "AWAITING_CLASSIFICATION", reply.pk))
        self.assertIn("on_hold", [code for code, _ in ineligibility(invoice)])
        job = OutboxJob.objects.get(kind="inbox.classify")
        self.assertEqual(job.payload, {"reply_id": str(reply.pk), "attempt": 1})
        event = TimelineEvent.objects.get(type="REPLY_RECEIVED")
        self.assertNotIn("payment", str(event.payload))  # never the reply text

    def test_a_thread_about_several_invoices_holds_each_of_them(self) -> None:
        first, _ = collectflow_thread("INV-1", thread="shared")
        from apps.followups.tests.helpers import overdue_invoice
        from apps.sequences.tests.helpers import sent

        second = overdue_invoice(external_id="INV-2", client_email=first.client_email)
        sent(second, "FRIENDLY", days_ago=1, thread="shared")
        MailboxCursor.objects.create(name="gmail", history_id="100")
        box = FakeMailbox()
        box.add(mail("m1", "shared", sender=first.client_email))

        poll(box)

        reply = InboundMessage.objects.get()
        self.assertEqual(reply.invoice, second)  # the latest CollectFlow message in the thread
        self.assertEqual(sorted(reply.thread_invoice_ids), sorted([str(first.pk), str(second.pk)]))
        self.assertEqual(ChaseHold.objects.filter(released_at__isnull=True).count(), 2)


class PollJobAndRetentionTests(TestCase):
    def test_no_poll_is_scheduled_until_read_access_is_enabled(self) -> None:
        with override_settings(GMAIL_READ_ENABLED=False):
            schedule_periodic_jobs()
        self.assertFalse(OutboxJob.objects.filter(kind="inbox.poll").exists())
        with override_settings(GMAIL_READ_ENABLED=True):
            now = timezone.now()
            schedule_periodic_jobs(now)
            schedule_periodic_jobs(now)
        self.assertEqual(OutboxJob.objects.filter(kind="inbox.poll").count(), 1)

    def test_poll_job_does_nothing_while_read_access_is_off(self) -> None:
        with override_settings(GMAIL_READ_ENABLED=False), \
                mock.patch("apps.inbox.ingestion.MailboxReader.from_settings", side_effect=AssertionError("no read")):
            job = OutboxJob.objects.create(kind="inbox.poll", idempotency_key="poll-1", max_attempts=1)
            run_jobs()
        job.refresh_from_db()
        self.assertEqual(job.result, {"skipped": "read_not_enabled"})

    def test_bodies_older_than_retention_are_purged(self) -> None:
        collectflow_thread()
        MailboxCursor.objects.create(name="gmail", history_id="100")
        box = FakeMailbox()
        box.add(mail("m1", "thread-1", sender=CUSTOMER))
        poll(box)
        InboundMessage.objects.update(received_at=timezone.now() - dt.timedelta(days=91))

        self.assertEqual(ingestion.purge_bodies(), 1)

        reply = InboundMessage.objects.get()
        self.assertEqual(reply.body, "")
        self.assertIsNotNone(reply.body_purged_at)
        self.assertEqual(ingestion.purge_bodies(), 0)

import datetime as dt
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.automation.models import OutboxJob
from apps.followups.models import FollowUp
from apps.followups.tests.helpers import make_follow_up
from apps.inbox.models import InboundMessage, MailboxCursor, ReplyClassification, Task
from apps.inbox.preview import preview
from apps.invoices.models import ChaseHold
from apps.sequences import engine
from apps.sequences.models import SequenceEnrollment
from apps.timeline.models import ActorType, TimelineEvent

from .helpers import SENDER, FakeMailbox, collectflow_thread, mail

CUSTOMER = "ap-inv-1@fictional.test"


def table_counts() -> dict[str, int]:
    return {model.__name__: model.objects.count() for model in (
        InboundMessage, ChaseHold, OutboxJob, MailboxCursor, ReplyClassification, Task, TimelineEvent, FollowUp)}


class DiscoveryPreviewTests(TestCase):
    def setUp(self) -> None:
        self.invoice, _ = collectflow_thread()
        collectflow_thread("INV-2", thread="thread-2")
        engine.reconcile(actor=ActorType.SYSTEM)
        self.box = FakeMailbox()
        self.box.add(mail("r1", "thread-1", sender=CUSTOMER, text="We paid."), in_history=False)
        self.box.add(mail("r2", "thread-1", sender="colleague@fictional.test"), in_history=False)
        self.box.add(mail("own", "thread-1", sender=SENDER, labels=("SENT",)), in_history=False)
        self.box.add(mail("old", "thread-2", sender="ap-inv-2@fictional.test",
                          received=timezone.now() - dt.timedelta(days=20)), in_history=False)
        self.box.add(mail("x", "unrelated", sender="friend@example.test"), in_history=False)

    def test_counts_replies_invoices_and_effects(self) -> None:
        make_follow_up(self.invoice)  # a draft awaiting approval
        report = preview(self.box).as_dict()  # type: ignore[arg-type]

        self.assertEqual((report["threads_scanned"], report["would_store"]), (2, 2))
        self.assertEqual(report["skipped"], {"own_message": 1, "older_than_first_send": 1})
        self.assertEqual(report["invoices_affected"], 1)
        item = report["invoices"][0]
        self.assertEqual((item["invoice"], item["replies"], item["sender_checks"]),
                         ("INV-1", 2, {"MATCH": 1, "SAME_DOMAIN": 1}))
        self.assertIn("draft reminder can no longer be approved", item["effect"])
        self.assertTrue(item["enrollment"].startswith("active"))

    def test_changes_nothing_and_reads_headers_only(self) -> None:
        before = table_counts()
        enrollments = list(SequenceEnrollment.objects.values_list("pk", "updated_at", "next_action_at"))

        preview(self.box)  # type: ignore[arg-type]

        self.assertEqual(table_counts(), before)
        self.assertEqual(list(SequenceEnrollment.objects.values_list("pk", "updated_at", "next_action_at")), enrollments)
        self.assertEqual(self.box.fetched, [])  # no full message (body) was ever requested
        self.assertNotIn("x", self.box.metadata_fetched)  # unrelated threads are not touched at all

    def test_already_stored_replies_are_not_counted_again(self) -> None:
        from apps.inbox import ingestion

        MailboxCursor.objects.create(name="gmail", history_id="1")
        self.box.add(mail("r3", "thread-1", sender=CUSTOMER))
        ingestion.poll(self.box, owner="t")  # type: ignore[arg-type]
        report = preview(self.box).as_dict()  # type: ignore[arg-type]
        self.assertEqual(report["skipped"]["already_stored"], 1)

    @override_settings(GMAIL_READ_ENABLED=False)
    def test_command_works_while_polling_is_off_and_prints_no_addresses(self) -> None:
        out = StringIO()
        with mock.patch("apps.inbox.management.commands.preview_reply_discovery.MailboxReader.from_settings",
                        return_value=self.box) as factory:
            call_command("preview_reply_discovery", stdout=out)
        factory.assert_called_once_with(require_enabled=False)
        text = out.getvalue()
        self.assertIn("Preview only: nothing was stored or changed", text)
        self.assertIn("Replies the first poll would store: 2", text)
        self.assertNotIn(CUSTOMER, text)
        self.assertFalse(OutboxJob.objects.filter(kind="inbox.poll").exists())

    @override_settings(GMAIL_READ_ENABLED=False)
    def test_preview_reader_ignores_the_switch_but_the_poller_does_not(self) -> None:
        from integrations.mailbox import MailboxReader

        import tempfile

        with tempfile.NamedTemporaryFile() as token, override_settings(GMAIL_TOKEN_FILE=token.name,
                                                                       GMAIL_SENDER_EMAIL=SENDER):
            self.assertTrue(MailboxReader.from_settings(require_enabled=False).check_configured()[0])
            self.assertFalse(MailboxReader.from_settings().check_configured()[0])

import uuid
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.followups.delivery import (
    DuplicateDelivery,
    FollowUpDeliveryService,
    GmailUnavailable,
    RecipientNotAllowed,
)
from apps.followups.models import DeliveryAttempt, FollowUp
from apps.followups.services import FollowUpGenerationService, InvalidContent, InvalidState, StaleInvoice
from apps.invoices.models import Invoice
from integrations.gmail import GmailDeliveryUnknown, GmailRejected

from .helpers import FakeGemini, FakeGmail, ai_output, make_follow_up, overdue_invoice

TOKEN = "test-operator-token"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}

# Tests must not depend on the developer's backend/.env (e.g. a real recipient allowlist).
isolated_delivery_settings = override_settings(DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[])


def approved_follow_up(**invoice_overrides: object) -> FollowUp:
    invoice = overdue_invoice(**invoice_overrides)  # type: ignore[arg-type]
    return make_follow_up(invoice, status=FollowUp.Status.APPROVED)


def send(follow_up: FollowUp, gmail: FakeGmail):  # type: ignore[no-untyped-def]
    return FollowUpDeliveryService(gmail=gmail).send(follow_up.pk)  # type: ignore[arg-type]


@isolated_delivery_settings
class SuccessfulDeliveryTests(TestCase):
    def test_approved_reminder_is_sent_exactly_as_approved(self) -> None:
        follow_up = approved_follow_up()
        gmail = FakeGmail()

        outcome = send(follow_up, gmail)

        self.assertEqual(
            gmail.sent,
            [{"recipient": "billing@fictional.test", "subject": follow_up.subject, "body": follow_up.body}],
        )
        self.assertEqual(outcome.follow_up.status, FollowUp.Status.SENT)
        self.assertIsNotNone(outcome.follow_up.sent_at)

    def test_attempt_is_created_with_gmail_ids(self) -> None:
        follow_up = approved_follow_up()

        send(follow_up, FakeGmail())

        attempt = DeliveryAttempt.objects.get(follow_up=follow_up)
        self.assertEqual(attempt.status, DeliveryAttempt.Status.SENT)
        self.assertEqual((attempt.gmail_message_id, attempt.gmail_thread_id), ("msg-123", "thread-456"))
        self.assertEqual(attempt.recipient_email, "billing@fictional.test")
        self.assertIsNotNone(attempt.completed_at)
        self.assertEqual((attempt.error_code, attempt.error_summary), ("", ""))


@isolated_delivery_settings
class EligibilityTests(TestCase):
    def assert_not_sent(self, follow_up: FollowUp, gmail: FakeGmail) -> None:
        self.assertEqual(gmail.sent, [])
        self.assertFalse(DeliveryAttempt.objects.filter(follow_up=follow_up).exists())

    def test_draft_and_rejected_cannot_be_sent(self) -> None:
        for status in (FollowUp.Status.DRAFT, FollowUp.Status.REJECTED):
            with self.subTest(status=status):
                follow_up = make_follow_up(overdue_invoice(external_id=f"INV-{status}"), status=status)
                gmail = FakeGmail()
                with self.assertRaises(InvalidState):
                    send(follow_up, gmail)
                self.assert_not_sent(follow_up, gmail)

    def test_paid_invoice_cannot_receive_reminder(self) -> None:
        follow_up = approved_follow_up()
        Invoice.objects.filter(pk=follow_up.invoice_id).update(status=Invoice.Status.PAID)
        gmail = FakeGmail()

        with self.assertRaisesMessage(StaleInvoice, "no longer eligible"):
            send(follow_up, gmail)
        self.assert_not_sent(follow_up, gmail)

    def test_changed_snapshot_blocks_sending(self) -> None:
        follow_up = approved_follow_up()
        Invoice.objects.filter(pk=follow_up.invoice_id).update(client_email="new-address@fictional.test")
        gmail = FakeGmail()

        with self.assertRaises(StaleInvoice) as ctx:
            send(follow_up, gmail)
        self.assertEqual(ctx.exception.extra["changed_fields"], ["client_email"])
        self.assert_not_sent(follow_up, gmail)

    def test_links_in_content_block_sending(self) -> None:
        follow_up = approved_follow_up()
        FollowUp.objects.filter(pk=follow_up.pk).update(body="Pay here: https://pay.example.test/now please")
        with self.assertRaises(InvalidContent):
            send(follow_up, FakeGmail())

    @override_settings(DEBUG=True, GMAIL_ALLOWED_RECIPIENTS=[])
    def test_development_requires_recipient_allowlist(self) -> None:
        follow_up = approved_follow_up()
        with self.assertRaises(RecipientNotAllowed):
            send(follow_up, FakeGmail())

    @override_settings(DEBUG=True, GMAIL_ALLOWED_RECIPIENTS=["@fictional.test"])
    def test_allowlisted_domain_can_be_sent(self) -> None:
        self.assertEqual(send(approved_follow_up(), FakeGmail()).follow_up.status, FollowUp.Status.SENT)

    @override_settings(DEBUG=True, GMAIL_ALLOWED_RECIPIENTS=["someone@else.test"])
    def test_recipient_outside_allowlist_is_refused(self) -> None:
        with self.assertRaises(RecipientNotAllowed):
            send(approved_follow_up(), FakeGmail())


@isolated_delivery_settings
class DuplicateProtectionTests(TestCase):
    def test_second_send_is_refused_and_gmail_called_once(self) -> None:
        follow_up = approved_follow_up()
        gmail = FakeGmail()
        send(follow_up, gmail)

        with self.assertRaisesMessage(DuplicateDelivery, "already been sent"):
            send(follow_up, gmail)

        self.assertEqual(len(gmail.sent), 1)
        self.assertEqual(DeliveryAttempt.objects.filter(follow_up=follow_up).count(), 1)

    def test_concurrent_reservation_loses_cleanly(self) -> None:
        follow_up = approved_follow_up()

        def other_request_reserves_first() -> None:
            FollowUp.objects.filter(pk=follow_up.pk).update(status=FollowUp.Status.SENDING)

        gmail = FakeGmail(on_check=other_request_reserves_first)

        with self.assertRaises(DuplicateDelivery):
            send(follow_up, gmail)

        self.assertEqual(gmail.sent, [])
        self.assertFalse(DeliveryAttempt.objects.exists())

    def test_database_allows_one_sending_attempt_per_follow_up(self) -> None:
        follow_up = approved_follow_up()
        DeliveryAttempt.objects.create(follow_up=follow_up, recipient_email="a@b.test")
        with self.assertRaises(IntegrityError):
            DeliveryAttempt.objects.create(follow_up=follow_up, recipient_email="a@b.test")

    def test_in_flight_delivery_blocks_new_reminder_generation(self) -> None:
        follow_up = approved_follow_up()
        FollowUp.objects.filter(pk=follow_up.pk).update(status=FollowUp.Status.DELIVERY_UNKNOWN)
        gemini = FakeGemini(ai_output())

        with self.assertRaises(Exception):
            FollowUpGenerationService(gemini=gemini).generate(follow_up.invoice_id, regenerate=True)
        self.assertEqual(gemini.calls, [])


@isolated_delivery_settings
class FailureClassificationTests(TestCase):
    def test_gmail_not_ready_is_reported_before_reservation(self) -> None:
        follow_up = approved_follow_up()
        gmail = FakeGmail(ready=False, detail="Gmail authorization was revoked or expired.")

        with self.assertRaises(GmailUnavailable) as ctx:
            send(follow_up, gmail)

        self.assertEqual(ctx.exception.status_code, 503)
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, FollowUp.Status.APPROVED)  # still sendable later
        self.assertFalse(DeliveryAttempt.objects.exists())
        self.assertEqual(gmail.sent, [])

    def test_gmail_auth_rejection_during_send_is_failed(self) -> None:
        follow_up = approved_follow_up()
        error = GmailRejected("Gmail refused the request (HTTP 401).", code="gmail_http_401")

        with self.assertLogs("apps.followups.delivery"):
            outcome = send(follow_up, FakeGmail(error=error))

        self.assertEqual(outcome.follow_up.status, FollowUp.Status.FAILED)
        self.assertEqual(outcome.attempt.error_code, "gmail_http_401")

    def test_confirmed_pre_submission_failure_is_failed(self) -> None:
        follow_up = approved_follow_up()
        error = GmailRejected("Could not connect to Gmail; the email was not sent.", code="gmail_unreachable")

        with self.assertLogs("apps.followups.delivery"):
            outcome = send(follow_up, FakeGmail(error=error))

        self.assertEqual(outcome.follow_up.status, FollowUp.Status.FAILED)
        self.assertIsNone(outcome.follow_up.sent_at)
        self.assertEqual(outcome.attempt.status, DeliveryAttempt.Status.FAILED)
        self.assertIn("not sent", outcome.attempt.error_summary)

    def test_ambiguous_timeout_is_delivery_unknown_and_not_retried(self) -> None:
        follow_up = approved_follow_up()
        gmail = FakeGmail(error=GmailDeliveryUnknown("The connection to Gmail was interrupted."))

        with self.assertLogs("apps.followups.delivery"):
            outcome = send(follow_up, gmail)

        self.assertEqual(outcome.follow_up.status, FollowUp.Status.DELIVERY_UNKNOWN)
        self.assertEqual(outcome.attempt.status, DeliveryAttempt.Status.DELIVERY_UNKNOWN)
        with self.assertRaisesMessage(DuplicateDelivery, "unknown outcome"):
            send(follow_up, gmail)
        self.assertEqual(len(gmail.sent), 1)

    def test_unexpected_error_after_reservation_is_treated_as_unknown(self) -> None:
        follow_up = approved_follow_up()

        with self.assertLogs("apps.followups.delivery", level="ERROR"):
            outcome = send(follow_up, FakeGmail(error=RuntimeError("boom with secret details")))

        self.assertEqual(outcome.follow_up.status, FollowUp.Status.DELIVERY_UNKNOWN)
        self.assertNotIn("secret", outcome.attempt.error_summary)


@isolated_delivery_settings
class TransactionBoundaryTests(TransactionTestCase):
    def test_gmail_is_called_outside_a_transaction(self) -> None:
        follow_up = approved_follow_up()
        observed: list[bool] = []

        class ObservingGmail(FakeGmail):
            def send(self, **kwargs):  # type: ignore[no-untyped-def]
                observed.append(connection.in_atomic_block)
                # The reservation is committed before Gmail is called.
                observed.append(FollowUp.objects.get(pk=follow_up.pk).status == FollowUp.Status.SENDING)
                return super().send(**kwargs)

        send(follow_up, ObservingGmail())

        self.assertEqual(observed, [False, True])


@isolated_delivery_settings
class ReconciliationTests(TestCase):
    def test_crash_after_reservation_can_be_reconciled_manually(self) -> None:
        follow_up = approved_follow_up()
        FollowUp.objects.filter(pk=follow_up.pk).update(status=FollowUp.Status.SENDING)
        DeliveryAttempt.objects.create(follow_up=follow_up, recipient_email="billing@fictional.test")

        listing = StringIO()
        call_command("reconcile_delivery", stdout=listing)
        self.assertIn(str(follow_up.pk), listing.getvalue())

        call_command("reconcile_delivery", str(follow_up.pk), "--sent", "--message-id", "abc", stdout=StringIO())

        follow_up.refresh_from_db()
        attempt = follow_up.delivery_attempts.get()
        self.assertEqual(follow_up.status, FollowUp.Status.SENT)
        self.assertEqual((attempt.status, attempt.gmail_message_id), ("SENT", "abc"))
        self.assertEqual(attempt.error_code, "manually_reconciled")

    def test_unknown_can_be_reconciled_as_failed(self) -> None:
        follow_up = approved_follow_up()
        FollowUp.objects.filter(pk=follow_up.pk).update(status=FollowUp.Status.DELIVERY_UNKNOWN)

        call_command("reconcile_delivery", str(follow_up.pk), "--failed", stdout=StringIO())

        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, FollowUp.Status.FAILED)

    def test_approved_cannot_be_reconciled(self) -> None:
        from django.core.management.base import CommandError

        follow_up = approved_follow_up()
        with self.assertRaises(CommandError):
            call_command("reconcile_delivery", str(follow_up.pk), "--sent", stdout=StringIO())


def patch_gmail(gmail: FakeGmail):  # type: ignore[no-untyped-def]
    return mock.patch("apps.followups.delivery.GmailClient.from_settings", return_value=gmail)


@override_settings(OPERATOR_TOKEN=TOKEN, DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[])
class DeliveryApiTests(TestCase):
    def post_send(self, follow_up_id: object, gmail: FakeGmail):  # type: ignore[no-untyped-def]
        with patch_gmail(gmail):
            return self.client.post(reverse("follow-up-send", args=[follow_up_id]), **AUTH)

    def test_sent_returns_200_with_message_id(self) -> None:
        follow_up = approved_follow_up()

        response = self.post_send(follow_up.pk, FakeGmail())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["code"], "sent")
        self.assertEqual(body["follow_up"]["status"], "SENT")
        self.assertEqual(body["follow_up"]["recipient_email"], "billing@fictional.test")
        self.assertEqual(body["delivery_attempt"]["gmail_message_id"], "msg-123")
        self.assertFalse(body["requires_reconciliation"])

    def test_unknown_returns_202_requiring_reconciliation(self) -> None:
        follow_up = approved_follow_up()
        with self.assertLogs("apps.followups.delivery"):
            response = self.post_send(follow_up.pk, FakeGmail(error=GmailDeliveryUnknown("Timed out.")))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["code"], "delivery_unknown")
        self.assertTrue(response.json()["requires_reconciliation"])
        self.assertIn("Do not resend", response.json()["detail"])

    def test_failed_returns_502(self) -> None:
        follow_up = approved_follow_up()
        with self.assertLogs("apps.followups.delivery"):
            response = self.post_send(follow_up.pk, FakeGmail(error=GmailRejected("Rejected (HTTP 400).")))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["code"], "delivery_failed")

    def test_error_status_codes(self) -> None:
        draft = make_follow_up(overdue_invoice(external_id="DRAFT-1"))
        approved = approved_follow_up()
        self.assertEqual(self.post_send(draft.pk, FakeGmail()).status_code, 409)
        self.assertEqual(self.post_send(uuid.uuid4(), FakeGmail()).status_code, 404)
        self.assertEqual(self.post_send(approved.pk, FakeGmail(ready=False, detail="Not authorized.")).status_code, 503)
        self.post_send(approved.pk, FakeGmail())
        duplicate = self.post_send(approved.pk, FakeGmail())
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["code"], "duplicate_delivery")

    def test_delivery_attempts_are_listed(self) -> None:
        follow_up = approved_follow_up()
        self.post_send(follow_up.pk, FakeGmail())

        response = self.client.get(reverse("follow-up-delivery-attempts", args=[follow_up.pk]), **AUTH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([attempt["status"] for attempt in response.json()], ["SENT"])

    def test_gmail_status_endpoint(self) -> None:
        with mock.patch("apps.followups.views.GmailClient.from_settings", return_value=FakeGmail()):
            response = self.client.get(reverse("gmail-status"), **AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ready"])
        self.assertNotIn("token", response.content.decode().lower())

    def test_endpoints_require_token(self) -> None:
        follow_up = approved_follow_up()
        gmail = FakeGmail()
        requests = [
            ("post", reverse("follow-up-send", args=[follow_up.pk])),
            ("get", reverse("follow-up-delivery-attempts", args=[follow_up.pk])),
            ("get", reverse("gmail-status")),
        ]
        with patch_gmail(gmail):
            for method, url in requests:
                with self.subTest(url=url):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 401)
                    self.assertEqual(
                        getattr(self.client, method)(url, HTTP_AUTHORIZATION="Bearer wrong").status_code, 401
                    )
        self.assertEqual(gmail.sent, [])
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, FollowUp.Status.APPROVED)

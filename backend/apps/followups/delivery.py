"""
Delivery of approved follow-ups through Gmail.

Sequence:
  1. Validate (no locks): status, invoice eligibility, snapshot, recipient, content, Gmail readiness.
  2. Reserve: one short transaction with a conditional UPDATE (status APPROVED -> SENDING) plus a
     DeliveryAttempt row. Only one request can win the UPDATE, on any database.
  3. Call Gmail with no transaction open.
  4. Record the outcome with conditional updates (SENDING -> SENT / FAILED / DELIVERY_UNKNOWN).

A crash between 2 and 4 leaves both rows in SENDING, which is never retried automatically and
is resolved by an operator with `manage.py reconcile_delivery`.
"""

import logging
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.invoices.models import Invoice
from integrations.gmail import (
    DeliveryResult,
    GmailClient,
    GmailDeliveryUnknown,
    GmailNotReady,
    GmailRejected,
)

from .eligibility import changed_snapshot_fields, ineligibility_reasons
from .models import DeliveryAttempt, FollowUp
from .services import (
    URL_PATTERN,
    FollowUpError,
    InvalidContent,
    InvalidState,
    StaleInvoice,
    validate_body,
    validate_subject,
)

logger = logging.getLogger(__name__)


class GmailUnavailable(FollowUpError):
    """Gmail is not ready; confirmed that nothing was submitted."""

    status_code = 503
    code = "gmail_not_ready"


class RecipientNotAllowed(FollowUpError):
    code = "recipient_not_allowed"


class DuplicateDelivery(FollowUpError):
    code = "duplicate_delivery"


@dataclass(frozen=True)
class DeliveryOutcome:
    follow_up: FollowUp
    attempt: DeliveryAttempt


def recipient_allowed(email: str) -> bool:
    allowed = settings.GMAIL_ALLOWED_RECIPIENTS
    if not allowed:
        # In development an allowlist is mandatory, so real clients cannot be emailed by accident.
        return not settings.DEBUG
    email = email.lower()
    domain = "@" + email.rsplit("@", 1)[-1]
    return email in allowed or domain in allowed


class FollowUpDeliveryService:
    def __init__(self, gmail: GmailClient | None = None) -> None:
        self._gmail = gmail

    def send(self, follow_up_id: uuid.UUID) -> DeliveryOutcome:
        follow_up = FollowUp.objects.select_related("invoice").get(pk=follow_up_id)  # DoesNotExist -> 404
        self._validate_state(follow_up)

        invoice = Invoice.objects.get(pk=follow_up.invoice_id)
        reasons = ineligibility_reasons(invoice)
        if reasons:
            raise StaleInvoice(f"Invoice is no longer eligible: {' '.join(reasons)}")
        changed = changed_snapshot_fields(follow_up.invoice_snapshot, invoice)
        if changed:
            raise StaleInvoice(
                f"Invoice details changed since this reminder was generated ({', '.join(changed)}). "
                "Generate a new reminder.",
                changed_fields=changed,
            )

        recipient = self._validate_recipient(follow_up)
        subject, body = self._validate_content(follow_up)

        gmail = self._gmail or GmailClient.from_settings()
        status = gmail.check_ready()
        if not status.ready:
            raise GmailUnavailable(status.detail)

        attempt = self._reserve(follow_up, recipient)

        # External call: deliberately outside any transaction.
        try:
            result = gmail.send(recipient=recipient, subject=subject, body=body)
        except GmailRejected as exc:
            return self._finish(follow_up, attempt, FollowUp.Status.FAILED, error_code=exc.code, error=str(exc))
        except GmailNotReady as exc:
            return self._finish(follow_up, attempt, FollowUp.Status.FAILED, error_code=exc.code, error=str(exc))
        except GmailDeliveryUnknown as exc:
            return self._finish(
                follow_up, attempt, FollowUp.Status.DELIVERY_UNKNOWN, error_code=exc.code, error=str(exc)
            )
        except Exception:
            # Unknown failure after reservation: never assume the email was not sent.
            logger.exception("Unexpected error during Gmail delivery")
            return self._finish(
                follow_up,
                attempt,
                FollowUp.Status.DELIVERY_UNKNOWN,
                error_code="unexpected_error",
                error="An unexpected error occurred during delivery; it is unknown whether the email was sent.",
            )

        # Logged so an operator can reconcile if saving the result below fails.
        logger.info("Gmail accepted follow-up %s as message %s", follow_up.pk, result.message_id)
        return self._finish(follow_up, attempt, FollowUp.Status.SENT, result=result)

    # Validation -----------------------------------------------------------------

    @staticmethod
    def _validate_state(follow_up: FollowUp) -> None:
        status = follow_up.status
        if status == FollowUp.Status.APPROVED:
            return
        messages = {
            FollowUp.Status.SENDING: "This reminder is already being sent.",
            FollowUp.Status.SENT: "This reminder has already been sent.",
            FollowUp.Status.DELIVERY_UNKNOWN: (
                "A previous send attempt has an unknown outcome. Check Gmail and reconcile it; do not resend."
            ),
        }
        if status in messages:
            raise DuplicateDelivery(messages[status])
        raise InvalidState(f"Only approved reminders can be sent; this one is {status}.")

    @staticmethod
    def _validate_recipient(follow_up: FollowUp) -> str:
        # The recipient comes from the approved snapshot (already compared with the current invoice).
        recipient = str(follow_up.invoice_snapshot.get("client_email", "")).strip()
        try:
            validate_email(recipient)
        except ValidationError:
            raise InvalidContent("The recipient email address is invalid.") from None
        if not recipient_allowed(recipient):
            raise RecipientNotAllowed(
                f"{recipient} is not in GMAIL_ALLOWED_RECIPIENTS. During development only allowlisted "
                "test recipients can be emailed."
            )
        return recipient

    @staticmethod
    def _validate_content(follow_up: FollowUp) -> tuple[str, str]:
        subject = validate_subject(follow_up.subject)
        body = validate_body(follow_up.body)
        if URL_PATTERN.search(subject) or URL_PATTERN.search(body):
            raise InvalidContent("Reminders must not contain links.")
        # Send exactly what was approved: validation must not have altered the stored text.
        if subject != follow_up.subject or body != follow_up.body:
            raise InvalidContent("The approved content is not in a sendable form.")
        return subject, body

    # Persistence ----------------------------------------------------------------

    @staticmethod
    def _reserve(follow_up: FollowUp, recipient: str) -> DeliveryAttempt:
        now = timezone.now()
        try:
            with transaction.atomic():
                # Conditional update: only one request can move APPROVED -> SENDING.
                reserved = FollowUp.objects.filter(pk=follow_up.pk, status=FollowUp.Status.APPROVED).update(
                    status=FollowUp.Status.SENDING, updated_at=now
                )
                if reserved != 1:
                    raise DuplicateDelivery("This reminder is already being sent or is no longer approved.")
                attempt = DeliveryAttempt.objects.create(
                    follow_up_id=follow_up.pk, recipient_email=recipient, started_at=now
                )
        except IntegrityError:
            raise DuplicateDelivery("This reminder is already being sent.") from None
        follow_up.status = FollowUp.Status.SENDING
        return attempt

    @staticmethod
    def _finish(
        follow_up: FollowUp,
        attempt: DeliveryAttempt,
        status: str,
        *,
        result: DeliveryResult | None = None,
        error_code: str = "",
        error: str = "",
    ) -> DeliveryOutcome:
        now = timezone.now()
        attempt_fields = {
            "status": status,
            "completed_at": now,
            "gmail_message_id": result.message_id if result else "",
            "gmail_thread_id": (result.thread_id or "") if result else "",
            "error_code": error_code[:50],
            "error_summary": error[:500],
        }
        follow_up_fields: dict[str, object] = {"status": status, "updated_at": now}
        if status == FollowUp.Status.SENT:
            follow_up_fields["sent_at"] = now

        with transaction.atomic():
            DeliveryAttempt.objects.filter(pk=attempt.pk, status=DeliveryAttempt.Status.SENDING).update(
                **attempt_fields
            )
            updated = FollowUp.objects.filter(pk=follow_up.pk, status=FollowUp.Status.SENDING).update(
                **follow_up_fields
            )
        if updated != 1:
            logger.error("Follow-up %s was not in SENDING when recording delivery outcome %s", follow_up.pk, status)
        if status != FollowUp.Status.SENT:
            logger.warning("Delivery of follow-up %s ended as %s (%s)", follow_up.pk, status, error_code)

        follow_up.refresh_from_db()
        attempt.refresh_from_db()
        return DeliveryOutcome(follow_up=follow_up, attempt=attempt)


def reconcile(follow_up_id: uuid.UUID, *, outcome: str, message_id: str = "", note: str = "") -> FollowUp:
    """Operator-only resolution of a SENDING / DELIVERY_UNKNOWN follow-up after checking Gmail."""
    if outcome not in (FollowUp.Status.SENT, FollowUp.Status.FAILED):
        raise InvalidState("Outcome must be SENT or FAILED.")
    now = timezone.now()
    with transaction.atomic():
        follow_up = FollowUp.objects.select_for_update().get(pk=follow_up_id)
        allowed = FollowUp.RECONCILIATION_TRANSITIONS.get(follow_up.status, frozenset())
        if outcome not in allowed:
            raise InvalidState(f"A {follow_up.status} follow-up cannot be reconciled.")
        fields: dict[str, object] = {"status": outcome, "updated_at": now}
        if outcome == FollowUp.Status.SENT:
            fields["sent_at"] = now
        updated = FollowUp.objects.filter(pk=follow_up.pk, status=follow_up.status).update(**fields)
        if updated != 1:
            raise InvalidState("The follow-up changed while reconciling; try again.")
        attempt = follow_up.delivery_attempts.filter(
            status__in=[DeliveryAttempt.Status.SENDING, DeliveryAttempt.Status.DELIVERY_UNKNOWN]
        ).first()
        if attempt:
            attempt.status = outcome
            attempt.completed_at = attempt.completed_at or now
            attempt.gmail_message_id = message_id[:100] or attempt.gmail_message_id
            attempt.error_code = "manually_reconciled"
            attempt.error_summary = (f"Manually reconciled as {outcome}. {note}".strip())[:500]
            attempt.save()
    follow_up.refresh_from_db()
    return follow_up

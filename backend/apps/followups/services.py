"""
Follow-up application services: AI draft generation and the approval workflow.

No database transaction is held open while waiting for Gemini; concurrent duplicates are
prevented by re-checking under a lock at write time plus a partial unique constraint.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction

from apps.invoices.models import Invoice
from integrations.gemini import GeminiClient, GeminiError, GeminiResponseError

from . import prompts
from .eligibility import changed_snapshot_fields, ineligibility_reasons, invoice_snapshot, tone_for_days_overdue
from .models import FollowUp, GenerationAttempt, InvalidTransition

logger = logging.getLogger(__name__)

SUBJECT_MAX_LENGTH = 150
BODY_MIN_LENGTH = 20
BODY_MAX_LENGTH = 5000
REASONING_MAX_LENGTH = 1000
ACTION_MAX_LENGTH = 500
URL_PATTERN = re.compile(r"https?://|www\.", re.IGNORECASE)


# Errors -----------------------------------------------------------------------


class FollowUpError(Exception):
    """A domain error with an HTTP status and a message that is safe to return."""

    status_code = 409
    code = "conflict"

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra


class NotEligible(FollowUpError):
    code = "not_eligible"


class ActiveFollowUpExists(FollowUpError):
    code = "active_follow_up_exists"


class StaleInvoice(FollowUpError):
    code = "stale_invoice"


class InvalidState(FollowUpError):
    code = "invalid_status_transition"


class InvalidContent(FollowUpError):
    status_code = 400
    code = "invalid_content"


class AIUnavailable(FollowUpError):
    status_code = 503
    code = "ai_unavailable"


class AIBadResponse(FollowUpError):
    status_code = 502
    code = "ai_bad_response"


# Content validation -----------------------------------------------------------


@dataclass(frozen=True)
class DraftContent:
    subject: str
    body: str
    reasoning: str
    recommended_action: str


def validate_subject(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidContent("Subject is required.")
    subject = value.strip()
    if "\n" in subject or "\r" in subject:
        raise InvalidContent("Subject must be a single line.")
    if len(subject) > SUBJECT_MAX_LENGTH:
        raise InvalidContent(f"Subject must be at most {SUBJECT_MAX_LENGTH} characters.")
    return subject


def validate_body(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidContent("Body is required.")
    body = value.strip()
    if len(body) < BODY_MIN_LENGTH:
        raise InvalidContent(f"Body must be at least {BODY_MIN_LENGTH} characters.")
    if len(body) > BODY_MAX_LENGTH:
        raise InvalidContent(f"Body must be at most {BODY_MAX_LENGTH} characters.")
    return body


def validate_ai_output(data: dict[str, Any], expected_tone: str) -> DraftContent:
    """Validates Gemini's structured output. Raises AIBadResponse on any problem."""
    try:
        subject = validate_subject(data.get("subject"))
        body = validate_body(data.get("body"))
    except InvalidContent as exc:
        raise AIBadResponse(f"Gemini returned an invalid draft: {exc.message}") from None

    if data.get("tone") != expected_tone:
        raise AIBadResponse("Gemini returned a different tone than required.")

    texts: dict[str, str] = {}
    for name, limit in (("reasoning", REASONING_MAX_LENGTH), ("recommended_action", ACTION_MAX_LENGTH)):
        value = data.get(name)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
            raise AIBadResponse(f"Gemini returned an invalid {name}.")
        texts[name] = value.strip()

    # The model is told never to include payment links; enforce it deterministically.
    if URL_PATTERN.search(subject) or URL_PATTERN.search(body):
        raise AIBadResponse("Gemini included a link, which is not allowed in generated drafts.")

    return DraftContent(subject, body, texts["reasoning"], texts["recommended_action"])


# Generation -------------------------------------------------------------------


class FollowUpGenerationService:
    def __init__(self, gemini: GeminiClient | None = None) -> None:
        self._gemini = gemini

    def generate(self, invoice_id: uuid.UUID, *, regenerate: bool = False) -> FollowUp:
        invoice = Invoice.objects.get(pk=invoice_id)  # DoesNotExist -> 404 in the view

        reasons = ineligibility_reasons(invoice)
        if reasons:
            raise NotEligible(" ".join(reasons))

        existing = FollowUp.objects.filter(invoice=invoice, status__in=FollowUp.ACTIVE_STATUSES).first()
        if existing is not None:
            if existing.status != FollowUp.Status.DRAFT:
                label = existing.get_status_display().lower()
                raise ActiveFollowUpExists(
                    f"This invoice already has a reminder that is {label}; it cannot be regenerated.",
                    follow_up_id=str(existing.pk),
                )
            if not regenerate:
                raise ActiveFollowUpExists(
                    "This invoice already has a draft. Regenerate explicitly to replace it.",
                    follow_up_id=str(existing.pk),
                )

        days_overdue = invoice.days_overdue
        tone = tone_for_days_overdue(days_overdue)
        snapshot = invoice_snapshot(invoice)
        model_name = ""

        # External call: deliberately outside any transaction.
        try:
            gemini = self._gemini or GeminiClient.from_settings()
            model_name = gemini.model
            data = gemini.generate_json(
                system_instruction=prompts.SYSTEM_INSTRUCTION,
                prompt=prompts.build_prompt(prompts.build_context(invoice, days_overdue=days_overdue, tone=tone)),
                response_schema=prompts.response_schema(tone),
            )
            content = validate_ai_output(data, tone)
        except GeminiError as exc:
            error: FollowUpError = (
                AIBadResponse(str(exc)) if isinstance(exc, GeminiResponseError) else AIUnavailable(str(exc))
            )
            self._record_failure(invoice, error, model_name)
            raise error from None
        except AIBadResponse as exc:
            self._record_failure(invoice, exc, model_name)
            raise

        try:
            follow_up = self._persist(invoice, snapshot, tone, content, model_name, existing)
        except FollowUpError as exc:
            self._record_failure(invoice, exc, model_name)
            raise

        GenerationAttempt.objects.create(
            invoice=invoice, follow_up=follow_up, outcome=GenerationAttempt.Outcome.SUCCEEDED, ai_model=model_name
        )
        return follow_up

    def _persist(
        self,
        invoice: Invoice,
        snapshot: dict[str, str],
        tone: str,
        content: DraftContent,
        model_name: str,
        replacing: FollowUp | None,
    ) -> FollowUp:
        try:
            with transaction.atomic():
                current = Invoice.objects.select_for_update().get(pk=invoice.pk)
                changed = changed_snapshot_fields(snapshot, current)
                if changed:
                    raise StaleInvoice(
                        f"Invoice changed during generation ({', '.join(changed)}). Try again.",
                        changed_fields=changed,
                    )

                active = list(
                    FollowUp.objects.select_for_update().filter(invoice=current, status__in=FollowUp.ACTIVE_STATUSES)
                )
                expected_ids = {replacing.pk} if replacing else set()
                if {follow_up.pk for follow_up in active} != expected_ids:
                    raise ActiveFollowUpExists("Another reminder was created for this invoice at the same time.")

                for previous in active:  # explicit regeneration: retire the old draft
                    previous.transition_to(FollowUp.Status.REJECTED)
                    previous.save(update_fields=["status", "updated_at"])

                return FollowUp.objects.create(
                    invoice=current,
                    subject=content.subject,
                    body=content.body,
                    tone=tone,
                    reasoning=content.reasoning,
                    recommended_action=content.recommended_action,
                    invoice_snapshot=snapshot,
                    ai_model=model_name,
                )
        except IntegrityError:
            # The partial unique constraint caught a concurrent active follow-up.
            raise ActiveFollowUpExists("Another reminder was created for this invoice at the same time.") from None

    @staticmethod
    def _record_failure(invoice: Invoice, error: FollowUpError, model_name: str) -> None:
        logger.warning("Follow-up generation failed: %s", error.code)
        GenerationAttempt.objects.create(
            invoice=invoice,
            outcome=GenerationAttempt.Outcome.FAILED,
            error_code=error.code,
            error_message=error.message[:500],
            ai_model=model_name,
        )


# Review workflow --------------------------------------------------------------


def _locked(follow_up_id: uuid.UUID) -> FollowUp:
    return FollowUp.objects.select_for_update().select_related("invoice").get(pk=follow_up_id)


def update_draft(follow_up_id: uuid.UUID, changes: dict[str, Any]) -> FollowUp:
    allowed = {"subject", "body"}
    unknown = set(changes) - allowed
    if unknown:
        raise InvalidContent(f"Only subject and body can be edited (got: {', '.join(sorted(unknown))}).")
    if not changes:
        raise InvalidContent("Provide subject and/or body.")

    validated = {}
    if "subject" in changes:
        validated["subject"] = validate_subject(changes["subject"])
    if "body" in changes:
        validated["body"] = validate_body(changes["body"])
    if any(URL_PATTERN.search(value) for value in validated.values()):
        # No payment links in this MVP, whether written by the AI or an operator.
        raise InvalidContent("Links are not allowed in reminders.")

    with transaction.atomic():
        follow_up = _locked(follow_up_id)
        if follow_up.status != FollowUp.Status.DRAFT:
            raise InvalidState(f"Only drafts can be edited; this follow-up is {follow_up.status}.")
        for name, value in validated.items():
            setattr(follow_up, name, value)
        follow_up.save(update_fields=[*validated, "updated_at"])
    return follow_up


def approve(follow_up_id: uuid.UUID) -> FollowUp:
    """DRAFT -> APPROVED. Does not send anything; delivery is a separate, later step."""
    with transaction.atomic():
        follow_up = _locked(follow_up_id)
        if not follow_up.can_transition_to(FollowUp.Status.APPROVED):
            raise InvalidState(f"Cannot approve a follow-up that is {follow_up.status}.")

        invoice = Invoice.objects.select_for_update().get(pk=follow_up.invoice_id)
        reasons = ineligibility_reasons(invoice)
        if reasons:
            raise StaleInvoice(f"Invoice is no longer eligible: {' '.join(reasons)}")
        changed = changed_snapshot_fields(follow_up.invoice_snapshot, invoice)
        if changed:
            raise StaleInvoice(
                f"Invoice details changed since this draft was generated ({', '.join(changed)}). "
                "Regenerate the reminder.",
                changed_fields=changed,
            )

        follow_up.transition_to(FollowUp.Status.APPROVED)
        follow_up.save(update_fields=["status", "approved_at", "updated_at"])
    return follow_up


def reject(follow_up_id: uuid.UUID) -> FollowUp:
    with transaction.atomic():
        follow_up = _locked(follow_up_id)
        try:
            follow_up.transition_to(FollowUp.Status.REJECTED)
        except InvalidTransition:
            raise InvalidState(f"Cannot reject a follow-up that is {follow_up.status}.") from None
        follow_up.save(update_fields=["status", "updated_at"])
    return follow_up

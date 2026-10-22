"""
Follow-up application services: AI draft generation and the approval workflow.

No database transaction is held open while waiting for Gemini; concurrent duplicates are
prevented by re-checking under a lock at write time plus a partial unique constraint.
"""

import datetime as dt
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts import approval
from apps.automation import outbox
from apps.invoices.models import Invoice
from apps.timeline import services as timeline
from apps.timeline.models import EventType
from integrations.gemini import (
    GeminiClient,
    GeminiError,
    GeminiNotConfigured,
    GeminiQuotaExceeded,
    GeminiResponseError,
)

from . import prompts
from .eligibility import changed_snapshot_fields, ineligibility_reasons, invoice_snapshot, tone_for_days_overdue
from .models import FollowUp, GenerationAttempt

logger = logging.getLogger(__name__)

GENERATE_JOB_KIND = "followups.generate"

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


class ApprovalForbidden(FollowUpError):
    """Four-eyes refusal: 403 approver_required or self_approval."""

    status_code = 403

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class SubjectLocked(FollowUpError):
    status_code = 400
    code = "subject_locked"


class GenerationInProgress(FollowUpError):
    code = "generation_in_progress"


class AIUnavailable(FollowUpError):
    status_code = 503
    code = "ai_unavailable"


class AIQuotaExhausted(AIUnavailable):
    code = "ai_quota_exhausted"


class AINotConfigured(AIUnavailable):
    code = "ai_not_configured"


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


@dataclass
class DraftPlan:
    """
    What to draft for a sequence step, decided by the sequences app (never by the AI). The callbacks
    let the sequence record the outcome: `on_created` runs inside the transaction that saves the
    draft, `on_failed` after a failed attempt.
    """

    invoice_id: Any
    enrollment_id: Any
    step: Any
    tone: str
    thread_reply_to: Any
    step_number: int
    total_email_steps: int
    previous_send_date: dt.date | None
    manual: bool = False
    on_created: Callable[[FollowUp], None] = lambda follow_up: None
    # Called with the failure's error code (e.g. ai_quota_exhausted) so the sequence can back off.
    on_failed: Callable[[str], None] = lambda code: None


# The sequences app registers the planner that turns a person's "Analyze" into the invoice's next
# sequence step. The dependency points from sequences to follow-ups, never the other way.
_draft_planner: Any = None


def register_draft_planner(planner: Any) -> None:
    global _draft_planner
    _draft_planner = planner


REPLY_PREFIX = re.compile(r"^\s*((re|aw|fwd?)\s*:\s*)+", re.IGNORECASE)


def reply_subject(root_subject: str) -> str:
    """Gmail keeps a reply in the original thread only if the subject still matches it."""
    subject = f"Re: {REPLY_PREFIX.sub('', root_subject).strip()}"
    return subject[:SUBJECT_MAX_LENGTH]


def check_can_generate(invoice: Invoice, *, regenerate: bool) -> FollowUp | None:
    """Raises if no draft may be generated now; returns the draft an explicit regeneration replaces."""
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
    return existing


def request_generation(invoice_id: uuid.UUID, *, regenerate: bool, actor: Any) -> Any:
    """Checks the request now (cheap, no AI call) and queues generation for the worker."""
    invoice = Invoice.objects.get(pk=invoice_id)  # DoesNotExist -> 404 in the view
    check_can_generate(invoice, regenerate=regenerate)
    if _draft_planner is not None:
        _draft_planner.check(invoice, regenerate=regenerate)
    with transaction.atomic():
        if outbox.has_active(GENERATE_JOB_KIND, invoice_id=str(invoice.pk)):
            raise GenerationInProgress("A draft is already being generated for this invoice.")
        return outbox.enqueue(
            GENERATE_JOB_KIND,
            {"invoice_id": str(invoice.pk), "regenerate": regenerate},
            max_attempts=1,  # Gemini errors are retried inside the client; a failure is reported, not re-run
            created_by=actor,
        )


def handle_generate_job(job: Any) -> dict[str, Any]:
    payload = job.payload
    try:
        follow_up = FollowUpGenerationService().generate(
            uuid.UUID(payload["invoice_id"]), regenerate=bool(payload.get("regenerate")), actor=job.created_by
        )
    except Invoice.DoesNotExist:
        raise outbox.JobFailed("not_found", {"code": "not_found", "detail": "Invoice not found.", "http_status": 404})
    except FollowUpError as error:
        raise outbox.JobFailed(
            error.code,
            {"code": error.code, "detail": error.message, "http_status": error.status_code, **error.extra},
        ) from None
    return {"follow_up_id": str(follow_up.pk)}


class FollowUpGenerationService:
    def __init__(self, gemini: GeminiClient | None = None) -> None:
        self._gemini = gemini

    def generate(
        self, invoice_id: uuid.UUID, *, regenerate: bool = False, actor: Any = None, plan: DraftPlan | None = None
    ) -> FollowUp:
        invoice = Invoice.objects.select_related("client").get(pk=invoice_id)  # DoesNotExist -> 404 in the view
        try:
            existing = check_can_generate(invoice, regenerate=regenerate)
            if plan is None and _draft_planner is not None:
                plan = _draft_planner.plan(invoice, regenerate=regenerate, actor=actor)
        except FollowUpError as exc:
            # Refused before any AI call (not eligible, a draft already exists, sequence state):
            # not a generation attempt.
            if plan is not None:
                plan.on_failed(exc.code)
            raise
        try:
            return self._generate(invoice, existing, plan, actor)
        except Exception as exc:
            if plan is not None:
                plan.on_failed(exc.code if isinstance(exc, FollowUpError) else "unexpected_error")
            raise

    def _generate(self, invoice: Invoice, existing: FollowUp | None, plan: DraftPlan | None, actor: Any) -> FollowUp:
        days_overdue = invoice.days_overdue
        # A sequence step decides the tone; without sequences (legacy path) it follows days overdue.
        tone = plan.tone if plan is not None else tone_for_days_overdue(days_overdue)
        snapshot = invoice_snapshot(invoice)
        model_name = ""
        step_context: dict[str, Any] = {}
        if plan is not None:
            step_context = {"step_number": plan.step_number, "total_email_steps": plan.total_email_steps,
                            "previous_send_date": plan.previous_send_date}

        # External call: deliberately outside any transaction.
        try:
            gemini = self._gemini or GeminiClient.from_settings()
            model_name = gemini.model
            data = gemini.generate_json(
                system_instruction=prompts.SYSTEM_INSTRUCTION,
                prompt=prompts.build_prompt(
                    prompts.build_context(invoice, days_overdue=days_overdue, tone=tone, **step_context)
                ),
                response_schema=prompts.response_schema(tone),
            )
            content = validate_ai_output(data, tone)
        except GeminiError as exc:
            error: FollowUpError
            if isinstance(exc, GeminiResponseError):
                error = AIBadResponse(str(exc))
            elif isinstance(exc, GeminiQuotaExceeded):
                error = AIQuotaExhausted(str(exc))
            elif isinstance(exc, GeminiNotConfigured):
                error = AINotConfigured(str(exc))
            else:
                error = AIUnavailable(str(exc))
            self._record_failure(invoice, error, model_name, actor)
            raise error from None
        except AIBadResponse as exc:
            self._record_failure(invoice, exc, model_name, actor)
            raise

        try:
            follow_up = self._persist(invoice, snapshot, tone, content, model_name, existing, actor, plan)
        except FollowUpError as exc:
            self._record_failure(invoice, exc, model_name, actor)
            raise

        GenerationAttempt.objects.create(
            invoice=invoice,
            follow_up=follow_up,
            outcome=GenerationAttempt.Outcome.SUCCEEDED,
            ai_model=model_name,
            prompt_version=prompts.PROMPT_VERSION,
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
        actor: Any,
        plan: DraftPlan | None = None,
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

                # Eligibility is re-checked at write time: the invoice may have been paid meanwhile.
                reasons = ineligibility_reasons(current)
                if reasons:
                    raise NotEligible(" ".join(reasons))
                thread_parent = plan.thread_reply_to if plan is not None else None
                subject = reply_subject(thread_parent.follow_up.subject) if thread_parent else content.subject
                follow_up = FollowUp.objects.create(
                    invoice=current,
                    enrollment_id=plan.enrollment_id if plan is not None else None,
                    step=plan.step if plan is not None else None,
                    thread_reply_to=thread_parent,
                    subject=subject,
                    body=content.body,
                    tone=tone,
                    reasoning=content.reasoning,
                    recommended_action=content.recommended_action,
                    invoice_snapshot=snapshot,
                    ai_model=model_name,
                    prompt_version=prompts.PROMPT_VERSION,
                )
                timeline.record(
                    EventType.DRAFT_GENERATED,
                    actor=actor,
                    invoice=current,
                    follow_up_id=str(follow_up.pk),
                    tone=tone,
                    ai_model=model_name,
                    prompt_version=prompts.PROMPT_VERSION,
                    replaced_follow_up_id=str(replacing.pk) if replacing else None,
                    sequence_step=plan.step_number if plan is not None else None,
                    threaded=thread_parent is not None,
                    manual=plan.manual if plan is not None else None,
                )
                if plan is not None:
                    plan.on_created(follow_up)
                return follow_up
        except IntegrityError:
            # The partial unique constraint caught a concurrent active follow-up.
            raise ActiveFollowUpExists("Another reminder was created for this invoice at the same time.") from None

    @staticmethod
    def _record_failure(invoice: Invoice, error: FollowUpError, model_name: str, actor: Any) -> None:
        logger.warning("Follow-up generation failed: %s", error.code)
        with transaction.atomic():
            GenerationAttempt.objects.create(
                invoice=invoice,
                outcome=GenerationAttempt.Outcome.FAILED,
                error_code=error.code,
                error_message=error.message[:500],
                ai_model=model_name,
                prompt_version=prompts.PROMPT_VERSION,
            )
            timeline.record(EventType.DRAFT_GENERATION_FAILED, actor=actor, invoice=invoice, error_code=error.code)


# Review workflow --------------------------------------------------------------


def _locked(follow_up_id: uuid.UUID) -> FollowUp:
    return FollowUp.objects.select_for_update(of=("self",)).select_related("invoice", "step").get(pk=follow_up_id)


def update_draft(follow_up_id: uuid.UUID, changes: dict[str, Any], *, actor: Any = None) -> FollowUp:
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
        if follow_up.thread_reply_to_id and "subject" in validated and validated["subject"] != follow_up.subject:
            raise SubjectLocked(
                "This reminder continues an earlier Gmail thread, so its subject must stay "
                f'"{follow_up.subject}". Edit the body instead.'
            )
        edited = [name for name, value in validated.items() if getattr(follow_up, name) != value]
        for name, value in validated.items():
            setattr(follow_up, name, value)
        update_fields = [*validated, "updated_at"]
        if edited and _is_user(actor):
            follow_up.last_edited_by = actor
            update_fields.append("last_edited_by")
        follow_up.save(update_fields=update_fields)
        if edited:
            timeline.record(
                EventType.DRAFT_EDITED, actor=actor, invoice=follow_up.invoice,
                follow_up_id=str(follow_up.pk), fields=edited,
            )
    return follow_up


def _is_user(actor: Any) -> bool:
    return bool(getattr(actor, "is_authenticated", False))


def approve(follow_up_id: uuid.UUID, *, actor: Any = None) -> FollowUp:
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

        # Four-eyes: without an identifiable approver, only operator-level reminders can be approved.
        blocked = approval.refusal(follow_up, actor)
        if blocked is not None:
            code, message = blocked
            raise ApprovalForbidden(message, code=code)

        _conditional_transition(
            follow_up, FollowUp.Status.APPROVED,
            approved_at=timezone.now(), approved_by=actor if _is_user(actor) else None,
        )
        timeline.record(
            EventType.FOLLOW_UP_APPROVED, actor=actor, invoice=invoice, follow_up_id=str(follow_up.pk), tone=follow_up.tone
        )
    return follow_up


def reject(follow_up_id: uuid.UUID, *, actor: Any = None) -> FollowUp:
    """DRAFT -> REJECTED, or APPROVED -> REJECTED (withdraw an approval that was never sent)."""
    with transaction.atomic():
        follow_up = _locked(follow_up_id)
        if not follow_up.can_transition_to(FollowUp.Status.REJECTED):
            raise InvalidState(f"Cannot reject a follow-up that is {follow_up.status}.")
        event = EventType.APPROVAL_WITHDRAWN if follow_up.status == FollowUp.Status.APPROVED else EventType.FOLLOW_UP_REJECTED
        _conditional_transition(follow_up, FollowUp.Status.REJECTED)
        timeline.record(event, actor=actor, invoice=follow_up.invoice, follow_up_id=str(follow_up.pk))
    return follow_up


def _conditional_transition(follow_up: FollowUp, new_status: str, **fields: Any) -> None:
    """
    Applies a transition only if the status is still what was read. The row is normally locked by
    the caller (select_for_update); the status condition also protects paths that don't hold the
    lock, e.g. a withdrawal racing a send that just reserved the reminder.
    """
    now = timezone.now()
    updated = FollowUp.objects.filter(pk=follow_up.pk, status=follow_up.status).update(
        status=new_status, updated_at=now, **fields
    )
    if updated != 1:
        raise InvalidState("The follow-up changed at the same time; reload and try again.")
    follow_up.refresh_from_db()

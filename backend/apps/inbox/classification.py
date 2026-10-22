"""
Reply classification (M10): Gemini proposes a label; deterministic rules decide what happens.

A label is applied automatically only if all of these hold; otherwise the reply goes to triage and
chasing stays on hold:
- the output passed schema validation and confidence >= REPLY_CONFIDENCE_THRESHOLD (0.8);
- no sign of prompt injection (the model's own flag, or patterns found in the text);
- the sender is the invoice contact or from the same domain;
- the reply concerns only this conversation's invoice;
- the label is not OTHER, PROMISE_TO_PAY or DISPUTE (those always get a person's review);
- a label that would release the hold (REQUESTS_INFO, OUT_OF_OFFICE) is not contradicted by payment
  or dispute wording in the text.

Gemini is called with no transaction open. Calls are recorded as GenerationAttempt(purpose=CLASSIFY),
so the spending guard counts them. Classification is not blocked by the daily drafting budget (it is
safety work), but it waits out a quota cooldown. Transient failures are retried automatically at most
INBOX_CLASSIFY_MAX_ATTEMPTS times in total; the reply sits in triage meanwhile.
"""

import datetime as dt
import logging
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from apps.followups import ai_budget
from apps.followups.models import GenerationAttempt
from apps.timeline import services as timeline
from apps.timeline.models import ActorType, EventType
from integrations.gemini import (
    GeminiClient,
    GeminiError,
    GeminiNotConfigured,
    GeminiQuotaExceeded,
    GeminiResponseError,
)

from . import actions, holds, prompts
from .ingestion import enqueue_classification
from .models import InboundMessage, ReplyClassification, ReplyLabel

logger = logging.getLogger(__name__)

State = InboundMessage.State
TRANSIENT_ERRORS = ("ai_unavailable", "ai_quota_exhausted")
# Triage reasons that a later automatic attempt may still resolve.
RETRYABLE_TRIAGE = ("ai_retry_scheduled", "ai_unavailable", "ai_quota_exhausted")

INJECTION_PATTERNS = re.compile(
    r"(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|earlier|system)?\s*"
    r"(instructions|rules|prompts?)"
    r"|system\s+prompt|developer\s+(message|mode)"
    r"|you\s+are\s+(now\s+)?(an?\s+)?(ai|assistant|language\s+model|chatbot|gemini|chatgpt)\b"
    r"|(classify|label|mark|categori[sz]e)\s+(this|it|the\s+(email|message|reply))\s+as"
    r"|\bconfidence\s*[:=]|\"label\"\s*:|</?\s*(client_reply|invoice_data)\s*>",
    re.IGNORECASE,
)
# Wording that suggests a payment, promise or dispute. A label that would let chasing continue is not
# applied automatically when the text contains any of these.
SAFETY_PATTERNS = re.compile(
    r"\b(paid|payment\s+(was\s+|has\s+been\s+)?(sent|made|done|processed)|transferred|wired|remitted|settled"
    r"|will\s+pay|going\s+to\s+pay|pay\s+(it\s+)?(by|on|next|this|tomorrow)|promise"
    r"|dispute|disagree|incorrect|wrong\s+amount|overcharged|not\s+(owe|ordered|received|agreed)"
    r"|refund|credit\s+note|cancel)",
    re.IGNORECASE,
)
RELEASING_LABELS = (ReplyLabel.REQUESTS_INFO, ReplyLabel.OUT_OF_OFFICE)
REVIEW_LABELS = (ReplyLabel.PROMISE_TO_PAY, ReplyLabel.DISPUTE)


class InvalidClassification(Exception):
    pass


@dataclass(frozen=True)
class Proposal:
    label: str
    confidence: float
    suspicious: bool
    rationale: str
    extracted: dict[str, Any]


def _date(value: str, name: str) -> str:
    if not value:
        return ""
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise InvalidClassification(f"{name} is not a YYYY-MM-DD date") from None


def _amount(value: str, name: str) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"[^\d.,-]", "", value).replace(",", "")
    try:
        if Decimal(cleaned) < 0:
            raise InvalidOperation
    except InvalidOperation:
        raise InvalidClassification(f"{name} is not an amount") from None
    return value[:40]


def validate(data: dict[str, Any]) -> Proposal:
    """Checks Gemini's output against the schema and field rules. Raises InvalidClassification."""
    label = data.get("label")
    if label not in ReplyLabel.values:
        raise InvalidClassification("unknown label")
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise InvalidClassification("confidence must be a number from 0 to 1")
    suspicious = data.get("suspicious_instructions")
    if not isinstance(suspicious, bool):
        raise InvalidClassification("suspicious_instructions must be true or false")
    rationale = data.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 1000:
        raise InvalidClassification("rationale is missing or too long")
    raw = data.get("extracted")
    if not isinstance(raw, dict) or set(raw) != {*prompts.EXTRACTED_FIELDS, "invoice_numbers"}:
        raise InvalidClassification("extracted fields do not match the schema")
    extracted: dict[str, Any] = {}
    for name in prompts.EXTRACTED_FIELDS:
        value = raw[name]
        if not isinstance(value, str) or len(value) > 300:
            raise InvalidClassification(f"{name} must be a short string")
        extracted[name] = value.strip()
    for name in ("promised_date", "paid_date", "return_date"):
        extracted[name] = _date(extracted[name], name)
    for name in ("promised_amount", "paid_amount"):
        extracted[name] = _amount(extracted[name], name)
    if extracted["new_contact_email"]:
        try:
            validate_email(extracted["new_contact_email"])
        except ValidationError:
            raise InvalidClassification("new_contact_email is not an email address") from None
    numbers = raw["invoice_numbers"]
    if not isinstance(numbers, list) or len(numbers) > 20 or not all(isinstance(n, str) and len(n) <= 100 for n in numbers):
        raise InvalidClassification("invoice_numbers must be a short list of strings")
    extracted["invoice_numbers"] = [n.strip() for n in numbers if n.strip()]
    return Proposal(label, float(confidence), suspicious, rationale.strip()[:500], extracted)


def _normalise_number(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def triage_reason(reply: InboundMessage, proposal: Proposal) -> str:
    """'' if the proposal may be applied automatically, else why a person must review it."""
    text = f"{reply.subject}\n{reply.body}"
    if proposal.suspicious or INJECTION_PATTERNS.search(text):
        return "suspected_injection"
    if proposal.confidence < settings.REPLY_CONFIDENCE_THRESHOLD:
        return "low_confidence"
    if reply.sender_check == InboundMessage.SenderCheck.MISMATCH:
        return "unknown_sender"
    own = _normalise_number(reply.invoice.external_id)
    others = {n for n in map(_normalise_number, proposal.extracted["invoice_numbers"]) if n and n != own}
    if len(reply.thread_invoice_ids) > 1 or others:
        return "several_invoices"
    if proposal.label == ReplyLabel.OTHER:
        return "no_automatic_action"
    if proposal.label in REVIEW_LABELS:
        return "review_required"
    if proposal.label in RELEASING_LABELS and SAFETY_PATTERNS.search(text):
        return "possible_payment_or_dispute"
    return ""


# The job -------------------------------------------------------------------------------------------


def _ai_may_classify(reply: InboundMessage) -> bool:
    if reply.label_source == InboundMessage.Source.HUMAN:
        return False
    return reply.state == State.RECEIVED or (reply.state == State.TRIAGE and reply.triage_reason in RETRYABLE_TRIAGE)


def handle_classify_job(job: Any) -> dict[str, Any]:
    reply_id = uuid.UUID(job.payload["reply_id"])
    attempt = int(job.payload.get("attempt", 1))
    return classify(reply_id, attempt=attempt)


def classify(reply_id: uuid.UUID, *, attempt: int = 1, gemini: Any = None) -> dict[str, Any]:
    reply = InboundMessage.objects.select_related("invoice__client").get(pk=reply_id)
    if not _ai_may_classify(reply):
        return {"skipped": "already_handled", "state": reply.state}
    if not reply.body and not reply.subject:
        return _triage_without_ai(reply, "empty_reply", attempt)

    now = timezone.now()
    budget = ai_budget.status(now)
    if budget.cooldown_until is not None and now < budget.cooldown_until:
        # Calling Gemini during a quota cooldown only burns quota. Wait for it (bounded), in triage.
        return _transient_failure(reply, "ai_quota_exhausted", attempt, run_after=budget.cooldown_until, called=False)

    model_name = ""
    try:
        client = gemini or GeminiClient.from_settings()
        model_name = client.model
        data = client.generate_json(
            system_instruction=prompts.SYSTEM_INSTRUCTION,
            prompt=prompts.build_prompt(
                prompts.build_context(reply.invoice, received_date=timezone.localtime(reply.received_at).date().isoformat()),
                subject=reply.subject, body=reply.body,
            ),
            response_schema=prompts.response_schema(),
            temperature=0.0,
        )
        proposal = validate(data)
    except GeminiError as exc:
        code = ("ai_bad_response" if isinstance(exc, GeminiResponseError) else
                "ai_quota_exhausted" if isinstance(exc, GeminiQuotaExceeded) else
                "ai_not_configured" if isinstance(exc, GeminiNotConfigured) else "ai_unavailable")
        _record_attempt(reply, model_name, code)
        if code in TRANSIENT_ERRORS:
            return _transient_failure(reply, code, attempt, called=True)
        return _triage_without_ai(reply, code, attempt, model_name=model_name)
    except InvalidClassification as exc:
        logger.warning("Gemini returned an invalid reply classification: %s", exc)
        _record_attempt(reply, model_name, "ai_bad_response")
        return _triage_without_ai(reply, "invalid_output", attempt, model_name=model_name, detail=str(exc))

    _record_attempt(reply, model_name, "")
    return _apply_proposal(reply.pk, proposal, model_name, attempt)


def _record_attempt(reply: InboundMessage, model_name: str, error_code: str) -> None:
    GenerationAttempt.objects.create(
        invoice=reply.invoice, purpose=GenerationAttempt.Purpose.CLASSIFY,
        outcome=GenerationAttempt.Outcome.FAILED if error_code else GenerationAttempt.Outcome.SUCCEEDED,
        error_code=error_code, error_message=f"Reply classification failed: {error_code}"[:500] if error_code else "",
        ai_model=model_name, prompt_version=prompts.PROMPT_VERSION,
    )


def _locked(reply_id: Any) -> InboundMessage:
    return InboundMessage.objects.select_for_update(of=("self",)).select_related("invoice__client").get(pk=reply_id)


def _apply_proposal(reply_id: Any, proposal: Proposal, model_name: str, attempt: int) -> dict[str, Any]:
    with transaction.atomic():
        invoices = holds.lock_invoices(InboundMessage.objects.get(pk=reply_id).thread_invoice_ids)
        reply = _locked(reply_id)
        base = {"source": InboundMessage.Source.AI, "label": proposal.label, "confidence": proposal.confidence,
                "extracted": proposal.extracted, "rationale": proposal.rationale, "ai_model": model_name,
                "prompt_version": prompts.PROMPT_VERSION}
        if not _ai_may_classify(reply):
            # A person handled it while Gemini was working: theirs stands.
            ReplyClassification.objects.create(message=reply, decision="superseded", **base)
            return {"skipped": "handled_meanwhile"}
        reason = triage_reason(reply, proposal)
        reply.label, reply.confidence, reply.extracted = proposal.label, proposal.confidence, proposal.extracted
        reply.rationale, reply.label_source = proposal.rationale, InboundMessage.Source.AI
        reply.classify_attempts = attempt
        if reason:
            actions.hold_for_review(reply, invoices, label=proposal.label, actor=ActorType.SYSTEM, reason=reason)
            reply.state, reply.triage_reason = State.TRIAGE, reason
            done: dict[str, Any] = {"triage": reason}
        else:
            done = actions.apply_label(reply, invoices, label=proposal.label, extracted=proposal.extracted,
                                       actor=ActorType.SYSTEM, by_person=False)
            reply.state, reply.triage_reason = State.ACTIONED, ""
        reply.save()
        ReplyClassification.objects.create(message=reply, decision=f"triage:{reason}" if reason else "applied", **base)
        event = EventType.REPLY_TRIAGED if reason else EventType.REPLY_CLASSIFIED
        for invoice in invoices:
            timeline.record(event, actor=ActorType.SYSTEM, invoice=invoice, reply_id=str(reply.pk),
                            label=proposal.label, confidence=proposal.confidence, triage_reason=reason,
                            prompt_version=prompts.PROMPT_VERSION)
    return {"reply_id": str(reply.pk), "label": proposal.label, "confidence": proposal.confidence,
            "state": reply.state, "triage_reason": reason, "actions": _jsonable(done)}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value if isinstance(value, (str, int, float, bool, type(None))) else str(value)


def _triage_without_ai(reply: InboundMessage, reason: str, attempt: int, *, model_name: str = "",
                       detail: str = "") -> dict[str, Any]:
    """No usable AI result: a person classifies it. Chasing stays held."""
    with transaction.atomic():
        invoices = holds.lock_invoices(reply.thread_invoice_ids)
        locked = _locked(reply.pk)
        if not _ai_may_classify(locked):
            return {"skipped": "handled_meanwhile"}
        actions.hold_for_review(locked, invoices, label="", actor=ActorType.SYSTEM, reason=reason)
        locked.state, locked.triage_reason, locked.classify_attempts = State.TRIAGE, reason, attempt
        locked.save(update_fields=["state", "triage_reason", "classify_attempts", "updated_at"])
        ReplyClassification.objects.create(
            message=locked, source=InboundMessage.Source.AI, decision=f"failed:{reason}", error_code=reason[:50],
            ai_model=model_name, prompt_version=prompts.PROMPT_VERSION, note=detail[:300],
        )
        for invoice in invoices:
            timeline.record(EventType.REPLY_TRIAGED, actor=ActorType.SYSTEM, invoice=invoice, reply_id=str(locked.pk),
                            triage_reason=reason)
    return {"reply_id": str(reply.pk), "state": State.TRIAGE, "triage_reason": reason}


def _transient_failure(reply: InboundMessage, code: str, attempt: int, *, run_after: dt.datetime | None = None,
                       called: bool) -> dict[str, Any]:
    """Gemini busy or out of quota: retry later if attempts remain; the reply is in triage meanwhile."""
    if attempt >= settings.INBOX_CLASSIFY_MAX_ATTEMPTS:
        return _triage_without_ai(reply, code, attempt)
    delay = ai_budget.backoff(attempt, base_minutes=settings.AI_RETRY_BASE_MINUTES)
    when = max(run_after or timezone.now(), timezone.now() + delay) if called else (run_after or timezone.now() + delay)
    with transaction.atomic():
        invoices = holds.lock_invoices(reply.thread_invoice_ids)
        locked = _locked(reply.pk)
        if not _ai_may_classify(locked):
            return {"skipped": "handled_meanwhile"}
        actions.hold_for_review(locked, invoices, label="", actor=ActorType.SYSTEM, reason="ai_retry_scheduled")
        locked.state, locked.triage_reason, locked.classify_attempts = State.TRIAGE, "ai_retry_scheduled", attempt
        locked.save(update_fields=["state", "triage_reason", "classify_attempts", "updated_at"])
        ReplyClassification.objects.create(
            message=locked, source=InboundMessage.Source.AI, decision="retry_scheduled", error_code=code,
            prompt_version=prompts.PROMPT_VERSION, note=f"Next automatic attempt after {when.isoformat()}"[:300],
        )
        enqueue_classification(locked, attempt=attempt + 1, run_after=when)
    return {"reply_id": str(reply.pk), "state": State.TRIAGE, "triage_reason": "ai_retry_scheduled",
            "retry_after": when.isoformat(), "error": code}

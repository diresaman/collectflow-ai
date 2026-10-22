"""
People's decisions on customer replies (M10). Permissions are enforced here, not only in the UI:

- Any operator may classify or correct a reply, confirm the AI's label, or ask the AI to try again.
- Resuming chasing after a promise, dispute or payment claim (releasing a PROMISE_TO_PAY, DISPUTE or
  CLAIMS_PAID hold, directly or by re-labelling the reply as something that releases it) needs an
  APPROVER, with a note.

A person's label is applied with the same deterministic actions as a confident AI label (see
actions.py) and recorded in the append-only ReplyClassification log alongside the AI's proposals.
"""

import datetime as dt
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.roles import has_role
from apps.automation import outbox
from apps.followups.services import FollowUpError
from apps.timeline import services as timeline
from apps.timeline.models import EventType

from . import actions, holds, prompts
from .ingestion import CLASSIFY_JOB_KIND, enqueue_classification
from .models import InboundMessage, ReplyClassification, ReplyLabel

State = InboundMessage.State
TEXT_FIELDS = ("promised_amount", "paid_amount", "payment_reference", "dispute_reason", "requested_info",
               "new_contact_email")
DATE_FIELDS = ("promised_date", "paid_date", "return_date")


class TriageError(FollowUpError):
    code = "triage_conflict"


class InvalidTriage(TriageError):
    status_code = 400
    code = "invalid_input"


class TriageForbidden(TriageError):
    status_code = 403
    code = "approver_required"


def _require(actor: Any, role: str) -> None:
    if not has_role(actor, role):
        if role == Role.APPROVER:
            raise TriageForbidden("Only an approver can resume chasing after a promise, dispute or payment claim.")
        raise TriageForbidden("Your role does not allow this action.")


def clean_extracted(raw: Any, base: dict[str, Any]) -> dict[str, Any]:
    """A person's corrections to the extracted fields, merged over the current values."""
    if raw in (None, ""):
        return dict(base)
    if not isinstance(raw, dict):
        raise InvalidTriage("extracted must be an object.")
    unknown = set(raw) - {*TEXT_FIELDS, *DATE_FIELDS, "invoice_numbers"}
    if unknown:
        raise InvalidTriage(f"Unknown extracted fields: {', '.join(sorted(unknown))}.")
    merged = dict(base)
    for name, value in raw.items():
        if name == "invoice_numbers":
            if not isinstance(value, list) or not all(isinstance(v, str) and len(v) <= 100 for v in value):
                raise InvalidTriage("invoice_numbers must be a list of invoice numbers.")
            merged[name] = [v.strip() for v in value if v.strip()][:20]
            continue
        if not isinstance(value, str) or len(value) > 300:
            raise InvalidTriage(f"{name} must be text of at most 300 characters.")
        value = value.strip()
        if name in DATE_FIELDS and value:
            try:
                value = dt.date.fromisoformat(value).isoformat()
            except ValueError:
                raise InvalidTriage(f"{name} must be a date (YYYY-MM-DD).") from None
        merged[name] = value
    return merged


def _lock(reply_id: Any) -> tuple[InboundMessage, list[Any]]:
    ids = InboundMessage.objects.values_list("thread_invoice_ids", flat=True).get(pk=reply_id)  # DoesNotExist -> 404
    invoices = holds.lock_invoices(ids)
    reply = InboundMessage.objects.select_for_update(of=("self",)).select_related("invoice__client").get(pk=reply_id)
    return reply, invoices


def _protected_holds(reply: InboundMessage) -> list[Any]:
    return [hold for hold in holds.active_for_reply(reply) if hold.protected]


def classify(reply_id: Any, *, label: Any, extracted: Any = None, note: Any = "", actor: Any) -> InboundMessage:
    """A person classifies (or confirms, or corrects) a reply; the label's action is applied."""
    _require(actor, Role.OPERATOR)
    if label not in ReplyLabel.values:
        raise InvalidTriage(f"label must be one of: {', '.join(ReplyLabel.values)}.")
    note = (note or "").strip() if isinstance(note, str) else ""
    with transaction.atomic():
        reply, invoices = _lock(reply_id)
        values = clean_extracted(extracted, reply.extracted)
        releases = label not in actions.HOLDING_LABELS
        if releases and _protected_holds(reply):
            _require(actor, Role.APPROVER)
            if not note:
                raise InvalidTriage("Add a note explaining why chasing may resume.")
        previous = {"label": reply.label, "source": reply.label_source, "state": reply.state}
        done = actions.apply_label(reply, invoices, label=label, extracted=values, actor=actor, by_person=True)
        reply.label, reply.label_source, reply.extracted = label, InboundMessage.Source.HUMAN, values
        reply.confidence = None
        reply.state, reply.triage_reason = State.RESOLVED, ""
        reply.resolved_at, reply.resolved_by = timezone.now(), actor
        reply.save()
        ReplyClassification.objects.create(
            message=reply, source=InboundMessage.Source.HUMAN, label=label, extracted=values, decision="human",
            actor=actor, note=note[:300], prompt_version=prompts.PROMPT_VERSION,
        )
        for invoice in invoices:
            timeline.record(EventType.REPLY_REVIEWED, actor=actor, invoice=invoice, reply_id=str(reply.pk),
                            label=label, previous_label=previous["label"], previous_source=previous["source"],
                            corrected=bool(previous["label"]) and previous["label"] != label,
                            hold=done.get("hold"), released_holds=done.get("released_holds"))
    return reply


def release_hold(reply_id: Any, *, note: Any = "", actor: Any) -> InboundMessage:
    """A person resumes chasing for the invoices this reply put on hold."""
    _require(actor, Role.OPERATOR)
    note = (note or "").strip() if isinstance(note, str) else ""
    with transaction.atomic():
        reply, invoices = _lock(reply_id)
        active = holds.active_for_reply(reply)
        if not active:
            raise TriageError("This reply has no active hold.")
        if any(hold.protected for hold in active):
            _require(actor, Role.APPROVER)
            if not note:
                raise InvalidTriage("Add a note explaining why chasing may resume.")
        holds.release(reply, actor=actor, note=note or "Released by a person.")
        if reply.state in (State.RECEIVED, State.TRIAGE):
            reply.state, reply.triage_reason = State.RESOLVED, ""
            reply.resolved_at, reply.resolved_by = timezone.now(), actor
            reply.save(update_fields=["state", "triage_reason", "resolved_at", "resolved_by", "updated_at"])
        ReplyClassification.objects.create(
            message=reply, source=InboundMessage.Source.HUMAN, label=reply.label, extracted=reply.extracted,
            decision="hold_released", actor=actor, note=note[:300], prompt_version=prompts.PROMPT_VERSION,
        )
        for invoice in invoices:
            timeline.record(EventType.REPLY_REVIEWED, actor=actor, invoice=invoice, reply_id=str(reply.pk),
                            label=reply.label, action="hold_released")
    return reply


def retry_classification(reply_id: Any, *, actor: Any) -> Any:
    """Queues one more AI attempt for a reply in triage that no person has classified yet."""
    _require(actor, Role.OPERATOR)
    with transaction.atomic():
        reply, _ = _lock(reply_id)
        if reply.label_source == InboundMessage.Source.HUMAN or reply.state not in (State.TRIAGE, State.RECEIVED):
            raise TriageError("Only replies awaiting review that no person has classified can be re-classified.")
        queued = outbox.active_job(CLASSIFY_JOB_KIND, reply_id=str(reply.pk))
        if queued is not None:
            return queued
        reply.state, reply.triage_reason = State.RECEIVED, ""
        reply.save(update_fields=["state", "triage_reason", "updated_at"])
        return enqueue_classification(reply, attempt=reply.classify_attempts + 1, created_by=actor)

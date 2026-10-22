"""People's actions on sequences: new versions, pause/resume, and reviewing an enrollment's position."""

from typing import Any

from django.db import transaction
from django.db.models import Max

from apps.followups.models import FollowUp
from apps.followups.services import FollowUpError
from apps.timeline import services as timeline
from apps.timeline.models import EventType

from . import engine
from .models import Channel, Sequence, SequenceEnrollment, SequenceStep

Status = SequenceEnrollment.Status


class InvalidSequence(FollowUpError):
    status_code = 400
    code = "invalid_sequence"


class EnrollmentConflict(FollowUpError):
    code = "enrollment_conflict"


def create_version(*, name: str, steps: list[dict[str, Any]], make_default: bool, actor: Any) -> Sequence:
    """Saves a new immutable version of a sequence (2–5 steps, increasing offsets)."""
    name = (name or "").strip()
    if not name or len(name) > 100:
        raise InvalidSequence("A sequence needs a name of at most 100 characters.")
    if not isinstance(steps, list) or not 2 <= len(steps) <= 5:
        raise InvalidSequence("A sequence has 2 to 5 steps.")
    cleaned = []
    previous_offset = -1
    for index, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            raise InvalidSequence(f"Step {index} is not an object.")
        channel = raw.get("channel")
        tone = raw.get("tone") or ""
        offset = raw.get("offset_days")
        if channel not in Channel.values:
            raise InvalidSequence(f"Step {index}: channel must be EMAIL or TASK.")
        if channel == Channel.EMAIL and tone not in ("FRIENDLY", "PROFESSIONAL", "FIRM"):
            raise InvalidSequence(f"Step {index}: an email step needs a tone (FRIENDLY, PROFESSIONAL or FIRM).")
        if channel == Channel.TASK and tone:
            raise InvalidSequence(f"Step {index}: a task step has no tone.")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or offset <= previous_offset:
            raise InvalidSequence(f"Step {index}: offset_days must be a whole number larger than the previous step's.")
        previous_offset = offset
        cleaned.append(SequenceStep(order=index, offset_days=offset, channel=channel, tone=tone,
                                    requires_approver=bool(raw.get("requires_approver", False))))

    with transaction.atomic():
        version = (Sequence.objects.select_for_update().filter(name=name).aggregate(m=Max("version"))["m"] or 0) + 1
        if make_default:
            Sequence.objects.filter(is_default=True).update(is_default=False)
        sequence = Sequence.objects.create(name=name, version=version, is_default=make_default, active=True,
                                           created_by=actor if getattr(actor, "is_authenticated", False) else None)
        for step in cleaned:
            step.sequence = sequence
        SequenceStep.objects.bulk_create(cleaned)
    return sequence


def _locked(enrollment_id: Any) -> SequenceEnrollment:
    return (
        SequenceEnrollment.objects.select_for_update(of=("self",)).select_related("invoice", "sequence")
        .get(pk=enrollment_id)
    )


def pause(enrollment_id: Any, *, reason: str, actor: Any) -> SequenceEnrollment:
    reason = (reason or "").strip()
    if not reason:
        raise InvalidSequence("Give a reason for pausing.")
    with transaction.atomic():
        enrollment = _locked(enrollment_id)
        if enrollment.status != Status.ACTIVE:
            raise EnrollmentConflict(f"Only an active sequence can be paused; this one is {enrollment.status}.")
        enrollment.status = Status.PAUSED
        enrollment.pause_reason = reason[:200]
        enrollment.save(update_fields=["status", "pause_reason", "updated_at"])
        timeline.record(EventType.ENROLLMENT_PAUSED, actor=actor, invoice=enrollment.invoice,
                        enrollment_id=str(enrollment.pk), reason=enrollment.pause_reason)
    return enrollment


def resume(enrollment_id: Any, *, actor: Any) -> SequenceEnrollment:
    with transaction.atomic():
        enrollment = _locked(enrollment_id)
        if enrollment.status != Status.PAUSED:
            raise EnrollmentConflict(f"Only a paused sequence can be resumed; this one is {enrollment.status}.")
        if enrollment.pause_reason == "history_review":
            raise EnrollmentConflict("This sequence is waiting for a history review. An approver must review it.")
        enrollment.status = Status.ACTIVE
        enrollment.pause_reason = ""
        enrollment.save(update_fields=["status", "pause_reason", "updated_at"])
        if enrollment.pending_step is None:
            engine._schedule_next(enrollment)
        timeline.record(EventType.ENROLLMENT_RESUMED, actor=actor, invoice=enrollment.invoice,
                        enrollment_id=str(enrollment.pk))
    return enrollment


def review(enrollment_id: Any, *, last_completed_step: int, note: str, actor: Any) -> SequenceEnrollment:
    """
    An approver confirms (or corrects) where an invoice with pre-sequence history stands. Only a
    person can place an invoice before a firm step when its history was ambiguous.
    """
    with transaction.atomic():
        enrollment = _locked(enrollment_id)
        if enrollment.status not in SequenceEnrollment.LIVE_STATUSES or not enrollment.review_required:
            raise EnrollmentConflict("This sequence has no history to review.")
        unresolved = FollowUp.objects.filter(
            invoice=enrollment.invoice, status__in=[FollowUp.Status.SENDING, FollowUp.Status.DELIVERY_UNKNOWN]
        ).exists()
        if unresolved:
            raise EnrollmentConflict("A delivery for this invoice is unresolved. Reconcile it first.")
        steps = engine.steps_of(enrollment.sequence)
        if not isinstance(last_completed_step, int) or isinstance(last_completed_step, bool) or not 0 <= last_completed_step <= len(steps):
            raise InvalidSequence(f"last_completed_step must be between 0 and {len(steps)}.")
        if enrollment.pending_step is not None and enrollment.pending_step <= last_completed_step:
            raise InvalidSequence(f"Step {enrollment.pending_step} is in progress; the position must be before it.")

        enrollment.last_completed_step = last_completed_step
        enrollment.review_required = False
        enrollment.blocked_reason = "" if enrollment.blocked_reason == "delivery_unknown" else enrollment.blocked_reason
        if enrollment.status == Status.PAUSED and enrollment.pause_reason == "history_review":
            enrollment.status = Status.ACTIVE
            enrollment.pause_reason = ""
        enrollment.save(update_fields=["last_completed_step", "review_required", "blocked_reason", "status",
                                       "pause_reason", "updated_at"])
        if enrollment.pending_step is None:
            engine._schedule_next(enrollment)
        timeline.record(EventType.ENROLLMENT_REVIEWED, actor=actor, invoice=enrollment.invoice,
                        enrollment_id=str(enrollment.pk), last_completed_step=last_completed_step,
                        note=(note or "")[:300], previous_reason=enrollment.review_reason)
    return enrollment

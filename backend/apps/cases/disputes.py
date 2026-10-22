"""
Dispute workflow (M11, v2 §6).

- Any operator opens a case (from a DISPUTE reply or manually) with a reason and description. Opening it
  withdraws the invoice's unsent reminders (the sequence step stays pending), places a chase hold owned by
  the case, hands the reply's own hold over to it, and creates a review task for an approver.
- While OPEN, eligibility reports `invoice_disputed`, so drafting, approval, send reservation, delivery
  and the sequence tick all refuse (409 invoice_disputed): one rule covers every chasing path.
- Operators assign an owner and add internal notes. Only an APPROVER resolves, with a written resolution:
  RESOLVED_VALID exits the sequence (no re-enrollment until the sheet changes the invoice);
  RESOLVED_INVALID releases the case's hold. Neither sends anything, and neither touches other holds:
  chasing resumes only when the invoice is eligible again.
"""

import datetime as dt
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.followups.eligibility import ineligibility
from apps.followups.services import cancel_unsent
from apps.inbox import holds
from apps.inbox.models import ReplyLabel, Task
from apps.invoices.models import ChaseHold, Invoice
from apps.sequences import engine
from apps.sequences.models import SequenceEnrollment
from apps.timeline import services as timeline
from apps.timeline.models import EventType

from .common import (
    CaseError,
    InvalidCase,
    lock_case_rows,
    require,
    require_chaseable,
    settle_reply,
    text,
    user_with_role,
)
from .models import DisputeCase, DisputeNote

Status = DisputeCase.Status
OUTCOMES = {"VALID": Status.RESOLVED_VALID, "INVALID": Status.RESOLVED_INVALID}


class DisputeExists(CaseError):
    code = "dispute_exists"


def open_case(invoice_id: Any, *, reason: Any, description: Any, reply_id: Any = None, owner_id: Any = None,
              actor: Any) -> DisputeCase:
    require(actor, Role.OPERATOR, "open a dispute")
    if reason not in DisputeCase.Reason.values:
        raise InvalidCase(f"reason must be one of: {', '.join(DisputeCase.Reason.values)}.")
    description = text(description, "description", required=True, limit=1000)
    owner = user_with_role(owner_id, "owner")
    try:
        with transaction.atomic():
            invoice, reply = lock_case_rows(invoice_id, reply_id)
            if reply is not None:
                existing = DisputeCase.objects.filter(reply=reply).first()
                if existing is not None:
                    return existing  # the same reply handled twice: one case
            require_chaseable(invoice)
            if DisputeCase.objects.filter(invoice=invoice, status=Status.OPEN).exists():
                raise DisputeExists("This invoice already has an open dispute.")
            case = DisputeCase.objects.create(invoice=invoice, client=invoice.client, reply=reply, reason=reason,
                                              description=description, owner=owner, opened_by=actor)
            withdrawn = cancel_unsent(invoice.pk, reason="dispute", actor=actor)
            holds.place_owned(invoice, owner="dispute", owner_id=case.pk, reason=ChaseHold.Reason.DISPUTE, actor=actor,
                              detail=f"Disputed: {case.get_reason_display().lower()}.")
            if reply is not None:
                settle_reply(reply, invoice, label=ReplyLabel.DISPUTE, decision="dispute_opened", actor=actor,
                             note=description, extracted={"dispute_reason": description[:300]})
            task = Task.objects.create(
                kind=Task.Kind.REVIEW_DISPUTE, invoice=invoice, client=invoice.client, dispute=case, reply=reply,
                title=f"Review the dispute on invoice {invoice.external_id}", created_by=actor,
                detail=f"{case.get_reason_display()}: {description}"[:500], due_at=_task_due(),
            )
            timeline.record(EventType.TASK_CREATED, actor=actor, invoice=invoice, task_id=str(task.pk),
                            kind=task.kind, dispute_id=str(case.pk))
            timeline.record(EventType.DISPUTE_OPENED, actor=actor, invoice=invoice, dispute_id=str(case.pk),
                            reason=reason, reply_id=str(reply.pk) if reply else None,
                            owner=owner.username if owner else None,
                            withdrawn_follow_up_ids=[str(f.pk) for f in withdrawn])
    except IntegrityError:
        raise DisputeExists("A dispute was opened for this invoice at the same time.") from None
    return case


def _task_due() -> dt.datetime:
    return timezone.now() + dt.timedelta(days=settings.TASK_DUE_DAYS)


def _locked(case_id: Any) -> DisputeCase:
    return DisputeCase.objects.select_for_update(of=("self",)).select_related("invoice__client").get(pk=case_id)


def assign(case_id: Any, *, owner_id: Any, actor: Any) -> DisputeCase:
    require(actor, Role.OPERATOR, "assign a dispute")
    owner = user_with_role(owner_id, "owner")
    with transaction.atomic():
        case = _locked(case_id)
        if case.status != Status.OPEN:
            raise CaseError("Only an open dispute can be reassigned.")
        previous = case.owner.username if case.owner else None
        case.owner = owner
        case.save(update_fields=["owner"])
        timeline.record(EventType.DISPUTE_ASSIGNED, actor=actor, invoice=case.invoice, dispute_id=str(case.pk),
                        owner=owner.username if owner else None, previous_owner=previous)
    return case


def add_note(case_id: Any, *, note: Any, actor: Any) -> DisputeNote:
    require(actor, Role.OPERATOR, "add a dispute note")
    body = text(note, "note", required=True, limit=2000)
    with transaction.atomic():
        case = _locked(case_id)
        entry = DisputeNote.objects.create(dispute=case, author=actor, text=body)
        timeline.record(EventType.DISPUTE_NOTE_ADDED, actor=actor, invoice=case.invoice, dispute_id=str(case.pk),
                        note_id=str(entry.pk))
    return entry


def resolve(case_id: Any, *, outcome: Any, resolution: Any, actor: Any) -> tuple[DisputeCase, dict[str, Any]]:
    """
    An approver closes the case. Returns the case and whether chasing can now resume (and if not, why).
    Never drafts or sends: if chasing resumes, the next sequence tick drafts the pending step for review.
    """
    require(actor, Role.APPROVER, "resolve a dispute")
    if outcome not in OUTCOMES:
        raise InvalidCase("outcome must be VALID (the customer is right) or INVALID (the invoice stands).")
    resolution = text(resolution, "resolution", required=True, limit=1000)
    with transaction.atomic():
        case = _locked(case_id)
        if case.status != Status.OPEN:
            raise CaseError(f"This dispute is already {case.get_status_display().lower()}.")
        holds.lock_invoices([case.invoice_id])
        case.status = OUTCOMES[outcome]
        case.resolved_at, case.resolved_by, case.resolution = timezone.now(), actor, resolution
        case.save(update_fields=["status", "resolved_at", "resolved_by", "resolution"])
        holds.release_owned(owner="dispute", owner_id=case.pk, actor=actor, note=f"Dispute {outcome.lower()}.")
        for task in Task.objects.select_for_update().filter(dispute=case, status=Task.Status.OPEN):
            task.status, task.done_at, task.done_by = Task.Status.DONE, timezone.now(), actor
            task.completion_note = f"Resolved {outcome.lower()}: {resolution}"[:500]
            task.save(update_fields=["status", "done_at", "done_by", "completion_note"])
            timeline.record(EventType.TASK_DONE, actor=actor, invoice=case.invoice, task_id=str(task.pk), kind=task.kind)
        exited = False
        if case.status == Status.RESOLVED_VALID:
            enrollment = SequenceEnrollment.objects.filter(
                invoice=case.invoice, status__in=SequenceEnrollment.LIVE_STATUSES).first()
            if enrollment is not None:
                exited = bool(engine._exit(enrollment.pk, engine.DISPUTE_UPHELD, actor))
        timeline.record(EventType.DISPUTE_RESOLVED, actor=actor, invoice=case.invoice, dispute_id=str(case.pk),
                        outcome=case.status, sequence_exited=exited)
        chasing = chasing_status(case.invoice)
    return case, chasing


def chasing_status(invoice: Invoice) -> dict[str, Any]:
    """Whether the invoice is chased again now, and what still stops it (other holds, paid, cooldown...)."""
    invoice.refresh_from_db()
    blockers = [{"code": code, "detail": message} for code, message in ineligibility(invoice) if code != "not_overdue"]
    enrollment = SequenceEnrollment.objects.filter(invoice=invoice, status__in=SequenceEnrollment.LIVE_STATUSES).first()
    if enrollment is None:
        blockers.append({"code": "not_in_sequence", "detail": "The invoice is not in a collection sequence."})
    elif enrollment.status == SequenceEnrollment.Status.PAUSED:
        blockers.append({"code": "sequence_paused", "detail": f"The sequence is paused ({enrollment.pause_reason})."})
    return {
        "resumes": not blockers,
        "blocked_by": blockers,
        "next_action_at": enrollment.next_action_at if enrollment and not blockers else None,
        "note": "Nothing is sent automatically: the next step is drafted for review." if not blockers else "",
    }

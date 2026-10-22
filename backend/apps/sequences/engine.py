"""
Sequence engine: enrollment, deterministic step progression and the sequence tick.

Progression rules (v2 §6):
  - A step starts (`pending_step`) when it is due and nothing else is pending. EMAIL steps get an
    AI draft; TASK steps get a call task. Creating a draft does not complete the step.
  - The pending EMAIL step completes when its reminder is SENT, or REJECTED by a person (the step
    was deliberately not sent). DRAFT, APPROVED and SENDING wait; DELIVERY_UNKNOWN and FAILED block
    until a person reconciles or re-drafts. A TASK step completes when its task is done.
  - The next step is due at due_date + offset, and never earlier than the last send + cooldown.
  - One tick advances an enrollment by at most one new step; nothing loops over steps.
  - The engine never approves and never sends; it only drafts through the generation service and
    creates tasks. Every draft path re-checks invoice eligibility.

Concurrency: each enrollment is evaluated and updated in a short transaction holding its row lock
(SELECT … FOR UPDATE SKIP LOCKED for ticks). Gemini is called after that transaction commits, with
`drafting_started_at` marking the step as being drafted. Partial unique indexes back this up: one
live enrollment per invoice, one active reminder per invoice, one task per enrollment step.
"""

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.followups import ai_budget
from apps.followups.eligibility import changed_snapshot_fields, ineligibility
from apps.followups.models import DeliveryAttempt, FollowUp, Tone
from apps.followups.services import (
    ActiveFollowUpExists,
    DraftPlan,
    FollowUpError,
    FollowUpGenerationService,
)
from apps.inbox.models import Task
from apps.invoices.models import AutomationRun, Invoice
from apps.timeline import services as timeline
from apps.timeline.models import ActorType, EventType

from .models import Channel, Sequence, SequenceEnrollment, SequenceStep

logger = logging.getLogger(__name__)

TICK_JOB_KIND = "sequences.tick"
# Failures worth retrying automatically (after a backoff), and failures a person must look at.
RETRYABLE_AI_ERRORS = {"ai_unavailable", "ai_quota_exhausted"}
NEEDS_PERSON_AI_ERRORS = {"ai_bad_response", "ai_not_configured", "unexpected_error"}
TONE_LADDER = [Tone.FRIENDLY, Tone.PROFESSIONAL, Tone.FIRM]
Status = SequenceEnrollment.Status


# Errors surfaced to people (manual drafting) ------------------------------------------------


class SequenceConflict(FollowUpError):
    code = "sequence_conflict"


class SequencePaused(SequenceConflict):
    code = "sequence_paused"


class SequenceReviewRequired(SequenceConflict):
    code = "sequence_review_required"


class SequenceCompleted(SequenceConflict):
    code = "sequence_completed"


class NextStepIsTask(SequenceConflict):
    code = "next_step_is_task"


class StepBlocked(SequenceConflict):
    code = "sequence_step_blocked"


class DraftInProgress(SequenceConflict):
    code = "generation_in_progress"


# Sequence helpers ---------------------------------------------------------------------------


DEFAULT_NAME = "Standard collection"
# v2 §6 default cadence (also seeded by migration sequences/0002): order, offset, channel, tone, approver.
DEFAULT_STEPS = [
    (1, 1, Channel.EMAIL, Tone.FRIENDLY, False),
    (2, 8, Channel.EMAIL, Tone.PROFESSIONAL, False),
    (3, 15, Channel.EMAIL, Tone.FIRM, True),
    (4, 30, Channel.TASK, "", False),
]


def default_sequence() -> Sequence:
    """The default sequence for new enrollments; re-creates the standard one if none exists at all."""
    sequence = Sequence.objects.filter(is_default=True, active=True).first()
    if sequence is not None:
        return sequence
    if Sequence.objects.filter(name=DEFAULT_NAME).exists():
        raise SequenceConflict("No default sequence is active. An admin must mark one as the default.")
    with transaction.atomic():
        sequence, created = Sequence.objects.get_or_create(
            name=DEFAULT_NAME, version=1, defaults={"is_default": True, "active": True}
        )
        if created:
            SequenceStep.objects.bulk_create(
                SequenceStep(sequence=sequence, order=o, offset_days=d, channel=c, tone=t, requires_approver=a)
                for o, d, c, t, a in DEFAULT_STEPS
            )
    return sequence


def steps_of(sequence: Sequence) -> dict[int, SequenceStep]:
    cache = getattr(sequence, "_steps_cache", None)
    if cache is None:
        cache = {step.order: step for step in sequence.steps.all()}
        sequence._steps_cache = cache  # type: ignore[attr-defined]
    return cache


def email_steps(sequence: Sequence) -> list[SequenceStep]:
    return [step for _, step in sorted(steps_of(sequence).items()) if step.channel == Channel.EMAIL]


def escalate(tone: str, level: int) -> str:
    index = TONE_LADDER.index(Tone(tone))
    return TONE_LADDER[min(index + level, len(TONE_LADDER) - 1)]


def step_tone(enrollment: SequenceEnrollment, step: SequenceStep) -> str:
    return escalate(step.tone, enrollment.escalation_level)


def last_sent_at(invoice: Invoice) -> dt.datetime | None:
    return FollowUp.objects.filter(invoice=invoice, status=FollowUp.Status.SENT).aggregate(m=Max("sent_at"))["m"]


def thread_parent(invoice: Invoice) -> DeliveryAttempt | None:
    """The latest sent message to this client about this invoice: later steps continue its thread."""
    return (
        DeliveryAttempt.objects.filter(
            follow_up__invoice=invoice, status=DeliveryAttempt.Status.SENT
        ).exclude(gmail_thread_id="").select_related("follow_up").order_by("-completed_at", "-started_at").first()
    )


def due_time(invoice: Invoice, step: SequenceStep, last_sent: dt.datetime | None,
             not_before: dt.datetime | None = None) -> tuple[dt.datetime, str]:
    """
    (when the step is due, the rule that decided it): due date + offset; never earlier than the
    last send + cooldown; and, for history from before sequences, never earlier than a recent
    rejection + cooldown (`not_before`), so a draft a person just rejected is not redrafted at once.
    """
    day = invoice.due_date + dt.timedelta(days=step.offset_days)
    when, rule = timezone.make_aware(dt.datetime.combine(day, dt.time.min)), "offset"
    if last_sent is not None:
        by_cooldown = last_sent + dt.timedelta(days=settings.FOLLOWUP_COOLDOWN_DAYS)
        if by_cooldown > when:
            when, rule = by_cooldown, "cooldown"
    if not_before is not None and not_before > when:
        when, rule = not_before, "recently_rejected"
    return when, rule


def not_before(enrollment: SequenceEnrollment) -> dt.datetime | None:
    value = enrollment.history.get("not_before")
    return dt.datetime.fromisoformat(value) if value else None


# History assessment for invoices with reminders from before sequences ------------------------


@dataclass
class Placement:
    last_completed_step: int
    pending_step: int | None
    status: str
    review_required: bool
    review_reason: str
    pause_reason: str
    blocked_reason: str
    history: dict[str, Any]


def assess_history(invoice: Invoice, sequence: Sequence) -> Placement:
    """
    Where an invoice belongs in `sequence`, from the reminders it was actually sent.

    Clean history (sent tones match the first steps' tones) continues after the steps already
    sent. Anything else is flagged for review, and is paused instead whenever the next step would
    be firm or need an approver: an ambiguous history never moves an invoice into a firm reminder
    automatically.
    """
    emails = email_steps(sequence)
    follow_ups = list(FollowUp.objects.filter(invoice=invoice).order_by("generated_at"))
    sent = sorted((f for f in follow_ups if f.status == FollowUp.Status.SENT), key=lambda f: f.sent_at or f.generated_at)
    sent_count = len(sent)
    history: dict[str, Any] = {
        "sent_reminders": sent_count,
        "sent_tones": [f.tone for f in sent],
        "last_sent_at": sent[-1].sent_at.isoformat() if sent and sent[-1].sent_at else None,
    }
    reasons: list[str] = []

    if sent_count > len(emails):
        reasons.append(f"{sent_count} reminders were already sent; the sequence has {len(emails)} email steps")
    if [f.tone for f in sent[: len(emails)]] != [s.tone for s in emails[:sent_count]]:
        expected = ", ".join(s.tone.lower() for s in emails[:sent_count])
        reasons.append(
            f"earlier reminders were {', '.join(t.lower() for t in history['sent_tones'])}; "
            f"the sequence's first steps are {expected}"
        )
    last_completed = emails[min(sent_count, len(emails)) - 1].order if sent_count else 0

    unresolved = [f for f in follow_ups if f.status in (FollowUp.Status.SENDING, FollowUp.Status.DELIVERY_UNKNOWN)]
    if unresolved:
        history["unresolved_follow_up_id"] = str(unresolved[-1].pk)
        return Placement(
            last_completed, None, Status.PAUSED, True,
            "A reminder's delivery is unresolved (SENDING or DELIVERY_UNKNOWN). Reconcile it, then review the position.",
            "history_review", "delivery_unknown", history,
        )

    steps = steps_of(sequence)
    pending: int | None = None
    blocked = ""
    latest = follow_ups[-1] if follow_ups else None
    if latest is not None and latest.status in (FollowUp.Status.DRAFT, FollowUp.Status.APPROVED, FollowUp.Status.FAILED):
        # A reminder from before sequences is in progress (or failed): it becomes the next step's.
        pending = last_completed + 1 if last_completed + 1 in steps else None
        if pending is not None:
            history["adopted_follow_up_id"] = str(latest.pk)
            if steps[pending].channel != Channel.EMAIL or steps[pending].tone != latest.tone:
                reasons.append(f"the reminder in progress is {latest.tone.lower()}, unlike step {pending}")
            if latest.status == FollowUp.Status.FAILED:
                blocked = "delivery_failed"

    if latest is not None and latest.status == FollowUp.Status.REJECTED:
        # A person rejected the last reminder before sequences existed: honour the cooldown from then.
        history["not_before"] = (latest.updated_at + dt.timedelta(days=settings.FOLLOWUP_COOLDOWN_DAYS)).isoformat()

    next_order = pending or last_completed + 1
    next_step = steps.get(next_order)
    ambiguous = bool(reasons)
    escalates = next_step is not None and (next_step.tone == Tone.FIRM or next_step.requires_approver)
    if ambiguous and escalates:
        return Placement(
            last_completed, pending, Status.PAUSED, True,
            "History does not match the sequence (" + "; ".join(reasons) + f"). Step {next_order} is firm or needs "
            "an approver, so it will not start until a person reviews the position.",
            "history_review", blocked, history,
        )
    return Placement(
        last_completed, pending, Status.ACTIVE, ambiguous,
        ("History does not match the sequence (" + "; ".join(reasons) + "). Continuing at step "
         f"{next_order}; please confirm.") if ambiguous else "",
        "", blocked, history,
    )


# Enrollment reconciliation ------------------------------------------------------------------


@dataclass
class ReconcileSummary:
    enrolled: int = 0
    exited: int = 0
    flagged: int = 0


def exit_reason_for(invoice: Invoice) -> str:
    if invoice.status == Invoice.Status.PAID:
        return "paid"
    if not invoice.in_sheet:
        return "not_in_sheet"
    return ""


def reconcile(*, actor: Any) -> ReconcileSummary:
    """Exit enrollments of paid or removed invoices; enroll eligible overdue invoices. Idempotent."""
    summary = ReconcileSummary()
    for enrollment in SequenceEnrollment.objects.filter(status__in=SequenceEnrollment.LIVE_STATUSES).select_related("invoice"):
        reason = exit_reason_for(enrollment.invoice)
        if reason:
            summary.exited += _exit(enrollment.pk, reason, actor)

    live = SequenceEnrollment.objects.filter(status__in=SequenceEnrollment.LIVE_STATUSES).values("invoice_id")
    for invoice in Invoice.objects.exclude(pk__in=live).select_related("client"):
        if ineligibility(invoice) or not _was_never_completed(invoice):
            continue
        enrollment = enroll(invoice, actor=actor)
        if enrollment is not None:
            summary.enrolled += 1
            summary.flagged += int(enrollment.review_required)
    return summary


def _was_never_completed(invoice: Invoice) -> bool:
    """An invoice that already finished a sequence is not enrolled again (unless it left and came back)."""
    last = SequenceEnrollment.objects.filter(invoice=invoice).order_by("-created_at").first()
    return last is None or last.status != Status.COMPLETED


def enroll(invoice: Invoice, *, actor: Any) -> SequenceEnrollment | None:
    """Enrolls an eligible invoice in the default sequence. None if it is already enrolled."""
    sequence = default_sequence()
    placement = assess_history(invoice, sequence)
    try:
        with transaction.atomic():
            enrollment = SequenceEnrollment.objects.create(
                invoice=invoice, sequence=sequence, status=placement.status,
                last_completed_step=placement.last_completed_step, pending_step=placement.pending_step,
                review_required=placement.review_required, review_reason=placement.review_reason[:300],
                pause_reason=placement.pause_reason, blocked_reason=placement.blocked_reason,
                history=placement.history,
            )
            if enrollment.pending_step is None and enrollment.status == Status.ACTIVE:
                _schedule_next(enrollment)
            timeline.record(
                EventType.ENROLLED, actor=actor, invoice=invoice, enrollment_id=str(enrollment.pk),
                sequence=str(sequence), last_completed_step=enrollment.last_completed_step,
                pending_step=enrollment.pending_step, status=enrollment.status,
                review_required=enrollment.review_required, review_reason=enrollment.review_reason,
            )
    except IntegrityError:
        return None  # enrolled concurrently: the partial unique index allows one live enrollment
    return enrollment


def _schedule_next(enrollment: SequenceEnrollment) -> None:
    steps = steps_of(enrollment.sequence)
    step = steps.get(enrollment.next_step)
    if step is None:
        enrollment.next_action_at = None
    else:
        enrollment.next_action_at, _ = due_time(
            enrollment.invoice, step, last_sent_at(enrollment.invoice), not_before(enrollment)
        )
    enrollment.save(update_fields=["next_action_at", "updated_at"])


def _exit(enrollment_id: Any, reason: str, actor: Any) -> int:
    with transaction.atomic():
        enrollment = (
            SequenceEnrollment.objects.select_for_update().select_related("invoice")
            .filter(pk=enrollment_id, status__in=SequenceEnrollment.LIVE_STATUSES).first()
        )
        if enrollment is None:
            return 0
        enrollment.status = Status.EXITED
        enrollment.exit_reason = reason
        enrollment.next_action_at = None
        enrollment.completed_at = timezone.now()
        enrollment.save(update_fields=["status", "exit_reason", "next_action_at", "completed_at", "updated_at"])
        timeline.record(EventType.ENROLLMENT_EXITED, actor=actor, invoice=enrollment.invoice,
                        enrollment_id=str(enrollment.pk), reason=reason)
    return 1


# Evaluation (pure) --------------------------------------------------------------------------


DRAFT, TASK, WAIT, SKIP = "DRAFT", "TASK", "WAIT", "SKIP"

AI_GATE_DETAILS = {
    "ai_quota_cooldown": "Gemini's quota was exhausted recently; automatic drafting is backing off.",
    "ai_budget_exhausted": "Today's automatic drafting budget (AI_DAILY_GENERATION_BUDGET) is used up.",
}


@dataclass
class Evaluation:
    """What a tick should do for one enrollment, and the state changes that go with it."""

    action: str  # DRAFT | TASK | WAIT | SKIP
    reason: str  # reason code for run items (empty for DRAFT/TASK)
    detail: str
    step: SequenceStep | None = None
    updates: dict[str, Any] = field(default_factory=dict)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    follow_up: FollowUp | None = None  # the pending step's reminder, when waiting on it


PENDING_WAIT = {
    FollowUp.Status.DRAFT: ("draft_awaiting_review", "", "The step's draft awaits review."),
    FollowUp.Status.APPROVED: ("approved_awaiting_send", "", "The step's reminder is approved and awaits a send."),
    FollowUp.Status.SENDING: ("delivery_unresolved", "delivery_in_progress", "The step's reminder is being sent."),
    FollowUp.Status.DELIVERY_UNKNOWN: (
        "delivery_unresolved", "delivery_unknown",
        "The step's delivery outcome is unknown. Reconcile it (reconcile_delivery); nothing is resent.",
    ),
    FollowUp.Status.FAILED: (
        "delivery_failed", "delivery_failed",
        "The step's reminder was not delivered. Re-draft the step (Analyze) to try again.",
    ),
}


def step_follow_up(enrollment: SequenceEnrollment, order: int) -> FollowUp | None:
    # Filter by id: the preview evaluates unsaved enrollments.
    linked = (
        FollowUp.objects.filter(enrollment_id=enrollment.pk, step__order=order).order_by("-generated_at").first()
    )
    adopted_id = enrollment.history.get("adopted_follow_up_id")
    if adopted_id and order == enrollment.pending_step:
        adopted = FollowUp.objects.filter(pk=adopted_id).first()
        if adopted is not None and (linked is None or adopted.generated_at > linked.generated_at):
            return adopted
    return linked


def evaluate(enrollment: SequenceEnrollment, *, now: dt.datetime) -> Evaluation:
    invoice = enrollment.invoice
    steps = steps_of(enrollment.sequence)

    reason = exit_reason_for(invoice)
    if reason:
        return Evaluation(SKIP, "sequence_exited", f"Left the sequence: {reason}.",
                          updates={"status": Status.EXITED, "exit_reason": reason, "next_action_at": None,
                                   "completed_at": now},
                          events=[(EventType.ENROLLMENT_EXITED, {"reason": reason})])
    if enrollment.status == Status.PAUSED:
        code = "history_review" if enrollment.pause_reason == "history_review" else "sequence_paused"
        if enrollment.blocked_reason in ("delivery_unknown", "delivery_in_progress"):
            code = "delivery_unresolved"
        return Evaluation(SKIP, code, enrollment.review_reason or f"Paused: {enrollment.pause_reason}.")
    if enrollment.status != Status.ACTIVE:
        return Evaluation(SKIP, f"sequence_{enrollment.status.lower()}", f"Enrollment is {enrollment.status}.")
    problems = [(code, message) for code, message in ineligibility(invoice) if code != "not_overdue"]
    if problems:
        return Evaluation(SKIP, problems[0][0], problems[0][1])

    updates: dict[str, Any] = {}
    events: list[tuple[str, dict[str, Any]]] = []
    last_completed = enrollment.last_completed_step

    if enrollment.pending_step is not None:
        order = enrollment.pending_step
        step = steps[order]
        if step.channel == Channel.TASK:
            task = Task.objects.filter(enrollment_id=enrollment.pk, step=step).first()
            if task is None:
                return Evaluation(TASK, "", f"Creating the step {order} call task.", step=step)
            if task.done_at is None:
                return Evaluation(WAIT, "task_open", f"Step {order}: the call task is open.", step=step)
            outcome = "task_done"
        else:
            follow_up = step_follow_up(enrollment, order)
            if follow_up is None:
                drafting = enrollment.drafting_started_at
                timeout = dt.timedelta(seconds=settings.SEQUENCE_DRAFTING_TIMEOUT_SECONDS)
                if drafting is not None and now - drafting < timeout:
                    return Evaluation(WAIT, "drafting", f"Step {order}'s draft is being generated.", step=step)
                if enrollment.blocked_reason == "generation_failed":
                    return Evaluation(WAIT, "generation_failed",
                                      f"Step {order}'s AI draft failed and won't be retried automatically. "
                                      "Draft it manually to try again.", step=step)
                if enrollment.retry_after is not None and now < enrollment.retry_after:
                    when = timezone.localtime(enrollment.retry_after).strftime("%Y-%m-%d %H:%M")
                    return Evaluation(WAIT, "ai_retry_scheduled",
                                      f"Step {order}'s AI draft failed {enrollment.generation_failures} time(s); "
                                      f"next automatic try after {when}.", step=step)
                return Evaluation(DRAFT, "", f"Retrying the step {order} draft.", step=step)
            if follow_up.status in (FollowUp.Status.DRAFT, FollowUp.Status.APPROVED):
                changed = changed_snapshot_fields(follow_up.invoice_snapshot, invoice)
                if changed:
                    code = "stale_draft" if follow_up.status == FollowUp.Status.DRAFT else "stale_approved"
                    return Evaluation(WAIT, code, f"Step {order}: the invoice changed since drafting "
                                      f"({', '.join(changed)}); review or regenerate it.", step=step,
                                      follow_up=follow_up)
            if follow_up.status in PENDING_WAIT:
                code, blocked, message = PENDING_WAIT[follow_up.status]
                if blocked != enrollment.blocked_reason:
                    updates["blocked_reason"] = blocked
                return Evaluation(WAIT, code, f"Step {order}: {message}", step=step, updates=updates,
                                  follow_up=follow_up)
            outcome = "sent" if follow_up.status == FollowUp.Status.SENT else "rejected"
        # The pending step is finished.
        last_completed = order
        updates.update({"last_completed_step": order, "pending_step": None, "blocked_reason": "",
                        "drafting_started_at": None})
        events.append((EventType.STEP_COMPLETED, {"step": order, "outcome": outcome}))

    order = last_completed + 1
    step = steps.get(order)
    if step is None:
        updates.update({"status": Status.COMPLETED, "next_action_at": None, "completed_at": now})
        events.append((EventType.ENROLLMENT_COMPLETED, {"last_step": last_completed}))
        return Evaluation(SKIP, "sequence_completed", "All steps are done.", updates=updates, events=events)

    when, rule = due_time(invoice, step, last_sent_at(invoice), not_before(enrollment))
    if when > now:
        updates["next_action_at"] = when
        code = rule if rule in ("cooldown", "recently_rejected") else "not_due"
        local = timezone.localtime(when).date().isoformat()
        return Evaluation(SKIP, code, f"Step {order} is due {local}.", step=step, updates=updates, events=events)
    return Evaluation(TASK if step.channel == Channel.TASK else DRAFT, "", f"Step {order} is due.", step=step,
                      updates=updates, events=events)


# Applying one tick to one enrollment --------------------------------------------------------


@dataclass
class StepResult:
    invoice: Invoice
    outcome: str  # GENERATED | SKIPPED | FAILED (AutomationRunItem outcomes)
    reason: str
    detail: str
    follow_up: FollowUp | None = None


def advance(enrollment_id: Any, *, actor: Any, now: dt.datetime, allow_draft: bool = True,
            gemini: Any = None, ai_gate: Callable[[], str] | None = None) -> StepResult | None:
    """
    One tick for one enrollment. Evaluates and applies under the row lock, then (if a draft is
    due) calls Gemini with no transaction open. Returns None if another worker holds the row.
    """
    with transaction.atomic():
        enrollment = (
            SequenceEnrollment.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("invoice__client", "sequence").filter(pk=enrollment_id).first()
        )
        if enrollment is None:
            return None
        evaluation = evaluate(enrollment, now=now)
        if evaluation.action == DRAFT and not allow_draft:
            evaluation = Evaluation(SKIP, "run_limit_reached", "Draft limit for this run reached.",
                                    step=evaluation.step, updates=evaluation.updates, events=evaluation.events)
        # Spending guard: no Gemini call while quota is cooling down or the daily budget is used up.
        # The step is not started, so it stays due and is picked up once Gemini may be called again.
        gate = ai_gate() if evaluation.action == DRAFT and ai_gate is not None else ""
        if gate:
            evaluation = Evaluation(SKIP, gate, AI_GATE_DETAILS[gate], step=evaluation.step,
                                    updates=evaluation.updates, events=evaluation.events)
        _apply(enrollment, evaluation, actor)
        invoice = enrollment.invoice
        if evaluation.action == TASK:
            _create_task(enrollment, evaluation.step, actor)  # type: ignore[arg-type]
            return StepResult(invoice, "SKIPPED", "task_created", f"Step {evaluation.step.order}: call task created.")  # type: ignore[union-attr]
        if evaluation.action != DRAFT:
            return StepResult(invoice, "SKIPPED", evaluation.reason, evaluation.detail, evaluation.follow_up)
        plan = _start_draft(enrollment, evaluation.step, now=now)  # type: ignore[arg-type]

    return _generate(plan, actor=actor, gemini=gemini)


def _apply(enrollment: SequenceEnrollment, evaluation: Evaluation, actor: Any) -> None:
    if evaluation.updates:
        for name, value in evaluation.updates.items():
            setattr(enrollment, name, value)
        enrollment.save(update_fields=[*evaluation.updates, "updated_at"])
    for event_type, payload in evaluation.events:
        timeline.record(event_type, actor=actor, invoice=enrollment.invoice, enrollment_id=str(enrollment.pk), **payload)


def _create_task(enrollment: SequenceEnrollment, step: SequenceStep, actor: Any) -> Task:
    invoice = enrollment.invoice
    task, created = Task.objects.get_or_create(
        enrollment=enrollment, step=step,
        defaults={"kind": Task.Kind.CALL, "invoice": invoice, "client": invoice.client,
                  "title": f"Call {invoice.client_name} about invoice {invoice.external_id}"},
    )
    enrollment.pending_step = step.order
    enrollment.next_action_at = None
    enrollment.save(update_fields=["pending_step", "next_action_at", "updated_at"])
    if created:
        timeline.record(EventType.TASK_CREATED, actor=actor, invoice=invoice, task_id=str(task.pk),
                        kind=task.kind, step=step.order, enrollment_id=str(enrollment.pk))
    return task


def _start_draft(enrollment: SequenceEnrollment, step: SequenceStep, *, now: dt.datetime, manual: bool = False) -> DraftPlan:
    """Marks the step as pending and being drafted (in the caller's locked transaction) and builds the plan."""
    enrollment.pending_step = step.order
    enrollment.next_action_at = None
    enrollment.drafting_started_at = now
    enrollment.save(update_fields=["pending_step", "next_action_at", "drafting_started_at", "updated_at"])
    invoice = enrollment.invoice
    parent = thread_parent(invoice)
    previous = last_sent_at(invoice)
    enrollment_id = enrollment.pk

    def on_created(follow_up: FollowUp) -> None:
        SequenceEnrollment.objects.filter(pk=enrollment_id).update(
            drafting_started_at=None, blocked_reason="", generation_failures=0, retry_after=None
        )

    def on_failed(code: str) -> None:
        # The step stays pending with no draft. Transient AI errors are retried automatically after
        # an exponential backoff; other AI errors wait for a person; anything else just clears the marker.
        queryset = SequenceEnrollment.objects.filter(pk=enrollment_id)
        if code in RETRYABLE_AI_ERRORS:
            current = queryset.values_list("generation_failures", flat=True).first() or 0
            failures = current + 1
            queryset.update(
                drafting_started_at=None, generation_failures=failures, blocked_reason="ai_retry_scheduled",
                retry_after=timezone.now() + ai_budget.backoff(failures, base_minutes=settings.AI_RETRY_BASE_MINUTES),
            )
        elif code in NEEDS_PERSON_AI_ERRORS:
            queryset.update(drafting_started_at=None, blocked_reason="generation_failed", retry_after=None)
        else:
            queryset.update(drafting_started_at=None)

    return DraftPlan(
        invoice_id=invoice.pk, enrollment_id=enrollment_id, step=step, tone=step_tone(enrollment, step),
        thread_reply_to=parent, step_number=step.order, total_email_steps=len(email_steps(enrollment.sequence)),
        previous_send_date=timezone.localtime(previous).date() if previous else None,
        manual=manual, on_created=on_created, on_failed=on_failed,
    )


def _generate(plan: DraftPlan, *, actor: Any, gemini: Any = None) -> StepResult:
    invoice = Invoice.objects.get(pk=plan.invoice_id)
    try:
        follow_up = FollowUpGenerationService(gemini=gemini).generate(invoice.pk, actor=actor, plan=plan)
    except FollowUpError as exc:
        outcome = "SKIPPED" if isinstance(exc, ActiveFollowUpExists) or exc.code == "not_eligible" else "FAILED"
        return StepResult(invoice, outcome, exc.code, exc.message)
    except Exception:
        logger.exception("Unexpected error drafting sequence step for invoice %s", invoice.pk)
        plan.on_failed("unexpected_error")
        return StepResult(invoice, "FAILED", "unexpected_error", "Unexpected error while generating the draft.")
    return StepResult(invoice, "GENERATED", "", f"Step {plan.step_number} drafted; awaiting approval.", follow_up)


# The tick ------------------------------------------------------------------------------------


def due_enrollments(now: dt.datetime) -> list[Any]:
    """ACTIVE enrollments with a pending step to resolve or a next step that is due."""
    active = SequenceEnrollment.objects.filter(status=Status.ACTIVE)
    ids = list(active.filter(pending_step__isnull=False).values_list("pk", flat=True))
    ids += list(
        active.filter(pending_step__isnull=True, next_action_at__lte=now)
        .order_by("next_action_at").values_list("pk", flat=True)
    )
    # Exits (paid, removed) and completions are found by reconcile and by these evaluations.
    return ids


def run_tick(*, actor: Any, record: Callable[[StepResult], None] | None = None,
             heartbeat: Callable[[], None] | None = None, gemini: Any = None,
             now: dt.datetime | None = None, reconcile_first: bool = True) -> list[StepResult]:
    """Reconcile enrollments, then advance each due one by at most one step. Never sends."""
    now = now or timezone.now()
    if reconcile_first:
        reconcile(actor=actor)
    results: list[StepResult] = []
    drafts = 0
    for enrollment_id in due_enrollments(now):
        result = advance(enrollment_id, actor=actor, now=now,
                         allow_draft=drafts < settings.AUTOMATION_MAX_DRAFTS_PER_RUN, gemini=gemini,
                         ai_gate=lambda: ai_budget.status(now).blocked_reason(now))
        if result is None:
            continue
        drafts += int(result.outcome in ("GENERATED", "FAILED") and result.reason != "run_limit_reached")
        results.append(result)
        if record:
            record(result)
        if heartbeat:
            heartbeat()
    return results


def preview(now: dt.datetime | None = None) -> list[tuple[Invoice, str, str]]:
    """Dry run: (invoice, outcome, reason) per invoice, without writes or provider calls."""
    now = now or timezone.now()
    rows: list[tuple[Invoice, str, str]] = []
    live = {e.invoice_id: e for e in SequenceEnrollment.objects.filter(status__in=SequenceEnrollment.LIVE_STATUSES)
            .select_related("sequence", "invoice")}
    sequence = Sequence.objects.filter(is_default=True, active=True).first()
    would_draft = 0
    for invoice in Invoice.objects.order_by("due_date", "external_id"):
        enrollment = live.get(invoice.pk)
        if enrollment is None:
            reasons = ineligibility(invoice)
            if reasons:
                rows.append((invoice, "SKIPPED", reasons[0][0]))
                continue
            if sequence is None:
                rows.append((invoice, "SKIPPED", "no_default_sequence"))
                continue
            placement = assess_history(invoice, sequence)
            enrollment = SequenceEnrollment(
                invoice=invoice, sequence=sequence, status=placement.status,
                last_completed_step=placement.last_completed_step, pending_step=placement.pending_step,
                pause_reason=placement.pause_reason, review_reason=placement.review_reason, history=placement.history,
            )
        evaluation = evaluate(enrollment, now=now)
        if evaluation.action == DRAFT and would_draft >= settings.AUTOMATION_MAX_DRAFTS_PER_RUN:
            rows.append((invoice, "SKIPPED", "run_limit_reached"))
        elif evaluation.action == DRAFT:
            would_draft += 1
            rows.append((invoice, "WOULD_GENERATE", f"step_{evaluation.step.order}"))  # type: ignore[union-attr]
        elif evaluation.action == TASK:
            rows.append((invoice, "WOULD_CREATE_TASK", f"step_{evaluation.step.order}"))  # type: ignore[union-attr]
        else:
            rows.append((invoice, "SKIPPED", evaluation.reason))
    return rows


def handle_tick_job(job: Any) -> dict[str, Any]:
    """Periodic or after-sync tick, under the automation lease (never overlaps a run or another tick)."""
    from apps.automation.lease import Lease, LeaseUnavailable

    try:
        lease = Lease.acquire()
    except LeaseUnavailable:
        return {"skipped": "automation_running"}
    trigger = job.payload.get("trigger", AutomationRun.Trigger.SCHEDULED)
    actor = ActorType.SCHEDULER if trigger == AutomationRun.Trigger.SCHEDULED else ActorType.SYSTEM
    try:
        run = AutomationRun.objects.create(kind=AutomationRun.Kind.SEQUENCE_TICK, trigger=trigger,
                                           status=AutomationRun.Status.RUNNING)
        lease.attach_run(run)
        counts = {"GENERATED": 0, "SKIPPED": 0, "FAILED": 0}

        def record(result: StepResult) -> None:
            counts[result.outcome] += 1

        def beat() -> None:
            if not lease.heartbeat():
                raise RuntimeError("lease lost")

        results = run_tick(actor=actor, record=record, heartbeat=beat)
        run.invoices_evaluated = len(results)
        run.drafts_generated = counts["GENERATED"]
        run.invoices_skipped = counts["SKIPPED"]
        run.generation_failures = counts["FAILED"]
        run.status = AutomationRun.Status.PARTIAL if counts["FAILED"] else AutomationRun.Status.COMPLETED
        run.completed_at = timezone.now()
        run.save()
        return {"run_id": run.pk, **{k.lower(): v for k, v in counts.items()}}
    finally:
        lease.release()


# Manual drafting (Analyze / regenerate) ------------------------------------------------------


class ManualDraftPlanner:
    """
    Registered with the follow-up service so a person's "Analyze" drafts the invoice's sequence step
    (enrolling it first when needed) instead of choosing a tone from days overdue. A person may draft a
    step before its due date, but never skip a pending step, an unresolved delivery or a review.
    """

    def check(self, invoice: Invoice, *, regenerate: bool) -> None:
        enrollment = SequenceEnrollment.objects.filter(invoice=invoice, status__in=SequenceEnrollment.LIVE_STATUSES).first()
        if enrollment is not None:
            self._refuse_if_blocked(enrollment, regenerate=regenerate, now=timezone.now())

    def plan(self, invoice: Invoice, *, regenerate: bool, actor: Any) -> DraftPlan:
        if not SequenceEnrollment.objects.filter(invoice=invoice, status__in=SequenceEnrollment.LIVE_STATUSES).exists():
            if not _was_never_completed(invoice):
                raise SequenceCompleted("This invoice already completed its sequence.")
            enroll(invoice, actor=actor)
        now = timezone.now()
        with transaction.atomic():
            enrollment = (
                SequenceEnrollment.objects.select_for_update(of=("self",)).select_related("invoice__client", "sequence")
                .filter(invoice=invoice, status__in=SequenceEnrollment.LIVE_STATUSES).first()
            )
            if enrollment is None:
                raise SequenceConflict("The invoice could not be enrolled in a sequence.")
            self._refuse_if_blocked(enrollment, regenerate=regenerate, now=now)
            if enrollment.pending_step is not None and not regenerate:
                # The pending step may have finished since the last tick (sent or rejected): record that first.
                evaluation = evaluate(enrollment, now=now)
                if evaluation.updates.get("pending_step", -1) is None:
                    _apply(enrollment, evaluation, actor)
            if enrollment.status == Status.COMPLETED:
                raise SequenceCompleted("All steps of this invoice's sequence are done.")
            # A pending step without a live draft (failed delivery, failed generation) is re-drafted;
            # otherwise the next step is drafted now, even if it is not due yet (a person asked).
            order = enrollment.pending_step or enrollment.next_step
            step = steps_of(enrollment.sequence).get(order)
            if step is None:
                raise SequenceCompleted("All steps of this invoice's sequence are done.")
            if step.channel != Channel.EMAIL:
                raise NextStepIsTask(f"Step {order} is a call task, not an email.")
            return _start_draft(enrollment, step, now=now, manual=True)

    @staticmethod
    def _refuse_if_blocked(enrollment: SequenceEnrollment, *, regenerate: bool, now: dt.datetime) -> int:
        if enrollment.status == Status.PAUSED:
            if enrollment.pause_reason == "history_review":
                raise SequenceReviewRequired(enrollment.review_reason or "The sequence position needs review first.")
            raise SequencePaused(f"The sequence is paused ({enrollment.pause_reason}). Resume it first.")
        order = enrollment.pending_step or enrollment.next_step
        if enrollment.pending_step is not None:
            drafting = enrollment.drafting_started_at
            if drafting is not None and now - drafting < dt.timedelta(seconds=settings.SEQUENCE_DRAFTING_TIMEOUT_SECONDS):
                raise DraftInProgress(f"Step {order}'s draft is already being generated.")
            follow_up = step_follow_up(enrollment, order)
            if follow_up is not None and follow_up.status == FollowUp.Status.DELIVERY_UNKNOWN:
                raise StepBlocked(f"Step {order}'s delivery outcome is unknown. Reconcile it first; nothing is resent.")
            if follow_up is not None and follow_up.status == FollowUp.Status.SENDING:
                raise StepBlocked(f"Step {order}'s reminder is being sent.")
        return order


plan_manual_draft = ManualDraftPlanner()

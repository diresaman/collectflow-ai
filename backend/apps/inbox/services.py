"""
Task lifecycle (M09 call tasks, M10 reply tasks, M11 assignment, notes, cancellation, manual tasks).

OPEN -> DONE (with an optional completion note) or OPEN -> CANCELLED (with a note). Both are idempotent
and run under the task's row lock, so concurrent clicks complete a task once. Completing a sequence call
task records the step's outcome as soon as the transaction commits (exactly once: the engine evaluates
the enrollment under its own lock). No other task touches a sequence: completing a reply or dispute task
never resumes chasing, because holds are only released by their owners.
"""

import datetime as dt
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.roles import has_role
from apps.followups.services import FollowUpError
from apps.invoices.models import Invoice
from apps.timeline import services as timeline
from apps.timeline.models import ActorType, EventType

from .models import Task


class TaskError(FollowUpError):
    code = "task_conflict"


class InvalidTask(TaskError):
    status_code = 400
    code = "invalid_input"


class TaskForbidden(TaskError):
    status_code = 403
    code = "forbidden"


def _require_operator(actor: Any) -> None:
    if not has_role(actor, Role.OPERATOR):
        raise TaskForbidden("Your role does not allow this action.")


def _note(value: Any, *, required: bool = False) -> str:
    if value in (None, ""):
        if required:
            raise InvalidTask("A note is required.")
        return ""
    if not isinstance(value, str) or len(value.strip()) > 500:
        raise InvalidTask("The note must be text of at most 500 characters.")
    if required and not value.strip():
        raise InvalidTask("A note is required.")
    return value.strip()


def _locked(task_id: Any) -> Task:
    return Task.objects.select_for_update(of=("self",)).select_related("invoice", "client").get(pk=task_id)


def _assignee(user_id: Any) -> Any:
    if user_id in (None, ""):
        return None
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=user_id, is_active=True).first() if str(user_id).isdigit() else None
    if user is None or not has_role(user, Role.OPERATOR):
        raise InvalidTask("assignee must be an active CollectFlow user.")
    return user


def mark_done(task_id: Any, *, actor: Any, note: Any = "") -> Task:
    """Idempotent. A sequence call step is recorded as completed right after the commit."""
    if actor not in (ActorType.SYSTEM, ActorType.SCHEDULER):
        _require_operator(actor)
    note = _note(note)
    with transaction.atomic():
        task = _locked(task_id)
        if task.status == Task.Status.CANCELLED:
            raise TaskError("This task was cancelled.")
        if task.status == Task.Status.OPEN:
            task.status, task.done_at = Task.Status.DONE, timezone.now()
            task.done_by = actor if getattr(actor, "is_authenticated", False) else None
            task.completion_note = note
            task.save(update_fields=["status", "done_at", "done_by", "completion_note"])
            timeline.record(EventType.TASK_DONE, actor=actor, invoice=task.invoice, client=task.client,
                            task_id=str(task.pk), kind=task.kind, note=bool(note))
            if task.enrollment_id and task.step_id:
                enrollment_id = task.enrollment_id
                transaction.on_commit(lambda: _record_step(enrollment_id))
    return task


def _record_step(enrollment_id: Any) -> None:
    from apps.sequences import engine

    engine.refresh(enrollment_id, actor=ActorType.SYSTEM)


def cancel(task_id: Any, *, actor: Any, note: Any) -> Task:
    """Withdraws a task that is no longer needed. A sequence call task cannot be cancelled (its step needs it)."""
    _require_operator(actor)
    note = _note(note, required=True)
    with transaction.atomic():
        task = _locked(task_id)
        if task.step_id:
            raise TaskError("A sequence call task cannot be cancelled; mark it done once the call is made.")
        if task.status == Task.Status.DONE:
            raise TaskError("This task is already done.")
        if task.status == Task.Status.OPEN:
            task.status, task.cancelled_at, task.cancelled_by = Task.Status.CANCELLED, timezone.now(), actor
            task.completion_note = note
            task.save(update_fields=["status", "cancelled_at", "cancelled_by", "completion_note"])
            timeline.record(EventType.TASK_CANCELLED, actor=actor, invoice=task.invoice, client=task.client,
                            task_id=str(task.pk), kind=task.kind)
    return task


def assign(task_id: Any, *, assignee_id: Any, actor: Any) -> Task:
    _require_operator(actor)
    assignee = _assignee(assignee_id)
    with transaction.atomic():
        task = _locked(task_id)
        if task.status != Task.Status.OPEN:
            raise TaskError("Only an open task can be assigned.")
        if task.assignee_id != (assignee.pk if assignee else None):
            previous = task.assignee.username if task.assignee else None
            task.assignee = assignee
            task.save(update_fields=["assignee"])
            timeline.record(EventType.TASK_ASSIGNED, actor=actor, invoice=task.invoice, client=task.client,
                            task_id=str(task.pk), assignee=assignee.username if assignee else None,
                            previous_assignee=previous)
    return task


def create(invoice_id: Any, *, title: Any, due_at: Any = None, assignee_id: Any = None, detail: Any = "",
           actor: Any) -> Task:
    """A manual follow-up task for an invoice."""
    _require_operator(actor)
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
        raise InvalidTask("title is required (at most 200 characters).")
    when = timezone.now() + dt.timedelta(days=settings.TASK_DUE_DAYS)
    if due_at not in (None, ""):
        try:
            when = dt.datetime.fromisoformat(str(due_at))
        except ValueError:
            raise InvalidTask("due_at must be an ISO date or date-time.") from None
        if timezone.is_naive(when):
            when = timezone.make_aware(when)
    assignee = _assignee(assignee_id)
    detail = _note(detail)
    with transaction.atomic():
        invoice = Invoice.objects.select_related("client").get(pk=invoice_id)  # DoesNotExist -> 404
        task = Task.objects.create(kind=Task.Kind.FOLLOW_UP, title=title.strip(), invoice=invoice, client=invoice.client,
                                   assignee=assignee, due_at=when, detail=detail, created_by=actor)
        timeline.record(EventType.TASK_CREATED, actor=actor, invoice=invoice, task_id=str(task.pk), kind=task.kind,
                        assignee=assignee.username if assignee else None)
    return task

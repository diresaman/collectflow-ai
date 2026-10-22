from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.timeline import services as timeline
from apps.timeline.models import EventType

from .models import Task


def mark_done(task_id: Any, *, actor: Any) -> Task:
    """Idempotent. A sequence's call step completes on the next tick once its task is done."""
    with transaction.atomic():
        task = Task.objects.select_for_update(of=("self",)).select_related("invoice", "client").get(pk=task_id)
        if task.done_at is None:
            task.done_at = timezone.now()
            task.done_by = actor if getattr(actor, "is_authenticated", False) else None
            task.save(update_fields=["done_at", "done_by"])
            timeline.record(EventType.TASK_DONE, actor=actor, invoice=task.invoice, client=task.client,
                            task_id=str(task.pk), kind=task.kind)
    return task

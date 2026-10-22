import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Task(models.Model):
    """Work for a person. Phase 02 creates CALL tasks from sequence steps; replies add the other kinds later."""

    class Kind(models.TextChoices):
        CALL = "CALL", "Call the client"
        UPDATE_CONTACT = "UPDATE_CONTACT", "Update contact details"
        SEND_INFO = "SEND_INFO", "Send requested information"
        RECONCILE = "RECONCILE", "Reconcile a payment claim"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=200)
    invoice = models.ForeignKey("invoices.Invoice", on_delete=models.PROTECT, null=True, blank=True, related_name="tasks")
    client = models.ForeignKey("invoices.Client", on_delete=models.PROTECT, related_name="tasks")
    enrollment = models.ForeignKey(
        "sequences.SequenceEnrollment", on_delete=models.PROTECT, null=True, blank=True, related_name="tasks"
    )
    step = models.ForeignKey("sequences.SequenceStep", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    due_at = models.DateTimeField(default=timezone.now)
    done_at = models.DateTimeField(null=True, blank=True)
    done_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["done_at", "due_at"]
        constraints = [
            # A sequence step creates at most one task, however many ticks or workers see it.
            models.UniqueConstraint(
                fields=["enrollment", "step"], condition=Q(step__isnull=False), name="one_task_per_enrollment_step"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}: {self.title}"

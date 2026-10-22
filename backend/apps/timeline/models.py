import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class ActorType(models.TextChoices):
    USER = "USER", "User"
    SYSTEM = "SYSTEM", "System"
    SCHEDULER = "SCHEDULER", "Scheduler"


class EventType(models.TextChoices):
    # Google Sheets sync
    INVOICE_CREATED = "INVOICE_CREATED", "Invoice imported"
    INVOICE_UPDATED = "INVOICE_UPDATED", "Invoice changed in the sheet"
    INVOICE_REMOVED_FROM_SHEET = "INVOICE_REMOVED_FROM_SHEET", "Invoice removed from the sheet"
    INVOICE_RETURNED_TO_SHEET = "INVOICE_RETURNED_TO_SHEET", "Invoice back in the sheet"
    # Drafting and review
    DRAFT_GENERATED = "DRAFT_GENERATED", "Draft generated"
    DRAFT_GENERATION_FAILED = "DRAFT_GENERATION_FAILED", "Draft generation failed"
    DRAFT_EDITED = "DRAFT_EDITED", "Draft edited"
    FOLLOW_UP_APPROVED = "FOLLOW_UP_APPROVED", "Approved"
    FOLLOW_UP_REJECTED = "FOLLOW_UP_REJECTED", "Rejected"
    APPROVAL_WITHDRAWN = "APPROVAL_WITHDRAWN", "Approval withdrawn"
    # Delivery
    SEND_REQUESTED = "SEND_REQUESTED", "Send requested"
    SEND_CANCELLED = "SEND_CANCELLED", "Send not attempted"
    FOLLOW_UP_SENT = "FOLLOW_UP_SENT", "Sent"
    DELIVERY_FAILED = "DELIVERY_FAILED", "Delivery failed"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN", "Delivery outcome unknown"
    DELIVERY_RECONCILED = "DELIVERY_RECONCILED", "Delivery reconciled"
    # Sequences
    ENROLLED = "ENROLLED", "Enrolled in a sequence"
    ENROLLMENT_PAUSED = "ENROLLMENT_PAUSED", "Sequence paused"
    ENROLLMENT_RESUMED = "ENROLLMENT_RESUMED", "Sequence resumed"
    ENROLLMENT_REVIEWED = "ENROLLMENT_REVIEWED", "Sequence history reviewed"
    ENROLLMENT_EXITED = "ENROLLMENT_EXITED", "Left the sequence"
    ENROLLMENT_COMPLETED = "ENROLLMENT_COMPLETED", "Sequence completed"
    STEP_COMPLETED = "STEP_COMPLETED", "Sequence step completed"
    TASK_CREATED = "TASK_CREATED", "Task created"
    TASK_DONE = "TASK_DONE", "Task done"


class TimelineEvent(models.Model):
    """
    Append-only history of an invoice and its client. Database triggers (see migration 0001)
    reject UPDATE and DELETE, so events cannot be changed after the fact, even by the ORM.
    Payloads hold identifiers, codes and changed field values, never credentials or email bodies.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(
        "invoices.Invoice", on_delete=models.PROTECT, null=True, blank=True, related_name="timeline_events"
    )
    client = models.ForeignKey(
        "invoices.Client", on_delete=models.PROTECT, null=True, blank=True, related_name="timeline_events"
    )
    actor_type = models.CharField(max_length=10, choices=ActorType.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    type = models.CharField(max_length=40, choices=EventType.choices)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["invoice", "occurred_at"]),
            models.Index(fields=["client", "occurred_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(actor_type="USER", actor__isnull=False) | ~models.Q(actor_type="USER"),
                name="timeline_user_events_have_actor",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.type} at {self.occurred_at:%Y-%m-%d %H:%M}"

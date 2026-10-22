import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Channel(models.TextChoices):
    EMAIL = "EMAIL", "Email"
    TASK = "TASK", "Call task"


class Sequence(models.Model):
    """
    A versioned collection cadence. A version is immutable once an enrollment uses it: changes
    are saved as a new version, and existing enrollments keep the version they started with.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    version = models.PositiveIntegerField()
    # The default active version is used for new enrollments.
    is_default = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["name", "version"], name="sequence_name_version_unique"),
            models.UniqueConstraint(
                fields=["is_default"], condition=Q(is_default=True, active=True), name="one_default_sequence"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"


class SequenceStep(models.Model):
    sequence = models.ForeignKey(Sequence, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveSmallIntegerField()
    # Days after the invoice due date.
    offset_days = models.PositiveIntegerField()
    channel = models.CharField(max_length=10, choices=Channel.choices)
    # Required for EMAIL steps; the backend, not the AI, picks the tone.
    tone = models.CharField(max_length=20, blank=True)
    requires_approver = models.BooleanField(default=False)

    class Meta:
        ordering = ["sequence", "order"]
        constraints = [
            models.UniqueConstraint(fields=["sequence", "order"], name="step_order_unique_per_sequence"),
            models.CheckConstraint(condition=Q(order__gte=1, order__lte=5), name="step_order_1_to_5"),
            models.CheckConstraint(
                condition=Q(channel="TASK", tone="") | Q(channel="EMAIL", tone__in=["FRIENDLY", "PROFESSIONAL", "FIRM"]),
                name="step_tone_matches_channel",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sequence} step {self.order} ({self.channel} +{self.offset_days}d)"


class SequenceEnrollment(models.Model):
    """
    An invoice's position in a sequence.

    - `last_completed_step`: the highest step that is finished: its email was SENT, a person
      rejected its draft (the step was deliberately not sent), or its task is done. 0 = none.
    - `pending_step`: the step currently in progress (a draft, an approval, a delivery or an open
      task). Creating a draft does not complete a step; only its outcome does.
    - The next eligible step is always `last_completed_step + 1`, and only when nothing is pending.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        PAUSED = "PAUSED", "Paused"
        COMPLETED = "COMPLETED", "Completed"
        EXITED = "EXITED", "Exited"

    LIVE_STATUSES = (Status.ACTIVE, Status.PAUSED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey("invoices.Invoice", on_delete=models.PROTECT, related_name="enrollments")
    sequence = models.ForeignKey(Sequence, on_delete=models.PROTECT, related_name="enrollments")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    last_completed_step = models.PositiveSmallIntegerField(default=0)
    pending_step = models.PositiveSmallIntegerField(null=True, blank=True)
    # When the next eligible step becomes due; null while a step is pending or the enrollment ended.
    next_action_at = models.DateTimeField(null=True, blank=True)
    # Raised by a broken promise (Phase 04): the next email is one tone firmer, capped at FIRM.
    escalation_level = models.PositiveSmallIntegerField(default=0)
    # Why progress is held, in operator terms (e.g. delivery_unknown); empty when progressing.
    blocked_reason = models.CharField(max_length=50, blank=True)
    pause_reason = models.CharField(max_length=200, blank=True)
    exit_reason = models.CharField(max_length=50, blank=True)
    # Send history from before sequences that does not map cleanly onto the steps.
    review_required = models.BooleanField(default=False)
    review_reason = models.CharField(max_length=300, blank=True)
    history = models.JSONField(default=dict, blank=True)
    # Set while a draft for `pending_step` is being generated outside any transaction.
    drafting_started_at = models.DateTimeField(null=True, blank=True)
    # Transient AI failures for the pending step: retried after an exponential backoff (M09.1).
    generation_failures = models.PositiveSmallIntegerField(default=0)
    retry_after = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "next_action_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["invoice"], condition=Q(status__in=["ACTIVE", "PAUSED"]), name="one_live_enrollment_per_invoice"
            ),
            models.CheckConstraint(
                condition=Q(pending_step__isnull=True) | Q(pending_step__gt=F("last_completed_step")),
                name="pending_step_after_completed",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.invoice_id} in {self.sequence_id} ({self.status})"

    @property
    def next_step(self) -> int:
        return self.last_completed_step + 1

import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.invoices.models import Invoice


class Tone(models.TextChoices):
    FRIENDLY = "FRIENDLY", "Friendly"
    PROFESSIONAL = "PROFESSIONAL", "Professional"
    FIRM = "FIRM", "Firm"


class InvalidTransition(Exception):
    pass


class FollowUp(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        # Reserved for email delivery (Milestone 04); nothing transitions into them yet.
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"

    # At most one follow-up per invoice may be in an active status.
    ACTIVE_STATUSES = (Status.DRAFT, Status.APPROVED)

    ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
        Status.DRAFT: frozenset({Status.APPROVED, Status.REJECTED}),
        Status.APPROVED: frozenset(),
        Status.REJECTED: frozenset(),
        Status.SENT: frozenset(),
        Status.FAILED: frozenset(),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="follow_ups")
    subject = models.CharField(max_length=150)
    body = models.TextField()
    tone = models.CharField(max_length=20, choices=Tone.choices)
    reasoning = models.TextField()
    recommended_action = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    # Invoice facts the draft was written from; approval is refused if they change.
    invoice_snapshot = models.JSONField()
    ai_model = models.CharField(max_length=100, blank=True)
    generated_at = models.DateTimeField(default=timezone.now)
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-generated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["invoice"],
                condition=Q(status__in=["DRAFT", "APPROVED"]),
                name="one_active_follow_up_per_invoice",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_status_display()} follow-up for {self.invoice_id}"

    def can_transition_to(self, new_status: str) -> bool:
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, frozenset())

    def transition_to(self, new_status: str) -> None:
        if not self.can_transition_to(new_status):
            raise InvalidTransition(f"Cannot change a {self.status} follow-up to {new_status}.")
        self.status = new_status
        if new_status == self.Status.APPROVED:
            self.approved_at = timezone.now()


class GenerationAttempt(models.Model):
    """Audit record of each AI generation attempt. Holds no prompts, keys or customer data."""

    class Outcome(models.TextChoices):
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="generation_attempts")
    follow_up = models.ForeignKey(FollowUp, on_delete=models.SET_NULL, null=True, blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    error_code = models.CharField(max_length=50, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    ai_model = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class PromiseToPay(models.Model):
    """
    A customer's commitment to pay an invoice by a date (v2 §6), confirmed by a person, never taken
    straight from AI output.

    OPEN -> KEPT (the sheet showed the invoice PAID by promised_date + PROMISE_GRACE_DAYS), BROKEN (the
    grace period passed and it was still unpaid: the next reminder is one tone firmer) or CANCELLED (an
    approver withdrew it). At most one OPEN promise per invoice. While OPEN it owns a chase hold.
    """

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        KEPT = "KEPT", "Kept"
        BROKEN = "BROKEN", "Broken"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey("invoices.Invoice", on_delete=models.PROTECT, related_name="promises")
    client = models.ForeignKey("invoices.Client", on_delete=models.PROTECT, related_name="promises")
    reply = models.ForeignKey("inbox.InboundMessage", on_delete=models.PROTECT, null=True, blank=True,
                              related_name="promises")
    promised_date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    note = models.CharField(max_length=500, blank=True)
    # The person who confirmed it and is responsible for following it up.
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    resolution_note = models.CharField(max_length=500, blank=True)
    # When the sheet first showed the invoice PAID after the promise (evidence for KEPT).
    paid_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["invoice"], condition=Q(status="OPEN"), name="one_open_promise_per_invoice"),
            models.UniqueConstraint(fields=["reply"], condition=Q(reply__isnull=False), name="one_promise_per_reply"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="promise_amount_positive"),
            models.CheckConstraint(
                condition=Q(status="OPEN", resolved_at__isnull=True) | (~Q(status="OPEN") & Q(resolved_at__isnull=False)),
                name="promise_resolved_at_matches_status",
            ),
        ]

    def __str__(self) -> str:
        return f"Promise on {self.invoice_id} by {self.promised_date} ({self.status})"


class DisputeCase(models.Model):
    """
    A customer's dispute of an invoice (v2 §6). While OPEN it owns a chase hold that blocks every chasing
    path with 409 invoice_disputed. An APPROVER resolves it: RESOLVED_VALID (the customer is right: the
    sequence exits until the sheet changes the invoice) or RESOLVED_INVALID (chasing may resume once no
    other hold remains). Resolving never sends anything.
    """

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        RESOLVED_VALID = "RESOLVED_VALID", "Resolved: customer is right"
        RESOLVED_INVALID = "RESOLVED_INVALID", "Resolved: invoice stands"

    class Reason(models.TextChoices):
        AMOUNT = "AMOUNT", "Amount is wrong"
        WORK_NOT_DONE = "WORK_NOT_DONE", "Work or goods not delivered"
        QUALITY = "QUALITY", "Quality of the work"
        ALREADY_PAID = "ALREADY_PAID", "Already paid"
        NOT_ORDERED = "NOT_ORDERED", "Not ordered or not agreed"
        OTHER = "OTHER", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey("invoices.Invoice", on_delete=models.PROTECT, related_name="disputes")
    client = models.ForeignKey("invoices.Client", on_delete=models.PROTECT, related_name="disputes")
    reply = models.ForeignKey("inbox.InboundMessage", on_delete=models.PROTECT, null=True, blank=True,
                              related_name="disputes")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    description = models.CharField(max_length=1000)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                              related_name="+")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    resolution = models.CharField(max_length=1000, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["invoice"], condition=Q(status="OPEN"), name="one_open_dispute_per_invoice"),
            models.UniqueConstraint(fields=["reply"], condition=Q(reply__isnull=False), name="one_dispute_per_reply"),
            models.CheckConstraint(
                condition=(Q(status="OPEN", resolved_at__isnull=True)
                           | (~Q(status="OPEN") & Q(resolved_at__isnull=False, resolved_by__isnull=False))),
                name="dispute_resolution_matches_status",
            ),
        ]

    def __str__(self) -> str:
        return f"Dispute on {self.invoice_id} ({self.status})"


class DisputeNote(models.Model):
    """Internal notes on a dispute; append-only (never edited or deleted through the application)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dispute = models.ForeignKey(DisputeCase, on_delete=models.PROTECT, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    text = models.CharField(max_length=2000)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]

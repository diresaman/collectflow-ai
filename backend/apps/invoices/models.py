import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone


class Client(models.Model):
    """A billed client, identified by the lowercased email of their invoices in the sheet."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    # Latest client_name seen for this email in the sheet.
    display_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name", "email"]
        constraints = [
            models.CheckConstraint(condition=Q(email=Lower("email")), name="client_email_lowercase"),
        ]

    def __str__(self) -> str:
        return f"{self.display_name} <{self.email}>"

    @classmethod
    def for_invoice(cls, email: str, name: str) -> "Client":
        client, _ = cls.objects.get_or_create(email=email.strip().lower(), defaults={"display_name": name})
        return client


class Invoice(models.Model):
    class Status(models.TextChoices):
        UNPAID = "UNPAID", "Unpaid"
        PAID = "PAID", "Paid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    external_id = models.CharField(max_length=100, unique=True)
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="invoices")
    client_name = models.CharField(max_length=255)
    client_email = models.EmailField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    due_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNPAID)
    # False once a successful sync no longer contains the row. Kept for history; never chased.
    in_sheet = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date", "external_id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="invoice_amount_positive"),
            models.CheckConstraint(
                condition=Q(status__in=["UNPAID", "PAID"]), name="invoice_status_valid"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.external_id} ({self.client_name})"

    def save(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.client_id is None:
            self.client = Client.for_invoice(self.client_email, self.client_name)
        super().save(*args, **kwargs)

    @property
    def days_overdue(self) -> int:
        """Whole days past the due date for unpaid invoices; 0 otherwise (incl. due today)."""
        if self.status != self.Status.UNPAID:
            return 0
        return max((timezone.localdate() - self.due_date).days, 0)

    @property
    def is_overdue(self) -> bool:
        return self.days_overdue > 0


class AutomationRun(models.Model):
    """
    A recorded run. SYNC runs are Google Sheets synchronizations (status SUCCEEDED/FAILED).
    AUTOMATION runs are proactive follow-up runs (status COMPLETED/PARTIAL/FAILED) that
    start with their own SYNC run (`sync_run`) and record per-invoice items.
    """

    class Kind(models.TextChoices):
        SYNC = "SYNC", "Google Sheets sync"
        AUTOMATION = "AUTOMATION", "Follow-up automation"
        SEQUENCE_TICK = "SEQUENCE_TICK", "Sequence tick"

    class Trigger(models.TextChoices):
        MANUAL = "MANUAL", "Manual (dashboard/API)"
        COMMAND = "COMMAND", "Management command"
        SCHEDULED = "SCHEDULED", "Scheduled (run_automation)"

    class Status(models.TextChoices):
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        COMPLETED = "COMPLETED", "Completed"
        PARTIAL = "PARTIAL", "Partially completed"
        FAILED = "FAILED", "Failed"

    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SYNC)
    trigger = models.CharField(max_length=20, choices=Trigger.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    records_processed = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    unchanged_count = models.PositiveIntegerField(default=0)
    rejected_count = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True)
    # Structured row-level validation errors: [{"row": int | None, "field": str | None, "message": str}]
    errors = models.JSONField(default=list, blank=True)

    # AUTOMATION runs only.
    sync_run = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="automation_runs"
    )
    invoices_evaluated = models.PositiveIntegerField(default=0)
    # Unpaid invoices past their due date at evaluation time.
    invoices_overdue = models.PositiveIntegerField(default=0)
    drafts_generated = models.PositiveIntegerField(default=0)
    invoices_skipped = models.PositiveIntegerField(default=0)
    generation_failures = models.PositiveIntegerField(default=0)
    # Set when the interactive workflow for this run is closed (automatically if nothing needed review).
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]

    def __str__(self) -> str:
        return f"{self.trigger} {self.kind.lower()} at {self.started_at:%Y-%m-%d %H:%M} ({self.status})"


class ChaseHold(models.Model):
    """
    Stops all chasing of an invoice while it is active: no draft, approval, send reservation or
    delivery (see followups.eligibility). Placed in the same transaction that stores a customer reply,
    so a reply always wins against a sequence tick that is already running; released by a person or
    by a confidently classified harmless reply (out of office, information request).

    One active hold per (invoice, reply); chasing resumes only when every hold is released.
    """

    class Reason(models.TextChoices):
        AWAITING_CLASSIFICATION = "AWAITING_CLASSIFICATION", "Reply awaiting classification"
        NEEDS_REVIEW = "NEEDS_REVIEW", "Reply needs review"
        PROMISE_TO_PAY = "PROMISE_TO_PAY", "Promise to pay"
        DISPUTE = "DISPUTE", "Dispute"
        CLAIMS_PAID = "CLAIMS_PAID", "Client says it is paid"
        WRONG_CONTACT = "WRONG_CONTACT", "Wrong contact"

    # Only an approver may resume chasing after a payment, promise or dispute signal.
    PROTECTED_REASONS = (Reason.PROMISE_TO_PAY, Reason.DISPUTE, Reason.CLAIMS_PAID)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="chase_holds")
    reason = models.CharField(max_length=30, choices=Reason.choices)
    # What owns the hold (no FKs, so this app stays independent). Exactly one is set: the inbound reply
    # (inbox.InboundMessage, M10), the promise (cases.PromiseToPay) or the dispute (cases.DisputeCase, M11).
    # Only its owner releases it, so resolving one never clears another.
    reply_id = models.UUIDField(null=True, blank=True)
    promise_id = models.UUIDField(null=True, blank=True)
    dispute_id = models.UUIDField(null=True, blank=True)
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    release_note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["invoice", "released_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "reply_id"], condition=Q(released_at__isnull=True),
                name="one_active_hold_per_invoice_reply",
            ),
            models.UniqueConstraint(
                fields=["invoice", "promise_id"], condition=Q(released_at__isnull=True, promise_id__isnull=False),
                name="one_active_hold_per_invoice_promise",
            ),
            models.UniqueConstraint(
                fields=["invoice", "dispute_id"], condition=Q(released_at__isnull=True, dispute_id__isnull=False),
                name="one_active_hold_per_invoice_dispute",
            ),
            models.CheckConstraint(
                condition=(Q(reply_id__isnull=False, promise_id__isnull=True, dispute_id__isnull=True)
                           | Q(reply_id__isnull=True, promise_id__isnull=False, dispute_id__isnull=True)
                           | Q(reply_id__isnull=True, promise_id__isnull=True, dispute_id__isnull=False)),
                name="hold_has_exactly_one_owner",
            ),
        ]

    def __str__(self) -> str:
        state = "released" if self.released_at else "active"
        return f"{self.reason} hold on {self.invoice_id} ({state})"

    @property
    def protected(self) -> bool:
        return self.reason in self.PROTECTED_REASONS

    @property
    def source(self) -> str:
        return "dispute" if self.dispute_id else "promise" if self.promise_id else "reply"

    @property
    def release_rule(self) -> str:
        """Who or what ends this hold, in operator terms."""
        if self.dispute_id:
            return "An approver resolves the dispute."
        if self.promise_id:
            return "Ends when the promise is kept or broken (checked automatically), or an approver cancels it."
        if self.protected:
            return "An approver reviews the reply (with a note), or the promise or dispute is recorded."
        return "Anyone reviewing the reply in the Inbox."

import uuid

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

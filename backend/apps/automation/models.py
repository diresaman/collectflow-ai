import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.followups.models import FollowUp
from apps.invoices.models import AutomationRun, Invoice


class AutomationRunItem(models.Model):
    """Outcome for one invoice in an automation run. Holds identifiers and codes, not customer details."""

    class Outcome(models.TextChoices):
        GENERATED = "GENERATED", "Draft generated"
        SKIPPED = "SKIPPED", "Skipped"
        FAILED = "FAILED", "Generation failed"

    run = models.ForeignKey(AutomationRun, on_delete=models.CASCADE, related_name="items")
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    invoice_external_id = models.CharField(max_length=100)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    reason_code = models.CharField(max_length=50, blank=True)
    detail = models.CharField(max_length=300, blank=True)
    follow_up = models.ForeignKey(FollowUp, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    # Operator chose not to send this approved reminder in this workflow (it stays APPROVED).
    left_unsent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]


class WorkflowStep(models.Model):
    """
    Persisted progress of the automated phases of a workflow (an AUTOMATION run).
    The human phases (approval, send confirmation, sending) are derived from follow-ups and
    delivery attempts instead; see apps/automation/workflow.py.
    """

    class Key(models.TextChoices):
        SYNCING_INVOICES = "SYNCING_INVOICES", "Syncing invoices"
        EVALUATING_INVOICES = "EVALUATING_INVOICES", "Evaluating invoices"
        GENERATING_DRAFTS = "GENERATING_DRAFTS", "Generating drafts"
        FINALIZING = "FINALIZING", "Finalizing"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"
        SKIPPED = "SKIPPED", "Skipped"

    run = models.ForeignKey(AutomationRun, on_delete=models.CASCADE, related_name="workflow_steps")
    key = models.CharField(max_length=30, choices=Key.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    detail = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "key"], name="one_step_per_key_per_run")]


class AutomationLease(models.Model):
    """
    Database-backed mutual exclusion for automation runs (one row per lease name).

    Acquired and renewed with conditional UPDATEs, which are atomic on SQLite too. The owner
    renews `expires_at` (heartbeat) after every step; another process may only take over once
    the lease has expired, i.e. the owner has made no progress for the whole lease duration.
    """

    name = models.CharField(max_length=50, primary_key=True)
    owner = models.CharField(max_length=64, blank=True)
    run = models.ForeignKey(AutomationRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    acquired_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)


class OutboxJob(models.Model):
    """
    A unit of background work, written in the same transaction as the state change that needs it.

    The worker claims a job with a conditional UPDATE (PENDING -> RUNNING), which only one worker
    can win on SQLite too, then runs the handler with no transaction open. Provider calls (Sheets,
    Gemini, Gmail) only ever happen inside jobs or unattended commands, never in a web request.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=50)
    payload = models.JSONField(default=dict)
    idempotency_key = models.CharField(max_length=200, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    run_after = models.DateTimeField(default=timezone.now)
    locked_by = models.CharField(max_length=64, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    # Handler output (IDs and outcome codes only), shown by GET /api/jobs/{id}/.
    result = models.JSONField(default=dict, blank=True)
    last_error_code = models.CharField(max_length=50, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["status", "run_after"])]
        constraints = [
            models.CheckConstraint(condition=Q(attempts__lte=F("max_attempts")), name="job_attempts_within_max"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.status} ({self.pk})"


class WorkerHeartbeat(models.Model):
    """Last sign of life from the worker process, for health reporting."""

    name = models.CharField(max_length=50, primary_key=True)
    worker_id = models.CharField(max_length=64)
    pid = models.PositiveIntegerField()
    started_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()

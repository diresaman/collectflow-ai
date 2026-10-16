from django.db import models
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

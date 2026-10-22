import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Task(models.Model):
    """
    Work for a person: CALL tasks from sequence steps (M09), follow-up work from customer replies (M10)
    and from disputes, plus manual follow-ups (M11). OPEN -> DONE or CANCELLED; `done_at` stays in step
    with DONE (the sequence engine reads it for call steps).
    """

    class Kind(models.TextChoices):
        CALL = "CALL", "Call the client"
        SEND_INFO = "SEND_INFO", "Information request"
        UPDATE_CONTACT = "UPDATE_CONTACT", "Contact update"
        RECONCILE = "RECONCILE", "Reconciliation"
        REVIEW_DISPUTE = "REVIEW_DISPUTE", "Dispute review"
        FOLLOW_UP = "FOLLOW_UP", "Other follow-up"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        DONE = "DONE", "Done"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    title = models.CharField(max_length=200)
    invoice = models.ForeignKey("invoices.Invoice", on_delete=models.PROTECT, null=True, blank=True, related_name="tasks")
    client = models.ForeignKey("invoices.Client", on_delete=models.PROTECT, related_name="tasks")
    enrollment = models.ForeignKey(
        "sequences.SequenceEnrollment", on_delete=models.PROTECT, null=True, blank=True, related_name="tasks"
    )
    step = models.ForeignKey("sequences.SequenceStep", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    # The customer reply that created this task (M10); at most one task of each kind per reply.
    reply = models.ForeignKey("inbox.InboundMessage", on_delete=models.PROTECT, null=True, blank=True, related_name="tasks")
    detail = models.CharField(max_length=500, blank=True)
    # The dispute this task belongs to (M11), e.g. the approver's review.
    dispute = models.ForeignKey("cases.DisputeCase", on_delete=models.PROTECT, null=True, blank=True, related_name="tasks")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    due_at = models.DateTimeField(default=timezone.now)
    done_at = models.DateTimeField(null=True, blank=True)
    done_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    completion_note = models.CharField(max_length=500, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    # Set once the overdue check recorded this task as overdue, so it is reported only once.
    overdue_notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["done_at", "due_at"]
        constraints = [
            # A sequence step creates at most one task, however many ticks or workers see it.
            models.UniqueConstraint(
                fields=["enrollment", "step"], condition=Q(step__isnull=False), name="one_task_per_enrollment_step"
            ),
            models.UniqueConstraint(
                fields=["reply", "kind"], condition=Q(reply__isnull=False), name="one_task_per_reply_kind"
            ),
            models.UniqueConstraint(
                fields=["dispute", "kind"], condition=Q(dispute__isnull=False), name="one_task_per_dispute_kind"
            ),
            models.CheckConstraint(
                condition=(Q(status="OPEN", done_at__isnull=True, cancelled_at__isnull=True)
                           | Q(status="DONE", done_at__isnull=False) | Q(status="CANCELLED", cancelled_at__isnull=False)),
                name="task_status_matches_timestamps",
            ),
        ]
        indexes = [models.Index(fields=["status", "due_at"])]

    def __str__(self) -> str:
        return f"{self.kind}: {self.title}"

    @property
    def is_overdue(self) -> bool:
        return self.status == self.Status.OPEN and self.due_at < timezone.now()


class ReplyLabel(models.TextChoices):
    """The seven approved reply labels (v2 §6). The AI proposes one; the backend decides what happens."""

    PROMISE_TO_PAY = "PROMISE_TO_PAY", "Promise to pay"
    DISPUTE = "DISPUTE", "Dispute"
    CLAIMS_PAID = "CLAIMS_PAID", "Claims paid"
    REQUESTS_INFO = "REQUESTS_INFO", "Requests information"
    OUT_OF_OFFICE = "OUT_OF_OFFICE", "Out of office"
    WRONG_CONTACT = "WRONG_CONTACT", "Wrong contact"
    OTHER = "OTHER", "Other"


class MailboxCursor(models.Model):
    """
    Where reply polling stands in the Gmail mailbox (one row, name="gmail"): the history cursor,
    a lease so polls never overlap, and the outcome of the last poll for the health page.
    The cursor only moves after every message up to it has been stored.
    """

    name = models.CharField(max_length=20, primary_key=True)
    history_id = models.CharField(max_length=40, blank=True)
    bootstrapped_at = models.DateTimeField(null=True, blank=True)
    lease_owner = models.CharField(max_length=64, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_poll_started_at = models.DateTimeField(null=True, blank=True)
    last_poll_finished_at = models.DateTimeField(null=True, blank=True)
    last_poll_succeeded_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=50, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    last_rescan_at = models.DateTimeField(null=True, blank=True)
    last_rescan_reason = models.CharField(max_length=30, blank=True)
    messages_ingested = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class InboundMessage(models.Model):
    """
    A customer reply in a Gmail conversation CollectFlow started. Stored once (unique Gmail message
    ID); mail in any other conversation is never fetched or stored. The body is plain text with quotes
    and signatures removed, capped, and purged after INBOX_BODY_RETENTION_DAYS.

    `state`: RECEIVED (awaiting AI classification), ACTIONED (a confident label's action was applied),
    TRIAGE (a person must look: low confidence, failure, OTHER, promise, dispute…), RESOLVED (a person
    classified or confirmed it). `label`/`confidence`/`extracted` hold the current classification;
    every proposal and correction is kept in ReplyClassification.
    """

    class State(models.TextChoices):
        RECEIVED = "RECEIVED", "Awaiting classification"
        ACTIONED = "ACTIONED", "Handled automatically"
        TRIAGE = "TRIAGE", "Needs review"
        RESOLVED = "RESOLVED", "Resolved"

    class SenderCheck(models.TextChoices):
        MATCH = "MATCH", "Invoice contact"
        SAME_DOMAIN = "SAME_DOMAIN", "Same domain as the invoice contact"
        MISMATCH = "MISMATCH", "Unknown sender"

    class Source(models.TextChoices):
        AI = "AI", "AI"
        HUMAN = "HUMAN", "Person"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    gmail_message_id = models.CharField(max_length=100, unique=True)
    gmail_thread_id = models.CharField(max_length=100, db_index=True)
    rfc_message_id = models.CharField(max_length=255, blank=True)
    in_reply_to = models.TextField(blank=True)
    invoice = models.ForeignKey("invoices.Invoice", on_delete=models.PROTECT, related_name="replies")
    client = models.ForeignKey("invoices.Client", on_delete=models.PROTECT, related_name="replies")
    # The latest CollectFlow message in this conversation when the reply was stored.
    replied_to = models.ForeignKey(
        "followups.DeliveryAttempt", on_delete=models.PROTECT, null=True, blank=True, related_name="replies_received"
    )
    # Every invoice CollectFlow wrote about in this conversation (normally just `invoice`).
    thread_invoice_ids = models.JSONField(default=list, blank=True)
    sender_email = models.CharField(max_length=254)
    sender_name = models.CharField(max_length=255, blank=True)
    sender_check = models.CharField(max_length=12, choices=SenderCheck.choices)
    subject = models.CharField(max_length=300, blank=True)
    received_at = models.DateTimeField()
    body = models.TextField(blank=True)
    body_truncated = models.BooleanField(default=False)
    body_purged_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.RECEIVED)
    triage_reason = models.CharField(max_length=50, blank=True)
    label = models.CharField(max_length=20, choices=ReplyLabel.choices, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    label_source = models.CharField(max_length=5, choices=Source.choices, blank=True)
    extracted = models.JSONField(default=dict, blank=True)
    rationale = models.CharField(max_length=500, blank=True)
    classify_attempts = models.PositiveSmallIntegerField(default=0)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [models.Index(fields=["state", "received_at"])]

    def __str__(self) -> str:
        return f"Reply {self.gmail_message_id} on {self.invoice_id} ({self.state})"


class ReplyClassification(models.Model):
    """
    Append-only log (database triggers refuse UPDATE/DELETE) of every classification of a reply: each
    AI proposal (including failed attempts, with no label) and each person's classification or
    correction, with what the backend decided to do about it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(InboundMessage, on_delete=models.PROTECT, related_name="classifications")
    source = models.CharField(max_length=5, choices=InboundMessage.Source.choices)
    label = models.CharField(max_length=20, choices=ReplyLabel.choices, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    extracted = models.JSONField(default=dict, blank=True)
    rationale = models.CharField(max_length=500, blank=True)
    # What the backend did: applied, triage:<reason>, failed:<code>, retry_scheduled, human.
    decision = models.CharField(max_length=60)
    error_code = models.CharField(max_length=50, blank=True)
    ai_model = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=40, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(source="AI") | Q(actor__isnull=False), name="human_classification_has_actor"
            ),
            models.CheckConstraint(
                condition=Q(confidence__isnull=True) | Q(confidence__gte=0, confidence__lte=1),
                name="classification_confidence_0_to_1",
            ),
        ]

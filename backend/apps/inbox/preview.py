"""
Read-only preview of the first reply discovery (M10, Step 2 of the controlled rollout).

Before polling is switched on, this shows what the first poll's scan of the last INBOX_RESCAN_DAYS
would store: how many replies, on which invoices, and which sequences a hold would stop. It applies
the same filters as ingestion (CollectFlow threads only, own messages and older mail skipped) but:

- reads Gmail **headers only** (`format=metadata`): never a message body;
- writes nothing: no replies, holds, jobs, cursor, timeline events or enrollment changes;
- runs with GMAIL_READ_ENABLED off (it still needs the gmail.readonly grant).

The report holds invoice numbers, counts, sender-check categories and dates; no addresses, subjects
or text.
"""

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.followups.models import DeliveryAttempt, FollowUp
from apps.invoices.models import ChaseHold, Invoice
from apps.sequences.models import SequenceEnrollment
from integrations.mailbox import MailboxReader

from .ingestion import SKIP_LABELS, known_threads, sender_check
from .models import InboundMessage


@dataclass
class InvoicePreview:
    external_id: str
    status: str
    in_sheet: bool
    replies: int = 0
    sender_checks: Counter = field(default_factory=Counter)
    first_received: dt.datetime | None = None
    last_received: dt.datetime | None = None
    enrollment: str = "not enrolled"
    active_reminder: str = ""
    existing_holds: int = 0

    @property
    def effect(self) -> str:
        if self.status != Invoice.Status.UNPAID or not self.in_sheet:
            return "hold placed, but the invoice is not being chased anyway"
        if self.active_reminder in ("DRAFT", "APPROVED"):
            return f"chasing stops; the {self.active_reminder.lower()} reminder can no longer be approved or sent"
        if self.active_reminder in ("SENDING", "DELIVERY_UNKNOWN"):
            return "chasing stops (a delivery is unresolved)"
        return "chasing stops until the reply is reviewed"


@dataclass
class PreviewReport:
    window_days: int
    threads_scanned: int = 0
    messages_seen: int = 0
    would_store: int = 0
    skipped: Counter = field(default_factory=Counter)
    invoices: dict[str, InvoicePreview] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days, "threads_scanned": self.threads_scanned,
            "messages_seen": self.messages_seen, "would_store": self.would_store,
            "skipped": dict(self.skipped), "invoices_affected": len(self.invoices),
            "invoices": [
                {"invoice": p.external_id, "replies": p.replies, "sender_checks": dict(p.sender_checks),
                 "first_received": p.first_received, "last_received": p.last_received,
                 "enrollment": p.enrollment, "active_reminder": p.active_reminder or None,
                 "existing_holds": p.existing_holds, "effect": p.effect}
                for p in sorted(self.invoices.values(), key=lambda p: p.external_id)
            ],
        }


def _invoice_preview(invoice: Invoice) -> InvoicePreview:
    preview = InvoicePreview(invoice.external_id, invoice.status, invoice.in_sheet)
    enrollment = (SequenceEnrollment.objects.filter(invoice=invoice, status__in=SequenceEnrollment.LIVE_STATUSES)
                  .first())
    if enrollment is not None:
        position = (f"step {enrollment.pending_step} in progress" if enrollment.pending_step
                    else f"next step {enrollment.next_step}")
        preview.enrollment = f"{enrollment.status.lower()}, {position}"
    active = FollowUp.objects.filter(invoice=invoice, status__in=FollowUp.ACTIVE_STATUSES).first()
    preview.active_reminder = active.status if active else ""
    preview.existing_holds = ChaseHold.objects.filter(invoice=invoice, released_at__isnull=True).count()
    return preview


def preview(reader: MailboxReader, *, days: int | None = None) -> PreviewReport:
    days = days or settings.INBOX_RESCAN_DAYS
    report = PreviewReport(window_days=days)
    reader.profile()  # confirms the token belongs to the sender mailbox
    since = timezone.now() - dt.timedelta(days=days)
    own_ids = set(DeliveryAttempt.objects.exclude(gmail_message_id="").values_list("gmail_message_id", flat=True))
    stored = set(InboundMessage.objects.values_list("gmail_message_id", flat=True))

    for thread_id, attempts in known_threads().items():
        if (attempts[-1].completed_at or attempts[-1].started_at) < since:
            continue
        report.threads_scanned += 1
        first_send = attempts[0].completed_at or attempts[0].started_at
        invoice = attempts[-1].follow_up.invoice
        thread_invoices = {a.follow_up.invoice_id: a.follow_up.invoice for a in attempts}
        for ref in reader.thread_messages(thread_id):
            report.messages_seen += 1
            if SKIP_LABELS.intersection(ref.label_ids) or ref.message_id in own_ids:
                report.skipped["own_message"] += 1
                continue
            if ref.message_id in stored:
                report.skipped["already_stored"] += 1
                continue
            meta = reader.get_metadata(ref.message_id)
            if meta is None:
                report.skipped["deleted"] += 1
                continue
            sender = parseaddr(meta.headers.get("from", ""))[1].strip().lower()
            if SKIP_LABELS.intersection(meta.label_ids) or not sender or sender == reader.mailbox_email:
                report.skipped["own_message"] += 1
                continue
            received = (dt.datetime.fromtimestamp(meta.internal_date_ms / 1000, tz=dt.timezone.utc)
                        if meta.internal_date_ms else timezone.now())
            if received < first_send - dt.timedelta(minutes=5):
                report.skipped["older_than_first_send"] += 1
                continue
            report.would_store += 1
            check = sender_check(sender, invoice.client_email)
            for held in thread_invoices.values():
                item = report.invoices.get(held.external_id) or _invoice_preview(held)
                report.invoices[held.external_id] = item
                item.replies += 1
                item.sender_checks[check] += 1
                item.first_received = min(filter(None, [item.first_received, received]))
                item.last_received = max(filter(None, [item.last_received, received]))
    return report

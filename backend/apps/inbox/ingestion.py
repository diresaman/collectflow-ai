"""
Reply ingestion (M10): Gmail history polling with a persisted cursor.

- Only conversations CollectFlow started are read: a message's content is fetched only when its
  Gmail thread has a SENT CollectFlow delivery. Everything else in the mailbox is never fetched.
- CollectFlow's own messages (SENT label, the mailbox's own address, known delivery IDs) are skipped.
- Each reply is stored once (unique Gmail message ID). Storing it, putting its invoice(s) on hold and
  queueing classification happen in one transaction, so chasing stops the moment a reply is stored.
- The history cursor only advances after every message up to it was stored. A crash or error leaves
  it where it was; the next poll re-reads the same history and duplicates are ignored.
- An expired cursor (Gmail keeps about a week of history) or the first poll scans the known threads
  of the last INBOX_RESCAN_DAYS directly, then continues from the mailbox's current history ID.
- A lease on the cursor row keeps polls from overlapping, across workers and restarts.
"""

import datetime as dt
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.automation import outbox
from apps.followups.models import DeliveryAttempt, FollowUp
from apps.invoices.models import ChaseHold
from apps.timeline import services as timeline
from apps.timeline.models import ActorType, EventType
from integrations.mailbox import HistoryExpired, MailboxError, MailboxReader, MessageRef

from . import holds
from .models import InboundMessage, MailboxCursor
from .text import reply_text

logger = logging.getLogger(__name__)

POLL_JOB_KIND = "inbox.poll"
CLASSIFY_JOB_KIND = "inbox.classify"
CURSOR_NAME = "gmail"
LEASE_SECONDS = 600
MAX_HISTORY_PAGES = 100
SKIP_LABELS = {"SENT", "DRAFT", "SPAM"}


@dataclass
class PollStats:
    ingested: int = 0
    duplicates: int = 0
    own: int = 0
    unrelated: int = 0
    gone: int = 0
    pages: int = 0
    rescanned_threads: int = 0
    message_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "message_ids"} | {"reply_ids": self.message_ids}


# Known conversations ----------------------------------------------------------------------------


def known_threads() -> dict[str, list[DeliveryAttempt]]:
    """Gmail thread ID -> CollectFlow's SENT deliveries in it, oldest first."""
    threads: dict[str, list[DeliveryAttempt]] = defaultdict(list)
    attempts = (
        DeliveryAttempt.objects.filter(status=DeliveryAttempt.Status.SENT).exclude(gmail_thread_id="")
        .select_related("follow_up__invoice__client").order_by("completed_at", "started_at")
    )
    for attempt in attempts:
        threads[attempt.gmail_thread_id].append(attempt)
    return threads


def _invoices_written_about(attempt: DeliveryAttempt) -> list[str]:
    """A reminder is about its invoice; a consolidated statement (M14) about every invoice it lists."""
    follow_up = attempt.follow_up
    if follow_up.kind == FollowUp.Kind.STATEMENT:
        return [str(i) for i in follow_up.statement_lines.order_by("position").values_list("invoice_id", flat=True)]
    return [str(follow_up.invoice_id)]


def sender_check(sender: str, invoice_email: str) -> str:
    sender, expected = sender.lower(), invoice_email.lower()
    if sender == expected:
        return InboundMessage.SenderCheck.MATCH
    if "@" in sender and sender.rsplit("@", 1)[1] == expected.rsplit("@", 1)[-1]:
        return InboundMessage.SenderCheck.SAME_DOMAIN
    return InboundMessage.SenderCheck.MISMATCH


# Storing one message --------------------------------------------------------------------------


def ingest(reader: MailboxReader, ref: MessageRef, threads: dict[str, list[DeliveryAttempt]], stats: PollStats) -> None:
    attempts = threads.get(ref.thread_id)
    if not attempts:
        stats.unrelated += 1  # never fetched: not a CollectFlow conversation
        return
    if SKIP_LABELS.intersection(ref.label_ids):
        stats.own += 1
        return
    if InboundMessage.objects.filter(gmail_message_id=ref.message_id).exists():
        stats.duplicates += 1
        return
    if DeliveryAttempt.objects.filter(gmail_message_id=ref.message_id).exists():
        stats.own += 1
        return

    message = reader.get_message(ref.message_id)
    if message is None:
        stats.gone += 1
        return
    if SKIP_LABELS.intersection(message.label_ids):
        stats.own += 1
        return
    sender_name, sender = parseaddr(message.headers.get("from", ""))
    sender = sender.strip().lower()
    if not sender or sender == reader.mailbox_email:
        stats.own += 1
        return
    received_at = (dt.datetime.fromtimestamp(message.internal_date_ms / 1000, tz=dt.timezone.utc)
                   if message.internal_date_ms else timezone.now())
    first_send = attempts[0].completed_at or attempts[0].started_at
    if received_at < first_send - dt.timedelta(minutes=5):
        stats.unrelated += 1  # older than CollectFlow's first message in the thread
        return

    latest = attempts[-1]
    invoice = latest.follow_up.invoice
    invoice_ids = list(dict.fromkeys(invoice_id for a in attempts for invoice_id in _invoices_written_about(a)))
    body, truncated = reply_text(message.payload, max_chars=settings.INBOX_BODY_MAX_CHARS)
    # Only the sender is validated; other recipients copied on the reply do not matter.
    check = sender_check(sender, invoice.client_email)

    try:
        with transaction.atomic():
            locked = holds.lock_invoices(invoice_ids)
            reply = InboundMessage.objects.create(
                gmail_message_id=message.message_id, gmail_thread_id=message.thread_id,
                rfc_message_id=message.headers.get("message-id", "")[:255],
                in_reply_to=message.headers.get("in-reply-to", "")[:2000],
                invoice=invoice, client=invoice.client, replied_to=latest, thread_invoice_ids=invoice_ids,
                sender_email=sender[:254], sender_name=sender_name[:255], sender_check=check,
                subject=message.headers.get("subject", "")[:300], received_at=received_at,
                body=body, body_truncated=truncated,
            )
            # Chasing stops now, before any AI is involved; classification decides what happens next.
            holds.place(locked, reply=reply, reason=ChaseHold.Reason.AWAITING_CLASSIFICATION, actor=ActorType.SYSTEM,
                        detail="A customer reply arrived and is being classified.")
            for item in locked:
                timeline.record(EventType.REPLY_RECEIVED, actor=ActorType.SYSTEM, invoice=item, reply_id=str(reply.pk),
                                gmail_message_id=reply.gmail_message_id, gmail_thread_id=reply.gmail_thread_id,
                                sender_check=check, body_truncated=truncated)
            enqueue_classification(reply, attempt=1)
            MailboxCursor.objects.filter(name=CURSOR_NAME).update(messages_ingested=F("messages_ingested") + 1)
    except IntegrityError:
        stats.duplicates += 1  # stored concurrently
        return
    stats.ingested += 1
    stats.message_ids.append(str(reply.pk))


def enqueue_classification(reply: InboundMessage, *, attempt: int, run_after: dt.datetime | None = None,
                           created_by: Any = None, deferral: int = 0) -> Any:
    """
    One job per attempt number: re-enqueueing the same attempt returns the existing job. An attempt
    deferred because Gemini is in a quota pause keeps its number and gets its own key per deferral.
    """
    key = f"{CLASSIFY_JOB_KIND}:{reply.pk}:{attempt}" + (f":deferred-{deferral}" if deferral else "")
    payload: dict[str, Any] = {"reply_id": str(reply.pk), "attempt": attempt}
    if deferral:
        payload["deferral"] = deferral
    return outbox.enqueue(CLASSIFY_JOB_KIND, payload, idempotency_key=key, max_attempts=1,
                          created_by=created_by, run_after=run_after)


# Polling ----------------------------------------------------------------------------------------


def _cursor() -> MailboxCursor:
    cursor, _ = MailboxCursor.objects.get_or_create(name=CURSOR_NAME)
    return cursor


def _acquire(owner: str, now: dt.datetime) -> bool:
    _cursor()
    return MailboxCursor.objects.filter(
        Q(lease_owner="") | Q(lease_expires_at__lt=now) | Q(lease_owner=owner), name=CURSOR_NAME
    ).update(lease_owner=owner, lease_expires_at=now + dt.timedelta(seconds=LEASE_SECONDS),
             last_poll_started_at=now) == 1


def _renew(owner: str) -> None:
    held = MailboxCursor.objects.filter(name=CURSOR_NAME, lease_owner=owner).update(
        lease_expires_at=timezone.now() + dt.timedelta(seconds=LEASE_SECONDS)
    )
    if held != 1:
        raise RuntimeError("mailbox lease lost")


def _advance(owner: str, old: str, new: str, **fields: Any) -> None:
    """Compare-and-set: only the lease holder moves the cursor, and only from the value it read."""
    if new and new != old:
        fields["history_id"] = new
    MailboxCursor.objects.filter(name=CURSOR_NAME, lease_owner=owner, history_id=old).update(**fields)


def _rescan(reader: MailboxReader, threads: dict[str, list[DeliveryAttempt]], stats: PollStats, owner: str) -> None:
    since = timezone.now() - dt.timedelta(days=settings.INBOX_RESCAN_DAYS)
    recent = [thread_id for thread_id, attempts in threads.items()
              if (attempts[-1].completed_at or attempts[-1].started_at) >= since]
    for thread_id in recent:
        for ref in reader.thread_messages(thread_id):
            ingest(reader, ref, threads, stats)
        stats.rescanned_threads += 1
        _renew(owner)


def poll(reader: MailboxReader, *, owner: str) -> dict[str, Any]:
    now = timezone.now()
    if not _acquire(owner, now):
        return {"skipped": "poll_running"}
    stats = PollStats()
    cursor = _cursor()
    start = cursor.history_id
    try:
        _, current_history = reader.profile()  # also confirms the token belongs to the sender mailbox
        threads = known_threads()
        rescan_reason = ""
        new_history = current_history
        if not start:
            rescan_reason = "bootstrap"
        else:
            try:
                new_history = _read_history(reader, start, threads, stats, owner)
            except HistoryExpired:
                rescan_reason = "history_expired"
        if rescan_reason:
            # current_history was read before the scan, so nothing arriving during it is skipped.
            _rescan(reader, threads, stats, owner)
            new_history = current_history
        finished = timezone.now()
        extra: dict[str, Any] = {"last_poll_finished_at": finished, "last_poll_succeeded_at": finished,
                                 "last_error_code": "", "last_error": ""}
        if rescan_reason:
            extra |= {"last_rescan_at": finished, "last_rescan_reason": rescan_reason}
            if not start:
                extra["bootstrapped_at"] = finished
        _advance(owner, start, new_history, **extra)
        result = stats.as_dict() | {"rescan": rescan_reason}
    except MailboxError as exc:
        logger.warning("Reply poll failed: %s", exc.code)
        MailboxCursor.objects.filter(name=CURSOR_NAME).update(
            last_poll_finished_at=timezone.now(), last_error_code=exc.code, last_error=str(exc)[:300]
        )
        result = stats.as_dict() | {"error": exc.code}
    finally:
        MailboxCursor.objects.filter(name=CURSOR_NAME, lease_owner=owner).update(lease_owner="", lease_expires_at=None)
    return result


def _read_history(reader: MailboxReader, start: str, threads: dict[str, list[DeliveryAttempt]],
                  stats: PollStats, owner: str) -> str:
    page_token = ""
    latest = start
    for _ in range(MAX_HISTORY_PAGES):
        page = reader.history_page(start, page_token)
        stats.pages += 1
        for ref in page.messages:
            ingest(reader, ref, threads, stats)
        latest = page.history_id or latest
        _renew(owner)
        if not page.next_page_token:
            return latest
        page_token = page.next_page_token
    # Too much history to page through safely: scan the known threads instead.
    raise HistoryExpired("History too long; rescanning known threads.")


def handle_poll_job(job: Any) -> dict[str, Any]:
    if not settings.GMAIL_READ_ENABLED:
        return {"skipped": "read_not_enabled"}
    return poll(MailboxReader.from_settings(), owner=f"job-{job.pk}")


# Retention ---------------------------------------------------------------------------------------


def purge_bodies(now: dt.datetime | None = None) -> int:
    """Clears reply text older than INBOX_BODY_RETENTION_DAYS; labels, dates and the audit trail stay."""
    now = now or timezone.now()
    cutoff = now - dt.timedelta(days=settings.INBOX_BODY_RETENTION_DAYS)
    return InboundMessage.objects.filter(received_at__lt=cutoff, body_purged_at__isnull=True).update(
        body="", body_purged_at=now
    )


def handle_purge_job(job: Any) -> dict[str, Any]:
    return {"purged": purge_bodies()}

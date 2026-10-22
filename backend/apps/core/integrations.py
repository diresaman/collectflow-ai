"""
Integration health (M10): what each external dependency and the background worker last did.

Built only from local state (database rows, settings, whether the token file exists). It never calls
Google or Gemini, so the page is fast and cannot use quota. It returns no tokens, credentials,
addresses or message contents.

Statuses: not_configured, awaiting_authorization, healthy, stale, failed.
"""

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.automation import outbox
from apps.automation.models import OutboxJob
from apps.automation.scheduling import CASES_MONITOR_KIND, INBOX_POLL_KIND, INBOX_PURGE_KIND, SEQUENCE_TICK_KIND
from apps.followups import ai_budget
from apps.followups.models import DeliveryAttempt, GenerationAttempt
from apps.inbox.ingestion import CURSOR_NAME
from apps.inbox.models import InboundMessage, MailboxCursor
from apps.invoices.models import AutomationRun, ChaseHold
from integrations.gmail import GmailClient
from integrations.mailbox import MailboxReader

NOT_CONFIGURED, AWAITING, HEALTHY, STALE, FAILED = (
    "not_configured", "awaiting_authorization", "healthy", "stale", "failed",
)
GMAIL_AUTH_ERRORS = ("gmail_not_ready", "gmail_http_401", "gmail_http_403")
READ_AUTH_ERRORS = ("read_not_authorized", "read_not_configured")
AUTHORIZE_READ = ("After the owner approves read access: python manage.py authorize_gmail --with-read-access, "
                  "then set GMAIL_READ_ENABLED=true and restart the worker.")


@dataclass
class Health:
    key: str
    name: str
    status: str
    detail: str
    action: str = ""
    last_success_at: dt.datetime | None = None
    last_attempt_at: dt.datetime | None = None
    facts: dict[str, Any] = field(default_factory=dict)


def _newer(a: dt.datetime | None, b: dt.datetime | None) -> bool:
    return a is not None and (b is None or a > b)


def sheets(now: dt.datetime) -> Health:
    if not (settings.GOOGLE_SHEETS_SPREADSHEET_ID and settings.GOOGLE_APPLICATION_CREDENTIALS):
        return Health("sheets", "Google Sheets sync", NOT_CONFIGURED, "The spreadsheet or service account is not configured.",
                      "Set GOOGLE_SHEETS_SPREADSHEET_ID and GOOGLE_APPLICATION_CREDENTIALS.")
    runs = AutomationRun.objects.filter(kind=AutomationRun.Kind.SYNC)
    last = runs.exclude(status=AutomationRun.Status.RUNNING).first()
    success = runs.filter(status=AutomationRun.Status.SUCCEEDED).first()
    success_at = success.completed_at if success else None
    facts = {"invoices_in_last_sync": success.records_processed if success else None}
    if last is None:
        return Health("sheets", "Google Sheets sync", STALE, "No sync has run yet.", "Run a sync from the dashboard.",
                      facts=facts)
    attempt_at = last.completed_at or last.started_at
    if last.status == AutomationRun.Status.FAILED:
        return Health("sheets", "Google Sheets sync", FAILED, f"The last sync failed: {last.error_summary[:200]}",
                      "Check the sheet's sharing and format, then sync again.", success_at, attempt_at, facts)
    if success_at is None or now - success_at > dt.timedelta(hours=settings.STALE_DATA_HOURS):
        return Health("sheets", "Google Sheets sync", STALE,
                      f"No successful sync in the last {settings.STALE_DATA_HOURS} hours.", "Run a sync.",
                      success_at, attempt_at, facts)
    return Health("sheets", "Google Sheets sync", HEALTHY, "Synced recently.", "", success_at, attempt_at, facts)


def gmail_send() -> Health:
    status = GmailClient.from_settings().check_configured()
    if not status.ready:
        return Health("gmail_send", "Gmail sending", NOT_CONFIGURED, status.detail,
                      "Run: python manage.py authorize_gmail")
    last = DeliveryAttempt.objects.exclude(status=DeliveryAttempt.Status.SENDING).first()
    sent = DeliveryAttempt.objects.filter(status=DeliveryAttempt.Status.SENT).order_by("-completed_at").first()
    sent_at = sent.completed_at if sent else None
    attempt_at = (last.completed_at or last.started_at) if last else None
    if last is not None and last.error_code in GMAIL_AUTH_ERRORS and _newer(attempt_at, sent_at):
        return Health("gmail_send", "Gmail sending", FAILED, "The last send was refused for authorization reasons.",
                      "Run: python manage.py authorize_gmail", sent_at, attempt_at)
    return Health("gmail_send", "Gmail sending", HEALTHY, "Authorized. Every send still needs a person's approval.",
                  "", sent_at, attempt_at)


def _cursor() -> MailboxCursor | None:
    return MailboxCursor.objects.filter(name=CURSOR_NAME).first()


def gmail_read() -> Health:
    name = "Gmail read access"
    if not settings.GMAIL_READ_ENABLED:
        return Health("gmail_read", name, AWAITING, "Reading replies has not been approved or enabled.", AUTHORIZE_READ)
    ready, detail = MailboxReader.from_settings().check_configured()
    if not ready:
        return Health("gmail_read", name, NOT_CONFIGURED, detail, AUTHORIZE_READ)
    cursor = _cursor()
    if cursor is not None and cursor.last_error_code in READ_AUTH_ERRORS and _newer(
            cursor.last_poll_finished_at, cursor.last_poll_succeeded_at):
        return Health("gmail_read", name, FAILED, cursor.last_error, AUTHORIZE_READ,
                      cursor.last_poll_succeeded_at, cursor.last_poll_finished_at)
    if cursor is None or cursor.last_poll_succeeded_at is None:
        return Health("gmail_read", name, STALE, "Enabled; waiting for the first successful poll.",
                      "Make sure the worker is running.")
    return Health("gmail_read", name, HEALTHY, "Read access works.", "", cursor.last_poll_succeeded_at,
                  cursor.last_poll_finished_at)


def reply_polling(now: dt.datetime) -> Health:
    name = "Reply polling"
    facts = {
        "replies_needing_review": InboundMessage.objects.filter(
            state__in=[InboundMessage.State.TRIAGE, InboundMessage.State.RECEIVED]).count(),
        "invoices_on_hold": ChaseHold.objects.filter(released_at__isnull=True).values("invoice").distinct().count(),
        "poll_minutes": settings.INBOX_POLL_MINUTES,
    }
    if not settings.GMAIL_READ_ENABLED:
        return Health("reply_polling", name, AWAITING, "No mailbox is polled until read access is approved.",
                      AUTHORIZE_READ, facts=facts)
    cursor = _cursor()
    if cursor is None or cursor.last_poll_finished_at is None:
        return Health("reply_polling", name, STALE, "No poll has run yet.", "Make sure the worker is running.",
                      facts=facts)
    facts |= {"cursor_set": bool(cursor.history_id), "messages_ingested": cursor.messages_ingested,
              "last_rescan_at": cursor.last_rescan_at, "last_rescan_reason": cursor.last_rescan_reason}
    if cursor.last_error_code and _newer(cursor.last_poll_finished_at, cursor.last_poll_succeeded_at):
        return Health("reply_polling", name, FAILED, f"The last poll failed ({cursor.last_error_code}): {cursor.last_error}",
                      "It is retried on the next poll; check Gmail read access if it keeps failing.",
                      cursor.last_poll_succeeded_at, cursor.last_poll_finished_at, facts)
    allowed = dt.timedelta(minutes=settings.INBOX_POLL_MINUTES * 3)
    if cursor.last_poll_succeeded_at is None or now - cursor.last_poll_succeeded_at > allowed:
        return Health("reply_polling", name, STALE, "No successful poll recently.", "Make sure the worker is running.",
                      cursor.last_poll_succeeded_at, cursor.last_poll_finished_at, facts)
    return Health("reply_polling", name, HEALTHY, "Polling on schedule.", "", cursor.last_poll_succeeded_at,
                  cursor.last_poll_finished_at, facts)


def gemini(now: dt.datetime) -> Health:
    name = "Gemini"
    if not (settings.GEMINI_API_KEY and settings.GEMINI_MODEL):
        return Health("gemini", name, NOT_CONFIGURED, "No Gemini API key or model is configured.",
                      "Set GEMINI_API_KEY and GEMINI_MODEL.")
    budget = ai_budget.status(now)
    attempts = GenerationAttempt.objects.exclude(error_code="ai_not_configured")
    success = attempts.filter(outcome=GenerationAttempt.Outcome.SUCCEEDED).first()
    last = attempts.first()
    facts = {
        "model": settings.GEMINI_MODEL, "calls_24h": budget.used_24h, "drafting_budget_24h": budget.limit,
        "classifications_24h": attempts.filter(purpose=GenerationAttempt.Purpose.CLASSIFY,
                                               created_at__gte=now - dt.timedelta(hours=24)).count(),
        "cooldown_until": budget.cooldown_until if budget.cooldown_until and budget.cooldown_until > now else None,
        "last_error_code": last.error_code if last and last.outcome == GenerationAttempt.Outcome.FAILED else "",
    }
    success_at, attempt_at = (success.created_at if success else None), (last.created_at if last else None)
    if facts["cooldown_until"]:
        return Health("gemini", name, FAILED, "Gemini's quota is exhausted; AI calls are paused until the cooldown ends.",
                      "Wait for the cooldown, or use a key with more quota.", success_at, attempt_at, facts)
    if last is not None and last.error_code in ("ai_bad_response",) and "API key" in last.error_message:
        return Health("gemini", name, FAILED, "Gemini rejected the API key.", "Check GEMINI_API_KEY.",
                      success_at, attempt_at, facts)
    detail = "Available." if not budget.exhausted else "Available; today's automatic drafting budget is used up."
    return Health("gemini", name, HEALTHY, detail, "", success_at, attempt_at, facts)


def worker(now: dt.datetime) -> Health:
    last_seen = outbox.worker_last_seen()
    if last_seen is None:
        return Health("worker", "Background worker", NOT_CONFIGURED, "The worker has never run.",
                      "Start it: python manage.py run_worker (or the Compose worker service).")
    if not outbox.worker_alive():
        return Health("worker", "Background worker", FAILED, "The worker has stopped sending heartbeats.",
                      "Restart the worker.", last_seen, last_seen)
    return Health("worker", "Background worker", HEALTHY, "Running.", "", last_seen, last_seen)


SCHEDULED = [
    (SEQUENCE_TICK_KIND, "Sequence tick", lambda: settings.SEQUENCE_TICK_MINUTES, lambda: True),
    (INBOX_POLL_KIND, "Reply poll", lambda: settings.INBOX_POLL_MINUTES, lambda: settings.GMAIL_READ_ENABLED),
    (INBOX_PURGE_KIND, "Reply text purge", lambda: 24 * 60, lambda: True),
    (CASES_MONITOR_KIND, "Promise and task check", lambda: settings.CASES_MONITOR_MINUTES, lambda: True),
]


def scheduled_jobs(now: dt.datetime) -> list[dict[str, Any]]:
    rows = []
    for kind, label, minutes, enabled in SCHEDULED:
        jobs = OutboxJob.objects.filter(kind=kind)
        last = jobs.filter(status__in=[OutboxJob.Status.SUCCEEDED, OutboxJob.Status.FAILED]).order_by("-completed_at").first()
        if not enabled():
            state = AWAITING
        elif last is None:
            state = STALE
        elif last.status == OutboxJob.Status.FAILED:
            state = FAILED
        elif now - last.completed_at > dt.timedelta(minutes=minutes() * 3):  # type: ignore[operator]
            state = STALE
        else:
            state = HEALTHY
        rows.append({
            "kind": kind, "name": label, "status": state, "every_minutes": minutes(),
            "last_completed_at": last.completed_at if last else None,
            "last_status": last.status if last else None, "last_error_code": last.last_error_code if last else "",
            "failed_24h": jobs.filter(status=OutboxJob.Status.FAILED, completed_at__gte=now - dt.timedelta(hours=24)).count(),
        })
    return rows


def report(now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or timezone.now()
    checks = [sheets(now), gmail_send(), gmail_read(), reply_polling(now), gemini(now), worker(now)]
    return {"generated_at": now, "integrations": [asdict(check) for check in checks],
            "scheduled_jobs": scheduled_jobs(now)}

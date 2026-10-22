"""
Invoice synchronization service: Google Sheets -> local Invoice records.

Flow: record a RUNNING AutomationRun -> fetch values (no transaction open) -> validate
the whole batch -> apply all changes in one transaction, or none if any row is invalid.

Applying also maintains clients (one per lowercased email), marks invoices that left the sheet
(`in_sheet=False`) and writes every change to the timeline, so UNPAID -> PAID is a dated fact.

From the API, `start_sync` records the run and enqueues an outbox job in one transaction; the
worker then calls `execute_sync`. Commands call `sync_invoices`, which does both inline.
"""

import logging
from typing import Any

from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.automation import outbox
from apps.timeline import services as timeline
from apps.timeline.models import ActorType, EventType

from .models import AutomationRun, Client, Invoice
from .sheet_parser import InvoiceRecord, RowError, parse_sheet
from .sheets_client import GoogleSheetsClient, SheetsFetchError

logger = logging.getLogger(__name__)

SYNCED_FIELDS = ("client_name", "client_email", "amount", "currency", "due_date", "status")
MAX_SUMMARY_ERRORS = 10
JOB_KIND = "invoices.sync"


def default_actor(trigger: str) -> str:
    return ActorType.SCHEDULER if trigger == AutomationRun.Trigger.SCHEDULED else ActorType.SYSTEM


def sync_invoices(
    *,
    trigger: str = AutomationRun.Trigger.MANUAL,
    client: GoogleSheetsClient | None = None,
    actor: Any = None,
) -> AutomationRun:
    run = AutomationRun.objects.create(trigger=trigger, status=AutomationRun.Status.RUNNING)
    return execute_sync(run, client=client, actor=actor)


def start_sync(*, actor: Any) -> tuple[AutomationRun, Any]:
    """Records a RUNNING sync run and its job atomically; the worker performs the sync."""
    with transaction.atomic():
        run = AutomationRun.objects.create(trigger=AutomationRun.Trigger.MANUAL, status=AutomationRun.Status.RUNNING)
        job = outbox.enqueue(
            JOB_KIND, {"run_id": run.pk}, idempotency_key=f"{JOB_KIND}:{run.pk}", max_attempts=1, created_by=actor
        )
    return run, job


def handle_sync_job(job: Any) -> dict[str, Any]:
    run = AutomationRun.objects.get(pk=job.payload["run_id"])
    if run.status != AutomationRun.Status.RUNNING:
        return {"run_id": run.pk, "status": run.status}
    run = execute_sync(run, actor=job.created_by)
    if run.status == AutomationRun.Status.SUCCEEDED:
        # Reconcile sequence enrollments and advance due steps with the fresh data. Queued by kind
        # name only: invoices does not depend on the sequences app.
        outbox.enqueue(
            "sequences.tick", {"trigger": AutomationRun.Trigger.MANUAL},
            idempotency_key=f"sequences.tick:after-sync:{run.pk}", max_attempts=1,
        )
    return {"run_id": run.pk, "status": run.status}


def execute_sync(
    run: AutomationRun, *, client: GoogleSheetsClient | None = None, actor: Any = None
) -> AutomationRun:
    actor = actor or default_actor(run.trigger)
    try:
        sheet = (client or GoogleSheetsClient.from_settings()).fetch_values()
    except SheetsFetchError as exc:
        return _fail(run, str(exc))
    except Exception:
        logger.exception("Unexpected error while fetching Google Sheets data")
        return _fail(run, "Unexpected error while fetching spreadsheet data.")

    parsed = parse_sheet(sheet.values, start_row=sheet.start_row)
    run.records_processed = parsed.rows_processed

    if parsed.errors:
        run.rejected_count = parsed.rejected_rows
        return _fail(
            run,
            f"Sync rejected: {len(parsed.errors)} validation error(s) in "
            f"{max(parsed.rejected_rows, 1)} row(s). No invoices were changed.",
            parsed.errors,
        )

    try:
        with transaction.atomic():
            created, updated, unchanged = _apply(parsed.records, actor)
            run.created_count = created
            run.updated_count = updated
            run.unchanged_count = unchanged
            run.status = AutomationRun.Status.SUCCEEDED
            run.completed_at = timezone.now()
            run.save()
    except DatabaseError:
        logger.exception("Database error while applying invoice sync")
        run.refresh_from_db()
        run.records_processed = parsed.rows_processed
        return _fail(run, "Database error while saving invoices. No invoices were changed.")

    return run


def _apply(records: list[InvoiceRecord], actor: Any) -> tuple[int, int, int]:
    now = timezone.now()
    clients = _clients_for(records)
    existing = {
        invoice.external_id: invoice
        for invoice in Invoice.objects.select_for_update().select_related("client").filter(
            external_id__in=[record.external_id for record in records]
        )
    }

    events = []
    created = updated = 0
    unchanged_ids: list[str] = []

    for record in records:
        client = clients[record.client_email]
        invoice = existing.get(record.external_id)
        if invoice is None:
            invoice = Invoice.objects.create(
                external_id=record.external_id,
                client=client,
                last_synced_at=now,
                **{name: getattr(record, name) for name in SYNCED_FIELDS},
            )
            created += 1
            events.append(timeline.build(EventType.INVOICE_CREATED, actor=actor, invoice=invoice, **_facts(invoice)))
            continue

        changes = {
            name: {"from": _plain(getattr(invoice, name)), "to": _plain(getattr(record, name))}
            for name in SYNCED_FIELDS
            if getattr(invoice, name) != getattr(record, name)
        }
        returned = not invoice.in_sheet
        if changes or returned or invoice.client_id != client.pk:
            for name in SYNCED_FIELDS:
                setattr(invoice, name, getattr(record, name))
            invoice.client = client
            invoice.in_sheet = True
            invoice.last_synced_at = now
            invoice.save()
            if returned:
                events.append(timeline.build(EventType.INVOICE_RETURNED_TO_SHEET, actor=actor, invoice=invoice))
            if changes:
                updated += 1
                events.append(timeline.build(EventType.INVOICE_UPDATED, actor=actor, invoice=invoice, changes=changes))
            else:
                unchanged_ids.append(invoice.external_id)
        else:
            unchanged_ids.append(invoice.external_id)

    if unchanged_ids:
        Invoice.objects.filter(external_id__in=unchanged_ids).update(last_synced_at=now)

    # Rows that disappeared: kept for history, never chased again while missing.
    synced_ids = {record.external_id for record in records}
    removed = Invoice.objects.select_related("client").filter(in_sheet=True).exclude(external_id__in=synced_ids)
    for invoice in removed:
        events.append(timeline.build(EventType.INVOICE_REMOVED_FROM_SHEET, actor=actor, invoice=invoice))
    removed.update(in_sheet=False)

    timeline.bulk_record(events)
    return created, updated, len(unchanged_ids)


def _clients_for(records: list[InvoiceRecord]) -> dict[str, Client]:
    """One client per email; its display name follows the last row seen for that email."""
    names = {record.client_email: record.client_name for record in records}
    clients = {client.email: client for client in Client.objects.filter(email__in=names)}
    for email, name in names.items():
        client = clients.get(email)
        if client is None:
            clients[email] = Client.objects.create(email=email, display_name=name)
        elif client.display_name != name:
            client.display_name = name
            client.save(update_fields=["display_name", "updated_at"])
    return clients


def _plain(value: Any) -> Any:
    return value if isinstance(value, (str, int, bool)) or value is None else str(value)


def _facts(invoice: Invoice) -> dict[str, Any]:
    return {
        "external_id": invoice.external_id,
        "amount": _plain(invoice.amount),
        "currency": invoice.currency,
        "due_date": _plain(invoice.due_date),
        "status": invoice.status,
    }


def _fail(run: AutomationRun, summary: str, errors: list[RowError] | None = None) -> AutomationRun:
    errors = errors or []
    details = "\n".join(str(error) for error in errors[:MAX_SUMMARY_ERRORS])
    if len(errors) > MAX_SUMMARY_ERRORS:
        details += f"\n…and {len(errors) - MAX_SUMMARY_ERRORS} more."

    run.status = AutomationRun.Status.FAILED
    run.completed_at = timezone.now()
    run.error_summary = f"{summary}\n{details}" if details else summary
    run.errors = [error.to_dict() for error in errors]
    run.save()
    return run

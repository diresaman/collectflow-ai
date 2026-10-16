"""
Invoice synchronization service: Google Sheets -> local Invoice records.

Flow: record a RUNNING AutomationRun -> fetch values (no transaction open) -> validate
the whole batch -> apply all changes in one transaction, or none if any row is invalid.
"""

import logging

from django.db import DatabaseError, transaction
from django.utils import timezone

from .models import AutomationRun, Invoice
from .sheet_parser import InvoiceRecord, RowError, parse_sheet
from .sheets_client import GoogleSheetsClient, SheetsFetchError

logger = logging.getLogger(__name__)

SYNCED_FIELDS = ("client_name", "client_email", "amount", "currency", "due_date", "status")
MAX_SUMMARY_ERRORS = 10


def sync_invoices(
    *,
    trigger: str = AutomationRun.Trigger.MANUAL,
    client: GoogleSheetsClient | None = None,
) -> AutomationRun:
    run = AutomationRun.objects.create(trigger=trigger, status=AutomationRun.Status.RUNNING)

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
            created, updated, unchanged = _apply(parsed.records)
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


def _apply(records: list[InvoiceRecord]) -> tuple[int, int, int]:
    now = timezone.now()
    existing = {
        invoice.external_id: invoice
        for invoice in Invoice.objects.select_for_update().filter(
            external_id__in=[record.external_id for record in records]
        )
    }

    to_create: list[Invoice] = []
    to_update: list[Invoice] = []
    unchanged_ids: list[str] = []

    for record in records:
        invoice = existing.get(record.external_id)
        if invoice is None:
            to_create.append(
                Invoice(
                    external_id=record.external_id,
                    last_synced_at=now,
                    **{name: getattr(record, name) for name in SYNCED_FIELDS},
                )
            )
            continue

        changed = [name for name in SYNCED_FIELDS if getattr(invoice, name) != getattr(record, name)]
        if changed:
            for name in changed:
                setattr(invoice, name, getattr(record, name))
            invoice.last_synced_at = now
            to_update.append(invoice)
        else:
            unchanged_ids.append(invoice.external_id)

    Invoice.objects.bulk_create(to_create)
    for invoice in to_update:
        invoice.save()
    if unchanged_ids:
        Invoice.objects.filter(external_id__in=unchanged_ids).update(last_synced_at=now)

    return len(to_create), len(to_update), len(unchanged_ids)


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

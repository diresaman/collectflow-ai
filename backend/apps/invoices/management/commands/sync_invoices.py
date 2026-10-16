from django.core.management.base import BaseCommand, CommandError

from apps.invoices.models import AutomationRun
from apps.invoices.sync import sync_invoices


class Command(BaseCommand):
    help = "Synchronize invoices from the configured Google Sheet."

    def handle(self, *args: object, **options: object) -> None:
        run = sync_invoices(trigger=AutomationRun.Trigger.COMMAND)
        counts = (
            f"processed={run.records_processed} created={run.created_count} "
            f"updated={run.updated_count} unchanged={run.unchanged_count} "
            f"rejected={run.rejected_count}"
        )
        if run.status != AutomationRun.Status.SUCCEEDED:
            raise CommandError(f"Sync failed (run {run.pk}). {counts}\n{run.error_summary}")
        self.stdout.write(self.style.SUCCESS(f"Sync succeeded (run {run.pk}). {counts}"))

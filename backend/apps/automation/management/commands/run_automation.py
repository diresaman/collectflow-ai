"""
Scheduled entry point (e.g. cron) for proactive follow-up automation.

Exit codes: 0 completed or partial, 1 fatal failure, 2 another run is in progress.
Never sends email; output contains invoice IDs and reason codes only.
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from apps.automation.orchestrator import AutomationAlreadyRunning, AutomationOrchestrator
from apps.invoices.models import AutomationRun


class Command(BaseCommand):
    help = "Sync invoices and draft AI follow-ups for eligible overdue invoices (no email is sent)."

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be drafted from current data. No sync, no Gemini, no Gmail, no writes.",
        )

    def handle(self, *args: object, **options: object) -> None:
        orchestrator = AutomationOrchestrator()

        if options["dry_run"]:
            planned = orchestrator.plan()
            for item in planned:
                suffix = f" ({item.reason_code})" if item.reason_code else ""
                self.stdout.write(f"  {item.invoice_external_id}: {item.outcome}{suffix}")
            would = sum(1 for item in planned if item.outcome == "WOULD_GENERATE")
            self.stdout.write(
                self.style.SUCCESS(f"Dry run: {len(planned)} invoices evaluated, {would} would get a draft. Nothing was changed.")
            )
            return

        try:
            run = orchestrator.run(trigger=AutomationRun.Trigger.SCHEDULED)
        except AutomationAlreadyRunning as exc:
            raise CommandError(f"Another automation run is in progress (run {exc.run_id}).", returncode=2) from None

        sync = run.sync_run
        if sync is not None:
            self.stdout.write(
                f"Sync {sync.status}: processed={sync.records_processed} created={sync.created_count} "
                f"updated={sync.updated_count} unchanged={sync.unchanged_count} rejected={sync.rejected_count}"
            )
        reasons = Counter(item.reason_code for item in run.items.all() if item.reason_code)
        if reasons:
            self.stdout.write("Reasons: " + ", ".join(f"{code}={count}" for code, count in sorted(reasons.items())))
        summary = (
            f"Automation run {run.pk} {run.status}: evaluated={run.invoices_evaluated} "
            f"drafts_generated={run.drafts_generated} skipped={run.invoices_skipped} "
            f"failed={run.generation_failures}"
        )
        if run.status == AutomationRun.Status.FAILED:
            raise CommandError(f"{summary}\n{run.error_summary}")
        if run.status == AutomationRun.Status.PARTIAL:
            self.stdout.write(self.style.WARNING(f"{summary}\n{run.error_summary}"))
        else:
            self.stdout.write(self.style.SUCCESS(summary))
        if run.drafts_generated:
            self.stdout.write("Drafts generated — awaiting approval. No email was sent.")

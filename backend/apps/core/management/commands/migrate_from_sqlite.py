from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

from apps.core.sqlite_transfer import SOURCE_ALIAS_HELP, TransferError, transfer

SOURCE_ALIAS = "sqlite_source"


class Command(BaseCommand):
    help = (
        "Copy a CollectFlow SQLite snapshot into the (empty, migrated) PostgreSQL database, verify every row and "
        "apply the cutover policy, in one transaction. " + SOURCE_ALIAS_HELP
    )

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument("--dry-run", action="store_true", help="Copy and verify, then roll back.")
        parser.add_argument(
            "--report", help="Where to write the Markdown report (default: backups/transfer-report-<time>.md)."
        )

    def handle(self, *args, **options):  # type: ignore[no-untyped-def]
        if SOURCE_ALIAS not in connections.settings:
            raise CommandError("Set SQLITE_SOURCE_PATH to the SQLite BACKUP file to read (it is opened read-only).")
        try:
            report = transfer(source_alias=SOURCE_ALIAS, dry_run=options["dry_run"])
        except TransferError as exc:
            raise CommandError(str(exc)) from None

        path = Path(options["report"] or settings.BASE_DIR / "backups" / f"transfer-report-{timezone.now():%Y%m%dT%H%M%S}.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.to_markdown())

        rows = sum(source for source, _ in report.counts.values())
        verb = "Verified (dry run, rolled back)" if options["dry_run"] else "Transferred and verified"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {rows} rows in {len(report.counts)} models."))
        for action in report.cutover_actions:
            self.stdout.write(f"  cutover: {action}")
        for warning in report.warnings:
            self.stdout.write(self.style.WARNING(f"  warning: {warning}"))
        self.stdout.write(f"Report: {path}")

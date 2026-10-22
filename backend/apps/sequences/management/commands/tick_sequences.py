from django.core.management.base import BaseCommand, CommandError

from apps.automation.lease import Lease, LeaseUnavailable
from apps.sequences import engine
from apps.timeline.models import ActorType


class Command(BaseCommand):
    help = "Reconcile sequence enrollments and advance each due one by at most one step. Never approves or sends."

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument("--dry-run", action="store_true", help="Show what would happen; no writes, no Gemini.")

    def handle(self, *args, **options):  # type: ignore[no-untyped-def]
        if options["dry_run"]:
            for invoice, outcome, reason in engine.preview():
                self.stdout.write(f"  {invoice.external_id}: {outcome} ({reason})")
            return
        try:
            lease = Lease.acquire()
        except LeaseUnavailable as exc:
            raise CommandError(f"An automation run or tick is in progress (run {exc.run_id}).", returncode=2) from None
        try:
            results = engine.run_tick(actor=ActorType.SYSTEM, heartbeat=lease.heartbeat)
        finally:
            lease.release()
        for result in results:
            self.stdout.write(f"  {result.invoice.external_id}: {result.outcome} ({result.reason or result.detail})")
        self.stdout.write(self.style.SUCCESS(f"Tick done: {len(results)} enrollment(s) evaluated. No email was sent."))

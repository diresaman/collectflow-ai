import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.automation import outbox
from apps.core import e2e


class Command(BaseCommand):
    help = "Browser tests only: runs queued jobs with fake Gemini and Gmail; schedules nothing (refuses any other database)."

    def handle(self, *args, **options):  # type: ignore[no-untyped-def]
        e2e.install_fakes()
        worker_id, started = outbox.new_worker_id(), timezone.now()
        self.stdout.write(f"e2e worker {worker_id} started")
        while True:
            outbox.beat(worker_id, started)
            outbox.run_pending(worker_id)
            time.sleep(0.5)

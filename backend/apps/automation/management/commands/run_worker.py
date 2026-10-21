import signal
import threading
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection
from django.utils import timezone

from apps.automation import outbox

HEARTBEAT_SECONDS = 10
RECOVERY_SECONDS = 60


class Command(BaseCommand):
    help = (
        "Run the background worker: claims outbox jobs (sync, AI drafts, Gmail delivery, automation runs) "
        "one at a time. Stop with Ctrl+C or SIGTERM; the current job finishes first."
    )

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument("--once", action="store_true", help="Run every due job, then exit.")

    def handle(self, *args, **options):  # type: ignore[no-untyped-def]
        worker_id = outbox.new_worker_id()
        started_at = timezone.now()
        outbox.recover_stale()

        if options["once"]:
            outbox.beat(worker_id, started_at)
            count = outbox.run_pending(worker_id)
            self.stdout.write(f"Ran {count} job(s).")
            return

        stopping = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stopping.set())

        # Heartbeat from its own thread so long jobs (an automation run) don't look like a dead worker.
        def heartbeat() -> None:
            while not stopping.is_set():
                try:
                    outbox.beat(worker_id, started_at)
                except Exception:  # noqa: BLE001 - a missed beat must never stop the worker
                    pass
                stopping.wait(HEARTBEAT_SECONDS)
            connection.close()

        threading.Thread(target=heartbeat, name="worker-heartbeat", daemon=True).start()
        self._say(f"Worker {worker_id} started.")

        last_recovery = time.monotonic()
        while not stopping.is_set():
            close_old_connections()
            if time.monotonic() - last_recovery >= RECOVERY_SECONDS:
                outbox.recover_stale()
                last_recovery = time.monotonic()
            job = outbox.claim_next(worker_id)
            if job is None:
                stopping.wait(settings.WORKER_POLL_SECONDS)
                continue
            self._say(f"{timezone.now():%H:%M:%S} {job.kind} {job.pk} ...")
            job = outbox.execute(job, worker_id)
            suffix = f" ({job.last_error_code})" if job.last_error_code else ""
            self._say(f"{timezone.now():%H:%M:%S} {job.kind} {job.pk} {job.status}{suffix}")

        self._say("Worker stopped.")

    def _say(self, message: str) -> None:
        # Flush every line: the worker usually runs with output redirected to a log file.
        self.stdout.write(message)
        self.stdout.flush()

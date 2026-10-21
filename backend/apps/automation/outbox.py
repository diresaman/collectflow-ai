"""
Transactional outbox: enqueue background work atomically with a state change, run it in a worker.

Apps register a handler per job kind in AppConfig.ready(), so this module never imports the
code that performs the work (in particular, it has no path to Gmail delivery of its own).

Claiming and finishing are conditional UPDATEs, so on SQLite two workers can never run the same
job, and a worker that lost a job (it was recovered as abandoned) cannot overwrite its outcome.
"""

import datetime as dt
import logging
import os
import uuid
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .models import OutboxJob, WorkerHeartbeat

logger = logging.getLogger(__name__)

Handler = Callable[[OutboxJob], dict[str, Any] | None]
_handlers: dict[str, Handler] = {}

HEARTBEAT_NAME = "worker"
ACTIVE_STATUSES = (OutboxJob.Status.PENDING, OutboxJob.Status.RUNNING)


class JobFailed(Exception):
    """A permanent, expected failure: the job ends FAILED with this code and result, and is not retried."""

    def __init__(self, code: str, result: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.result = result or {}


def register(kind: str, handler: Handler) -> None:
    _handlers[kind] = handler


def enqueue(
    kind: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str | None = None,
    max_attempts: int | None = None,
    created_by: Any = None,
) -> OutboxJob:
    """
    Records a job. Call it inside the transaction that makes the change the job depends on.
    Enqueueing an existing idempotency key returns the existing job instead of a duplicate.
    """
    key = idempotency_key or f"{kind}:{uuid.uuid4()}"
    existing = OutboxJob.objects.filter(idempotency_key=key).first()
    if existing is not None:
        return existing
    return OutboxJob.objects.create(
        kind=kind,
        payload=payload,
        idempotency_key=key,
        max_attempts=max_attempts or settings.JOB_MAX_ATTEMPTS,
        created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
    )


def has_active(kind: str, **payload: Any) -> bool:
    """True if a PENDING or RUNNING job of this kind matches the given payload values."""
    filters = {f"payload__{name}": value for name, value in payload.items()}
    return OutboxJob.objects.filter(kind=kind, status__in=ACTIVE_STATUSES, **filters).exists()


# Worker side ------------------------------------------------------------------


def new_worker_id() -> str:
    return f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


def claim_next(worker_id: str) -> OutboxJob | None:
    now = timezone.now()
    candidates = OutboxJob.objects.filter(status=OutboxJob.Status.PENDING, run_after__lte=now).order_by(
        "run_after", "created_at"
    )
    for job_id in candidates.values_list("pk", flat=True)[:10]:
        claimed = OutboxJob.objects.filter(pk=job_id, status=OutboxJob.Status.PENDING).update(
            status=OutboxJob.Status.RUNNING, locked_by=worker_id, locked_at=now, attempts=F("attempts") + 1
        )
        if claimed == 1:
            return OutboxJob.objects.get(pk=job_id)
    return None


def execute(job: OutboxJob, worker_id: str) -> OutboxJob:
    handler = _handlers.get(job.kind)
    if handler is None:
        logger.error("No handler registered for job kind %s", job.kind)
        return _finish(job, worker_id, OutboxJob.Status.FAILED, error_code="unknown_job_kind")

    try:
        result = handler(job) or {}
    except JobFailed as exc:
        logger.info("Job %s (%s) failed: %s", job.pk, job.kind, exc.code)
        return _finish(job, worker_id, OutboxJob.Status.FAILED, result=exc.result, error_code=exc.code)
    except Exception as exc:
        logger.exception("Job %s (%s) raised unexpectedly", job.pk, job.kind)
        error_code = type(exc).__name__[:50]
        if job.attempts < job.max_attempts:
            delay = settings.JOB_RETRY_BASE_SECONDS * 2 ** (job.attempts - 1)
            return _finish(
                job, worker_id, OutboxJob.Status.PENDING, error_code=error_code,
                run_after=timezone.now() + dt.timedelta(seconds=delay),
            )
        return _finish(job, worker_id, OutboxJob.Status.FAILED, error_code=error_code)
    return _finish(job, worker_id, OutboxJob.Status.SUCCEEDED, result=result)


def _finish(job: OutboxJob, worker_id: str, status: str, *, result: dict[str, Any] | None = None,
            error_code: str = "", run_after: dt.datetime | None = None) -> OutboxJob:
    fields: dict[str, Any] = {"status": status, "last_error_code": error_code, "locked_by": "", "locked_at": None}
    if result is not None:
        fields["result"] = result
    if run_after is not None:
        fields["run_after"] = run_after
    if status in (OutboxJob.Status.SUCCEEDED, OutboxJob.Status.FAILED):
        fields["completed_at"] = timezone.now()
    updated = OutboxJob.objects.filter(pk=job.pk, status=OutboxJob.Status.RUNNING, locked_by=worker_id).update(**fields)
    if updated != 1:
        logger.error("Job %s was no longer held by worker %s when finishing as %s", job.pk, worker_id, status)
    job.refresh_from_db()
    return job


def recover_stale() -> int:
    """
    Jobs left RUNNING by a worker that died. Retryable ones go back to PENDING; the rest (including
    every delivery job, max_attempts=1) fail as abandoned and are never re-run automatically.
    """
    cutoff = timezone.now() - dt.timedelta(seconds=settings.JOB_LOCK_TIMEOUT_SECONDS)
    stale = OutboxJob.objects.filter(status=OutboxJob.Status.RUNNING, locked_at__lt=cutoff)
    retried = stale.filter(attempts__lt=F("max_attempts")).update(
        status=OutboxJob.Status.PENDING, locked_by="", locked_at=None, last_error_code="abandoned"
    )
    failed = stale.update(
        status=OutboxJob.Status.FAILED, locked_by="", locked_at=None, last_error_code="abandoned",
        completed_at=timezone.now(),
    )
    if retried or failed:
        logger.warning("Recovered abandoned jobs: %s re-queued, %s failed", retried, failed)
    return retried + failed


def run_pending(worker_id: str | None = None, *, limit: int | None = None) -> int:
    """Runs due jobs until none are left (or `limit` is reached). Used by `run_worker --once` and tests."""
    worker_id = worker_id or new_worker_id()
    count = 0
    while limit is None or count < limit:
        job = claim_next(worker_id)
        if job is None:
            break
        execute(job, worker_id)
        count += 1
    return count


def beat(worker_id: str, started_at: dt.datetime) -> None:
    WorkerHeartbeat.objects.update_or_create(
        name=HEARTBEAT_NAME,
        defaults={"worker_id": worker_id, "pid": os.getpid(), "started_at": started_at, "last_seen_at": timezone.now()},
    )


def worker_last_seen() -> dt.datetime | None:
    return WorkerHeartbeat.objects.filter(name=HEARTBEAT_NAME).values_list("last_seen_at", flat=True).first()


def worker_alive() -> bool:
    last_seen = worker_last_seen()
    allowed = dt.timedelta(seconds=max(30, settings.WORKER_POLL_SECONDS * 10))
    return last_seen is not None and timezone.now() - last_seen <= allowed

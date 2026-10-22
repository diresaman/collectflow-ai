"""
Single-owner lease for automation runs.

Every state change is a conditional UPDATE. Under PostgreSQL's READ COMMITTED isolation a
concurrent UPDATE waits for the first one to commit and then re-checks its WHERE clause, so two
processes can never both acquire the lease, and an owner that lost it cannot renew it.
"""

import datetime as dt
import uuid

from django.conf import settings
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from apps.invoices.models import AutomationRun

from .models import AutomationLease, WorkflowStep

LEASE_NAME = "automation"
ABANDONED_SUMMARY = "Abandoned: the run stopped renewing its lease (process interrupted)."


def _fail_runs_started_before(moment: dt.datetime) -> None:
    """Marks RUNNING automation runs (and their unfinished steps) as abandoned."""
    run_ids = list(
        AutomationRun.objects.filter(
            kind=AutomationRun.Kind.AUTOMATION, status=AutomationRun.Status.RUNNING, started_at__lt=moment
        ).values_list("pk", flat=True)
    )
    if not run_ids:
        return
    AutomationRun.objects.filter(pk__in=run_ids, status=AutomationRun.Status.RUNNING).update(
        status=AutomationRun.Status.FAILED, completed_at=moment, error_summary=ABANDONED_SUMMARY
    )
    steps = WorkflowStep.objects.filter(run_id__in=run_ids)
    steps.filter(status=WorkflowStep.Status.RUNNING).update(status=WorkflowStep.Status.FAILED, completed_at=moment)
    steps.filter(status=WorkflowStep.Status.PENDING).update(status=WorkflowStep.Status.SKIPPED)


def fail_abandoned_runs() -> None:
    """
    Read-side recovery: if no live lease exists, no run can be making progress, so any run still
    RUNNING was interrupted (e.g. the server was stopped mid-run). Without this the workflow would
    show "processing" until the next run took over the lease.
    """
    now = timezone.now()
    lease = AutomationLease.objects.filter(name=LEASE_NAME).first()
    if lease is not None and lease.owner and lease.expires_at is not None and lease.expires_at >= now:
        return
    # Only runs that started before the lease was seen free; a run starting now holds a new lease.
    _fail_runs_started_before(now)


class LeaseUnavailable(Exception):
    def __init__(self, run_id: int | None, expires_at: dt.datetime | None) -> None:
        super().__init__("Another automation run holds the lease.")
        self.run_id = run_id
        self.expires_at = expires_at


class Lease:
    def __init__(self, owner: str) -> None:
        self.owner = owner

    @staticmethod
    def _duration() -> dt.timedelta:
        return dt.timedelta(seconds=settings.AUTOMATION_LEASE_SECONDS)

    @classmethod
    def acquire(cls) -> "Lease":
        try:
            AutomationLease.objects.get_or_create(name=LEASE_NAME)
        except IntegrityError:
            pass  # created concurrently by another process

        owner = uuid.uuid4().hex
        now = timezone.now()
        acquired = AutomationLease.objects.filter(
            Q(owner="") | Q(expires_at__lt=now), name=LEASE_NAME
        ).update(owner=owner, run=None, acquired_at=now, heartbeat_at=now, expires_at=now + cls._duration())
        if acquired != 1:
            current = AutomationLease.objects.get(name=LEASE_NAME)
            raise LeaseUnavailable(current.run_id, current.expires_at)

        # We hold the lease and have not created our run yet, so any RUNNING run stopped making
        # progress (e.g. the process died), whether its lease expired or was released.
        _fail_runs_started_before(now)
        return cls(owner)

    def attach_run(self, run: AutomationRun) -> None:
        AutomationLease.objects.filter(name=LEASE_NAME, owner=self.owner).update(run=run)

    def heartbeat(self) -> bool:
        """Extends the lease. False means it was lost and the caller must stop."""
        now = timezone.now()
        return (
            AutomationLease.objects.filter(name=LEASE_NAME, owner=self.owner).update(
                heartbeat_at=now, expires_at=now + self._duration()
            )
            == 1
        )

    def release(self) -> None:
        AutomationLease.objects.filter(name=LEASE_NAME, owner=self.owner).update(owner="", expires_at=None)

"""
Single-owner lease for automation runs, safe on SQLite.

Every state change is a conditional UPDATE, which the database applies atomically, so two
processes can never both acquire the lease, and an owner that lost it cannot renew it.
"""

import datetime as dt
import uuid

from django.conf import settings
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from apps.invoices.models import AutomationRun

from .models import AutomationLease

LEASE_NAME = "automation"


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

        previous = AutomationLease.objects.get(name=LEASE_NAME)
        owner = uuid.uuid4().hex
        now = timezone.now()
        acquired = AutomationLease.objects.filter(
            Q(owner="") | Q(expires_at__lt=now), name=LEASE_NAME
        ).update(owner=owner, run=None, acquired_at=now, heartbeat_at=now, expires_at=now + cls._duration())
        if acquired != 1:
            current = AutomationLease.objects.get(name=LEASE_NAME)
            raise LeaseUnavailable(current.run_id, current.expires_at)

        # Took over an expired lease: its run stopped making progress (e.g. the process died).
        if previous.owner and previous.run_id:
            AutomationRun.objects.filter(pk=previous.run_id, status=AutomationRun.Status.RUNNING).update(
                status=AutomationRun.Status.FAILED,
                completed_at=now,
                error_summary="Abandoned: the run stopped renewing its lease (process interrupted).",
            )
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

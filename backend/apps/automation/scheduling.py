"""
Periodic jobs queued by the worker itself (no separate scheduler process).

Each 15-minute slot is one `sequences.tick` job whose idempotency key is the slot's start time, so
restarts and parallel workers never queue a slot twice. A slot missed while no worker ran is not
back-filled: the next slot catches up, and a tick advances each enrollment by at most one step.
The tick runs under the automation lease, so it never overlaps an automation run or another tick.
"""

import datetime as dt

from django.conf import settings
from django.utils import timezone

from apps.invoices.models import AutomationRun

from . import outbox

SEQUENCE_TICK_KIND = "sequences.tick"


def current_slot(now: dt.datetime) -> dt.datetime:
    minutes = settings.SEQUENCE_TICK_MINUTES
    floored = now.astimezone(dt.timezone.utc).replace(second=0, microsecond=0)
    return floored - dt.timedelta(minutes=floored.minute % minutes)


def schedule_periodic_jobs(now: dt.datetime | None = None) -> None:
    slot = current_slot(now or timezone.now())
    outbox.enqueue(
        SEQUENCE_TICK_KIND, {"trigger": AutomationRun.Trigger.SCHEDULED, "slot": slot.isoformat()},
        idempotency_key=f"{SEQUENCE_TICK_KIND}:{slot.isoformat()}", max_attempts=1,
    )

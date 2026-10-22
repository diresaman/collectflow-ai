"""
Periodic jobs queued by the worker itself (no separate scheduler process).

Each 15-minute slot is one `sequences.tick` job whose idempotency key is the slot's start time, so
restarts and parallel workers never queue a slot twice. Reply polling (M10) works the same way in
5-minute slots, but only once GMAIL_READ_ENABLED is on; a daily job purges old reply text. A slot missed while no worker ran is not
back-filled: the next slot catches up, and a tick advances each enrollment by at most one step.
The tick runs under the automation lease, so it never overlaps an automation run or another tick.
"""

import datetime as dt

from django.conf import settings
from django.utils import timezone

from apps.invoices.models import AutomationRun

from . import outbox

SEQUENCE_TICK_KIND = "sequences.tick"
INBOX_POLL_KIND = "inbox.poll"
INBOX_PURGE_KIND = "inbox.purge"


def current_slot(now: dt.datetime, minutes: int | None = None) -> dt.datetime:
    minutes = minutes or settings.SEQUENCE_TICK_MINUTES
    floored = now.astimezone(dt.timezone.utc).replace(second=0, microsecond=0)
    return floored - dt.timedelta(minutes=floored.minute % minutes)


def schedule_periodic_jobs(now: dt.datetime | None = None) -> None:
    now = now or timezone.now()
    slot = current_slot(now)
    outbox.enqueue(
        SEQUENCE_TICK_KIND, {"trigger": AutomationRun.Trigger.SCHEDULED, "slot": slot.isoformat()},
        idempotency_key=f"{SEQUENCE_TICK_KIND}:{slot.isoformat()}", max_attempts=1,
    )
    if settings.GMAIL_READ_ENABLED:
        poll_slot = current_slot(now, settings.INBOX_POLL_MINUTES)
        outbox.enqueue(INBOX_POLL_KIND, {"slot": poll_slot.isoformat()},
                       idempotency_key=f"{INBOX_POLL_KIND}:{poll_slot.isoformat()}", max_attempts=1)
    day = now.astimezone(dt.timezone.utc).date().isoformat()
    outbox.enqueue(INBOX_PURGE_KIND, {"day": day}, idempotency_key=f"{INBOX_PURGE_KIND}:{day}", max_attempts=1)

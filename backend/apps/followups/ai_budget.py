"""
Gemini spending guard for *automatic* drafting (sequence ticks and automation runs).

Two checks, both read from GenerationAttempt rows (every call that reached Gemini records one,
including reply classifications since M10, which share the quota):

- Daily budget: at most AI_DAILY_GENERATION_BUDGET attempts in the last 24 hours, counting manual
  drafts too, so scheduled work leaves room for people within the free tier (~20/day).
- Quota cooldown: after HTTP 429 (ai_quota_exhausted) all automatic drafting pauses, backing off
  exponentially with each consecutive quota failure (AI_QUOTA_BACKOFF_MINUTES, doubling, capped at
  AI_RETRY_MAX_HOURS). A successful generation ends the cooldown.

People can still draft manually at any time; their attempts count toward the budget. Reply
classification is not blocked by the daily budget (it is safety work) but does wait out a cooldown.
"""

import datetime as dt
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from .models import GenerationAttempt

QUOTA_CODE = "ai_quota_exhausted"


@dataclass(frozen=True)
class BudgetStatus:
    used_24h: int
    limit: int
    cooldown_until: dt.datetime | None

    @property
    def exhausted(self) -> bool:
        return self.used_24h >= self.limit

    def blocked_reason(self, now: dt.datetime) -> str:
        """'' if automatic drafting may call Gemini now, else ai_quota_cooldown / ai_budget_exhausted."""
        if self.cooldown_until is not None and now < self.cooldown_until:
            return "ai_quota_cooldown"
        if self.exhausted:
            return "ai_budget_exhausted"
        return ""


def backoff(failures: int, *, base_minutes: int) -> dt.timedelta:
    """base, 2×base, 4×base … capped at AI_RETRY_MAX_HOURS (failures counts from 1)."""
    minutes = base_minutes * 2 ** max(failures - 1, 0)
    return min(dt.timedelta(minutes=minutes), dt.timedelta(hours=settings.AI_RETRY_MAX_HOURS))


def status(now: dt.datetime | None = None) -> BudgetStatus:
    now = now or timezone.now()
    # Attempts that reached Gemini (a missing configuration never calls it).
    used = (
        GenerationAttempt.objects.filter(created_at__gte=now - dt.timedelta(hours=24))
        .exclude(error_code="ai_not_configured").count()
    )

    cooldown_until = None
    last_success = (
        GenerationAttempt.objects.filter(outcome=GenerationAttempt.Outcome.SUCCEEDED)
        .order_by("-created_at").values_list("created_at", flat=True).first()
    )
    quota_failures = GenerationAttempt.objects.filter(error_code=QUOTA_CODE)
    if last_success is not None:
        quota_failures = quota_failures.filter(created_at__gt=last_success)
    consecutive = quota_failures.count()
    if consecutive:
        latest = quota_failures.order_by("-created_at").values_list("created_at", flat=True).first()
        cooldown_until = latest + backoff(consecutive, base_minutes=settings.AI_QUOTA_BACKOFF_MINUTES)  # type: ignore[operator]
    return BudgetStatus(used_24h=used, limit=settings.AI_DAILY_GENERATION_BUDGET, cooldown_until=cooldown_until)

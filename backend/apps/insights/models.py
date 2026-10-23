from django.db import models
from django.utils import timezone


class AnalyticsSnapshot(models.Model):
    """
    The latest computed analytics of one kind (M14): aging, DSO and CEI, attribution, forecast, KPIs.
    Written only by `apps.insights.analytics.refresh` (worker job), replaced in place; the API reads it,
    so a page view never recomputes. `computed_at` is the calculation time shown with every figure.
    """

    kind = models.CharField(max_length=20, unique=True)
    data = models.JSONField(default=dict)
    computed_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"{self.kind} at {self.computed_at:%Y-%m-%d %H:%M}"

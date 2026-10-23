from django.apps import AppConfig


class InsightsConfig(AppConfig):
    """Client risk scores, metrics and the prioritized worklist (M13). No AI; never chases."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.insights"

    def ready(self) -> None:
        from apps.automation import outbox
        from apps.timeline import services as timeline

        from .jobs import RECOMPUTE_JOB_KIND, handle_recompute_job, on_timeline_events

        outbox.register(RECOMPUTE_JOB_KIND, handle_recompute_job)
        timeline.subscribe(on_timeline_events)

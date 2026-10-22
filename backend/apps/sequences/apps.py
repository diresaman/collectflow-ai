from django.apps import AppConfig


class SequencesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sequences"

    def ready(self) -> None:
        from apps.automation import outbox
        from apps.followups import services as followups

        from . import engine

        outbox.register(engine.TICK_JOB_KIND, engine.handle_tick_job)
        # Manual "Analyze" and regenerate draft the invoice's sequence step (see engine.plan_manual_draft).
        followups.register_draft_planner(engine.plan_manual_draft)

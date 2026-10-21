from django.apps import AppConfig


class AutomationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.automation"

    def ready(self) -> None:
        from . import outbox
        from .orchestrator import RUN_JOB_KIND, handle_run_job

        outbox.register(RUN_JOB_KIND, handle_run_job)

from django.apps import AppConfig


class CasesConfig(AppConfig):
    """Promises to pay, disputes and their monitoring (M11)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cases"

    def ready(self) -> None:
        from apps.automation import outbox

        from .monitoring import MONITOR_JOB_KIND, handle_monitor_job

        outbox.register(MONITOR_JOB_KIND, handle_monitor_job)

from django.apps import AppConfig


class InvoicesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.invoices"

    def ready(self) -> None:
        from apps.automation import outbox

        from .sync import JOB_KIND, handle_sync_job

        outbox.register(JOB_KIND, handle_sync_job)

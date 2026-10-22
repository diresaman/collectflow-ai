from django.apps import AppConfig


class InboxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inbox"

    def ready(self) -> None:
        from apps.automation import outbox

        from .classification import handle_classify_job
        from .ingestion import CLASSIFY_JOB_KIND, POLL_JOB_KIND, handle_poll_job, handle_purge_job

        outbox.register(POLL_JOB_KIND, handle_poll_job)
        outbox.register(CLASSIFY_JOB_KIND, handle_classify_job)
        outbox.register("inbox.purge", handle_purge_job)

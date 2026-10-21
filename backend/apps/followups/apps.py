from django.apps import AppConfig


class FollowupsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.followups"

    def ready(self) -> None:
        # The delivery handler is registered here, by the app that owns Gmail send; the outbox
        # itself never imports delivery code.
        from apps.automation import outbox

        from .delivery import DELIVER_JOB_KIND, handle_deliver_job
        from .services import GENERATE_JOB_KIND, handle_generate_job

        outbox.register(GENERATE_JOB_KIND, handle_generate_job)
        outbox.register(DELIVER_JOB_KIND, handle_deliver_job)

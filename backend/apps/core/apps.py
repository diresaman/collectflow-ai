from typing import Any

from django.apps import AppConfig

PING_JOB_KIND = "core.ping"


def handle_ping(job: Any) -> dict[str, Any]:
    """No-op job for checking the queue and worker end to end without calling any provider."""
    return {"pong": True}


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"

    def ready(self) -> None:
        from apps.automation import outbox

        outbox.register(PING_JOB_KIND, handle_ping)

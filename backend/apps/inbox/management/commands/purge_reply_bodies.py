from django.conf import settings
from django.core.management.base import BaseCommand

from apps.inbox.ingestion import purge_bodies


class Command(BaseCommand):
    help = "Clear the text of customer replies older than INBOX_BODY_RETENTION_DAYS (labels and audit trail stay)."

    def handle(self, *args: object, **options: object) -> None:
        count = purge_bodies()
        self.stdout.write(f"Purged the text of {count} repl{'y' if count == 1 else 'ies'} older than "
                          f"{settings.INBOX_BODY_RETENTION_DAYS} days.")

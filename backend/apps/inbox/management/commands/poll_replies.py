from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.automation import outbox
from apps.inbox.ingestion import poll
from integrations.mailbox import MailboxReader


class Command(BaseCommand):
    help = (
        "Poll the Gmail mailbox once for replies in CollectFlow conversations. Reads mail: needs the owner's "
        "approval of read access (GMAIL_READ_ENABLED=true and authorize_gmail --with-read-access)."
    )

    def handle(self, *args: object, **options: object) -> None:
        if not settings.GMAIL_READ_ENABLED:
            raise CommandError("GMAIL_READ_ENABLED is off: reading replies has not been enabled.")
        result = poll(MailboxReader.from_settings(), owner=f"command-{outbox.new_worker_id()}")
        self.stdout.write(str(result))

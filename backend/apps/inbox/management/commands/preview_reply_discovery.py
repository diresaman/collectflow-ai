import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from apps.inbox.preview import preview
from integrations.mailbox import MailboxError, MailboxReader


class Command(BaseCommand):
    help = (
        "Read-only preview of the first reply discovery: replies the first poll would store from CollectFlow "
        "threads of the last INBOX_RESCAN_DAYS, the invoices affected and the holds it would place. Reads Gmail "
        "headers only (no message bodies) and changes nothing. Needs the gmail.readonly grant; works while "
        "GMAIL_READ_ENABLED is off."
    )

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument("--days", type=int, default=settings.INBOX_RESCAN_DAYS, help="Look-back window in days.")
        parser.add_argument("--json", action="store_true", help="Print the report as JSON.")

    def handle(self, *args: object, **options: object) -> None:
        reader = MailboxReader.from_settings(require_enabled=False)
        try:
            report = preview(reader, days=int(options["days"]))  # type: ignore[arg-type]
        except MailboxError as exc:
            raise CommandError(f"{exc.code}: {exc}") from None
        data = report.as_dict()
        if options["json"]:
            self.stdout.write(json.dumps(data, cls=DjangoJSONEncoder, indent=2))
            return
        self.stdout.write(f"Preview only: nothing was stored or changed. Window: last {data['window_days']} days.")
        self.stdout.write(f"CollectFlow threads scanned: {data['threads_scanned']}; messages in them: {data['messages_seen']}.")
        self.stdout.write(f"Replies the first poll would store: {data['would_store']}.")
        self.stdout.write(f"Skipped: {data['skipped'] or 'none'}.")
        self.stdout.write(f"Invoices that would be put on hold: {data['invoices_affected']}.")
        for item in data["invoices"]:
            self.stdout.write(
                f"  {item['invoice']}: {item['replies']} repl{'y' if item['replies'] == 1 else 'ies'} "
                f"{item['sender_checks']}, latest {item['last_received']:%Y-%m-%d}; sequence {item['enrollment']}; "
                f"{item['effect']}"
            )

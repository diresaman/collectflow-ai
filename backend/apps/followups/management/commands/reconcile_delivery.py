"""
Manually resolve a follow-up whose delivery outcome is SENDING or DELIVERY_UNKNOWN.

Check the sender's Gmail "Sent" folder first:
  - message present  -> --sent [--message-id <id>]
  - message absent   -> --failed
"""

import uuid

from django.core.management.base import BaseCommand, CommandError

from apps.followups.delivery import reconcile
from apps.followups.models import FollowUp
from apps.followups.services import FollowUpError


class Command(BaseCommand):
    help = "Resolve an uncertain Gmail delivery after checking the Sent folder."

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument("follow_up_id", nargs="?", help="Follow-up UUID. Omit to list unresolved deliveries.")
        outcome = parser.add_mutually_exclusive_group()
        outcome.add_argument("--sent", action="store_true", help="The email is in the Sent folder.")
        outcome.add_argument("--failed", action="store_true", help="The email is NOT in the Sent folder.")
        parser.add_argument("--message-id", default="", help="Gmail message ID, if known.")
        parser.add_argument("--note", default="", help="Short note stored on the delivery attempt.")

    def handle(self, *args: object, **options: object) -> None:
        unresolved = FollowUp.objects.filter(
            status__in=[FollowUp.Status.SENDING, FollowUp.Status.DELIVERY_UNKNOWN]
        ).select_related("invoice")

        if not options["follow_up_id"]:
            if not unresolved:
                self.stdout.write("No unresolved deliveries.")
            for follow_up in unresolved:
                self.stdout.write(
                    f"{follow_up.pk}  {follow_up.status:<16}  {follow_up.invoice.external_id}  "
                    f"to {follow_up.invoice_snapshot.get('client_email')}  subject: {follow_up.subject!r}"
                )
            return

        if not (options["sent"] or options["failed"]):
            raise CommandError("Choose --sent or --failed after checking the Gmail Sent folder.")
        try:
            follow_up_id = uuid.UUID(str(options["follow_up_id"]))
            follow_up = reconcile(
                follow_up_id,
                outcome=FollowUp.Status.SENT if options["sent"] else FollowUp.Status.FAILED,
                message_id=str(options["message_id"]),
                note=str(options["note"]),
            )
        except ValueError:
            raise CommandError("Invalid follow-up ID.") from None
        except FollowUp.DoesNotExist:
            raise CommandError("Follow-up not found.") from None
        except FollowUpError as exc:
            raise CommandError(exc.message) from None
        self.stdout.write(self.style.SUCCESS(f"Follow-up {follow_up.pk} reconciled as {follow_up.status}."))

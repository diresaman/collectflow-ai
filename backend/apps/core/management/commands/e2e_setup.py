from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.core import e2e


class Command(BaseCommand):
    help = "Browser tests only: migrate and seed the empty *_e2e database (refuses any other database)."

    def handle(self, *args, **options):  # type: ignore[no-untyped-def]
        e2e.require_e2e_database()
        call_command("migrate", verbosity=0)
        self.stdout.write(f"Seeded: {e2e.seed()}")

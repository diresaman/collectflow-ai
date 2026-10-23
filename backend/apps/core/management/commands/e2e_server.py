from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.core import e2e


class Command(BaseCommand):
    help = "Browser tests only: the development server with fake Gemini and Gmail (refuses any other database)."

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument("addrport", nargs="?", default="127.0.0.1:8002")

    def handle(self, *args, **options):  # type: ignore[no-untyped-def]
        e2e.install_fakes()
        call_command("runserver", options["addrport"], use_reloader=False)

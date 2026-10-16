"""
One-time local OAuth authorization for the Gmail sender account.

Opens a browser for consent, then stores the user token at GMAIL_TOKEN_FILE (mode 0600).
Run it on the machine that runs the backend, signed in as GMAIL_SENDER_EMAIL.
"""

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.gmail import SCOPES, GmailClient, save_token


class Command(BaseCommand):
    help = "Authorize the Gmail sender account (gmail.send scope) and store its token locally."

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument("--port", type=int, default=8765, help="Local port for the OAuth redirect.")
        parser.add_argument("--no-browser", action="store_true", help="Print the consent URL instead of opening it.")

    def handle(self, *args: object, **options: object) -> None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        client_file = Path(settings.GMAIL_OAUTH_CLIENT_FILE)
        token_file = Path(settings.GMAIL_TOKEN_FILE)
        if not settings.GMAIL_SENDER_EMAIL:
            raise CommandError("Set GMAIL_SENDER_EMAIL in backend/.env first.")
        if not client_file.is_file():
            raise CommandError(
                f"OAuth client file not found at {client_file}. Download a 'Desktop app' OAuth client "
                "JSON from Google Cloud Console and save it there (see README)."
            )

        # Google may report scopes in a different form (e.g. 'email'); accept that.
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
        flow = InstalledAppFlow.from_client_secrets_file(str(client_file), scopes=SCOPES)
        self.stdout.write(f"Sign in as {settings.GMAIL_SENDER_EMAIL} and allow sending email on your behalf.")
        credentials = flow.run_local_server(
            port=int(options["port"]),  # type: ignore[arg-type]
            open_browser=not options["no_browser"],
            login_hint=settings.GMAIL_SENDER_EMAIL,
            prompt="consent",  # ensures Google issues a refresh token
        )
        if not credentials.refresh_token:
            raise CommandError("Google did not return a refresh token. Revoke the app's access and run this again.")

        save_token(credentials, token_file)
        self.stdout.write(f"Token saved to {token_file} (permissions 600).")

        status = GmailClient.from_settings().check_ready()
        if not status.ready:
            raise CommandError(f"Authorization saved, but Gmail is not ready: {status.detail}")
        self.stdout.write(self.style.SUCCESS(f"Gmail is ready. Sender: {status.authorized_email}"))

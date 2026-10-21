import getpass
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Profile, Role


class Command(BaseCommand):
    help = "Create a CollectFlow user with a role, or change an existing user's role and password."

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument("username")
        parser.add_argument("--role", choices=Role.values, default=Role.OPERATOR)
        parser.add_argument("--email", default="")
        parser.add_argument(
            "--password-env",
            metavar="VAR",
            help="Read the password from this environment variable instead of prompting.",
        )
        parser.add_argument("--keep-password", action="store_true", help="Only change the role of an existing user.")

    def handle(self, *args, **options):  # type: ignore[no-untyped-def]
        User = get_user_model()
        username = options["username"].strip()
        user = User.objects.filter(username=username).first()
        if options["keep_password"] and user is None:
            raise CommandError(f"User {username!r} does not exist.")

        password = None
        if not options["keep_password"]:
            password = self._password(options["password_env"])
            candidate = user or User(username=username, email=options["email"])
            try:
                validate_password(password, candidate)
            except ValidationError as exc:
                raise CommandError(" ".join(exc.messages)) from None

        with transaction.atomic():
            if user is None:
                user = User.objects.create_user(username=username, email=options["email"], password=password)
                action = "Created"
            else:
                if password is not None:
                    user.set_password(password)
                if options["email"]:
                    user.email = options["email"]
                user.save()
                action = "Updated"
            Profile.objects.update_or_create(user=user, defaults={"role": options["role"]})

        self.stdout.write(self.style.SUCCESS(f"{action} {username} with role {options['role']}."))

    @staticmethod
    def _password(env_name: str | None) -> str:
        if env_name:
            value = os.environ.get(env_name, "")
            if not value:
                raise CommandError(f"Environment variable {env_name} is empty or not set.")
            return value
        first = getpass.getpass("Password: ")
        if first != getpass.getpass("Password (again): "):
            raise CommandError("Passwords do not match.")
        return first

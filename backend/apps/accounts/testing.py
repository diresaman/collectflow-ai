from typing import Any

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.automation import outbox

from .models import Profile, Role

TEST_PASSWORD = "correct-horse-battery-staple"


def make_user(username: str = "operator", role: str | None = Role.OPERATOR, **fields: Any) -> Any:
    user = get_user_model().objects.create_user(username=username, password=TEST_PASSWORD, **fields)
    if role is not None:
        Profile.objects.create(user=user, role=role)
    return user


def run_jobs() -> int:
    """Runs every queued outbox job inline, as the worker would."""
    return outbox.run_pending("test-worker")


class SessionApiTestCase(TestCase):
    """API tests with a signed-in user of `role`. Subclasses with setUp must call super().setUp()."""

    role: str = Role.OPERATOR

    def setUp(self) -> None:
        super().setUp()
        self.user = make_user(username=f"{self.role.lower()}-user", role=self.role)
        self.client.force_login(self.user)

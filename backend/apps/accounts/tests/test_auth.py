import os
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from rest_framework.permissions import SAFE_METHODS

from apps.accounts.models import LoginFailure, Profile, Role
from apps.accounts.permissions import IsAdmin, IsApprover, IsOperator, ReadOperatorWriteAdmin
from apps.accounts.roles import has_role, role_of
from apps.accounts.testing import TEST_PASSWORD, make_user


class LoginTests(TestCase):
    def setUp(self) -> None:
        # A browser-like client: CSRF is enforced exactly as in production.
        self.browser = Client(enforce_csrf_checks=True)
        self.user = make_user(username="ana", role=Role.APPROVER, first_name="Ana", last_name="Tesfaye")

    def csrf_token(self) -> str:
        self.browser.get(reverse("auth-csrf"))
        return self.browser.cookies["csrftoken"].value

    def login(self, username: str = "ana", password: str = TEST_PASSWORD, token: str | None = None):  # type: ignore[no-untyped-def]
        token = token if token is not None else self.csrf_token()
        return self.browser.post(
            reverse("auth-login"),
            {"username": username, "password": password},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

    def test_login_returns_the_user_and_role_and_starts_a_session(self) -> None:
        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {key: response.json()[key] for key in ("username", "display_name", "role")},
            {"username": "ana", "display_name": "Ana Tesfaye", "role": "APPROVER"},
        )
        self.assertIn("sessionid", self.browser.cookies)
        self.assertEqual(self.browser.get(reverse("me")).json()["role"], "APPROVER")

    def test_session_and_csrf_cookies_are_strict(self) -> None:
        response = self.login()
        self.assertEqual(response.cookies["sessionid"]["samesite"], "Strict")
        self.assertTrue(response.cookies["sessionid"]["httponly"])
        self.assertEqual(response.cookies["csrftoken"]["samesite"], "Strict")

    def test_login_requires_csrf(self) -> None:
        response = self.login(token="")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "csrf_failed")
        self.assertNotIn("sessionid", self.browser.cookies)

    def test_writes_require_csrf_once_signed_in(self) -> None:
        self.login()
        self.assertEqual(self.browser.post(reverse("sync"), content_type="application/json").status_code, 403)
        token = self.browser.cookies["csrftoken"].value  # rotated at login
        with mock.patch("apps.invoices.views.start_sync") as start:
            start.side_effect = AssertionError("reached the view")
            with self.assertRaises(AssertionError):
                self.browser.post(reverse("sync"), content_type="application/json", HTTP_X_CSRFTOKEN=token)

    def test_wrong_password_is_rejected_without_detail(self) -> None:
        response = self.login(password="wrong")
        self.assertEqual((response.status_code, response.json()["code"]), (400, "invalid_credentials"))
        unknown = self.login(username="nobody")
        self.assertEqual(unknown.json(), response.json())  # no user enumeration

    @override_settings(LOGIN_MAX_FAILURES=5)
    def test_five_failures_lock_the_username_for_the_window(self) -> None:
        for _ in range(5):
            self.assertEqual(self.login(password="wrong").status_code, 400)

        locked = self.login()  # even the correct password
        self.assertEqual((locked.status_code, locked.json()["code"]), (429, "too_many_attempts"))
        self.assertEqual(self.login(username="ANA").status_code, 429)  # case-insensitive

        LoginFailure.objects.update(occurred_at="2000-01-01T00:00:00Z")
        self.assertEqual(self.login().status_code, 200)
        self.assertFalse(LoginFailure.objects.exists())  # cleared on success

    def test_user_without_a_role_cannot_sign_in(self) -> None:
        make_user(username="norole", role=None)
        response = self.login(username="norole")
        self.assertEqual((response.status_code, response.json()["code"]), (403, "no_role"))
        self.assertNotIn("sessionid", self.browser.cookies)

    def test_inactive_user_cannot_sign_in(self) -> None:
        get_user_model().objects.filter(username="ana").update(is_active=False)
        self.assertEqual(self.login().status_code, 400)

    def test_logout_ends_the_session(self) -> None:
        self.login()
        token = self.browser.cookies["csrftoken"].value
        self.assertEqual(self.browser.post(reverse("auth-logout"), content_type="application/json", HTTP_X_CSRFTOKEN=token).status_code, 200)
        self.assertEqual(self.browser.get(reverse("me")).status_code, 401)

    def test_unauthenticated_me_is_401(self) -> None:
        response = self.browser.get(reverse("me"))
        self.assertEqual((response.status_code, response.json()["code"]), (401, "not_authenticated"))

    def test_passwords_are_hashed_with_argon2(self) -> None:
        self.assertTrue(self.user.password.startswith("argon2"))


class RoleTests(TestCase):
    def test_roles_are_ordered(self) -> None:
        operator = make_user("op", Role.OPERATOR)
        approver = make_user("ap", Role.APPROVER)
        admin = make_user("ad", Role.ADMIN)
        none = make_user("none", None)
        superuser = get_user_model().objects.create_superuser("root", password=TEST_PASSWORD)

        self.assertEqual([role_of(u) for u in (operator, approver, admin, none, superuser)],
                         ["OPERATOR", "APPROVER", "ADMIN", None, "ADMIN"])
        self.assertEqual(
            [[has_role(u, minimum) for minimum in Role.values] for u in (operator, approver, admin, none)],
            [[True, False, False], [True, True, False], [True, True, True], [False, False, False]],
        )

    def test_permission_classes(self) -> None:
        operator = make_user("op", Role.OPERATOR)
        admin = make_user("ad", Role.ADMIN)
        request = mock.Mock(user=operator, method="GET")
        self.assertTrue(IsOperator().has_permission(request, None))
        self.assertFalse(IsApprover().has_permission(request, None))
        self.assertFalse(IsAdmin().has_permission(request, None))
        self.assertTrue(ReadOperatorWriteAdmin().has_permission(request, None))
        request.method = "POST"
        self.assertNotIn("POST", SAFE_METHODS)
        self.assertFalse(ReadOperatorWriteAdmin().has_permission(request, None))
        request.user = admin
        self.assertTrue(ReadOperatorWriteAdmin().has_permission(request, None))


class CreateUserCommandTests(TestCase):
    def test_creates_and_updates_users(self) -> None:
        out = StringIO()
        with mock.patch.dict(os.environ, {"CF_PASSWORD": TEST_PASSWORD}):
            call_command("create_user", "sara", "--role", "APPROVER", "--password-env", "CF_PASSWORD", stdout=out)
        user = get_user_model().objects.get(username="sara")
        self.assertTrue(user.check_password(TEST_PASSWORD))
        self.assertEqual(user.profile.role, "APPROVER")
        self.assertIn("Created sara", out.getvalue())

        call_command("create_user", "sara", "--role", "ADMIN", "--keep-password", stdout=StringIO())
        self.assertEqual(Profile.objects.get(user=user).role, "ADMIN")

    def test_rejects_weak_passwords_and_missing_users(self) -> None:
        with mock.patch.dict(os.environ, {"CF_PASSWORD": "123"}), self.assertRaises(CommandError):
            call_command("create_user", "weak", "--password-env", "CF_PASSWORD", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("create_user", "ghost", "--keep-password", stdout=StringIO())
        self.assertFalse(get_user_model().objects.filter(username__in=["weak", "ghost"]).exists())

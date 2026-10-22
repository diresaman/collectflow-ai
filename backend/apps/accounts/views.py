"""Session login for the Next.js proxy. Credentials are checked here; the browser never holds a token."""

import datetime as dt

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from django.utils import timezone
from rest_framework import exceptions, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import SessionAuthentication
from .models import LoginFailure
from .roles import describe, role_of


class CsrfView(APIView):
    """Sets the CSRF cookie so the login form can echo it back in X-CSRFToken."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        get_token(request)
        return Response({"detail": "CSRF cookie set."})


class LoginView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        # DRF only checks CSRF for authenticated sessions; a login form must be protected too.
        SessionAuthentication().enforce_csrf(request)

        data = request.data if isinstance(request.data, dict) else {}
        username = data.get("username")
        password = data.get("password")
        if not isinstance(username, str) or not isinstance(password, str) or not username.strip() or not password:
            return Response(
                {"code": "invalid_input", "detail": "Enter a username and password."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        username = username.strip()[:150]

        window_start = timezone.now() - dt.timedelta(seconds=settings.LOGIN_FAILURE_WINDOW_SECONDS)
        recent_failures = LoginFailure.objects.filter(username__iexact=username, occurred_at__gte=window_start)
        if recent_failures.count() >= settings.LOGIN_MAX_FAILURES:
            return Response(
                {"code": "too_many_attempts", "detail": "Too many failed sign-in attempts. Try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        user = authenticate(request._request, username=username, password=password)
        if user is None:
            LoginFailure.objects.create(username=username.lower())
            return Response(
                {"code": "invalid_credentials", "detail": "Incorrect username or password."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if role_of(user) is None:
            return Response(
                {"code": "no_role", "detail": "This account has no CollectFlow role. Ask an admin to assign one."},
                status=status.HTTP_403_FORBIDDEN,
            )

        LoginFailure.objects.filter(username__iexact=username).delete()
        login(request._request, user)  # rotates the session key and the CSRF token
        return Response(describe(user))


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        logout(request._request)
        return Response({"detail": "Signed out."})


class MeView(APIView):
    def get(self, request: Request) -> Response:
        if role_of(request.user) is None:  # unreachable behind IsOperator; kept as a guard
            raise exceptions.PermissionDenied()
        return Response(describe(request.user))


class UserListView(APIView):
    """GET /api/users/: active users with a CollectFlow role, for task and dispute assignment (M11)."""

    def get(self, request: Request) -> Response:
        from django.contrib.auth import get_user_model

        users = get_user_model().objects.filter(is_active=True).select_related("profile").order_by("username")
        rows = [{"id": u.pk, "username": u.username, "display_name": u.get_full_name() or u.username, "role": role_of(u)}
                for u in users]
        return Response([row for row in rows if row["role"]])

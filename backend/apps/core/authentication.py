import hmac

from django.conf import settings
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.request import Request


class OperatorUser:
    """Minimal principal representing the local development operator."""

    is_authenticated = True
    is_anonymous = False
    username = "operator"

    def __str__(self) -> str:
        return self.username


class OperatorTokenAuthentication(BaseAuthentication):
    """
    Local MVP access control: `Authorization: Bearer <COLLECTFLOW_OPERATOR_TOKEN>`.

    If no token is configured on the backend, every request is rejected.
    """

    keyword = "Bearer"

    def authenticate(self, request: Request) -> tuple[OperatorUser, None] | None:
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword.lower().encode():
            return None
        if len(parts) != 2:
            raise exceptions.AuthenticationFailed("Invalid authorization header.")

        expected = settings.OPERATOR_TOKEN
        if not expected:
            raise exceptions.AuthenticationFailed("Operator token is not configured on the server.")
        if not hmac.compare_digest(parts[1], expected.encode()):
            raise exceptions.AuthenticationFailed("Invalid operator token.")

        return OperatorUser(), None

    def authenticate_header(self, request: Request) -> str:
        return self.keyword

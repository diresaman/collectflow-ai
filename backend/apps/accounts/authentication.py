from rest_framework import authentication
from rest_framework.request import Request


class SessionAuthentication(authentication.SessionAuthentication):
    """
    Django session authentication with CSRF enforced on unsafe methods (DRF's behaviour).

    Declaring an authenticate header makes DRF answer 401 rather than 403 when no session is
    present, so the frontend can tell "log in again" apart from "not allowed".
    """

    def authenticate_header(self, request: Request) -> str:
        return 'Session realm="collectflow"'

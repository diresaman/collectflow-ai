from typing import Any

from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """DRF errors in the API's standard shape: {code, detail, fields?}."""
    response = drf_exception_handler(exc, context)
    if response is None or not isinstance(exc, exceptions.APIException):
        return response

    if isinstance(exc, exceptions.ValidationError):
        response.data = {"code": "invalid_input", "detail": "Invalid input.", "fields": response.data}
    elif isinstance(exc, exceptions.NotAuthenticated):
        response.data = {"code": "not_authenticated", "detail": "Sign in to continue."}
    else:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        code = "csrf_failed" if detail.startswith("CSRF Failed") else exc.default_code
        response.data = {"code": code, "detail": detail}
    return response

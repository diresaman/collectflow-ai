from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthCheckView(APIView):
    """Lightweight liveness probe used by the frontend to confirm connectivity."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response({"status": "ok", "service": "collectflow-api"})


class IntegrationStatusView(APIView):
    """GET /api/integrations/status/: integration and worker health from local state only (no provider calls)."""

    def get(self, request: Request) -> Response:
        from .integrations import report

        return Response(report())


class TodayView(APIView):
    """GET /api/today/: actionable work in fixed sections, deterministic order, no scoring (M12)."""

    def get(self, request: Request) -> Response:
        from .today import report

        return Response(report(request.user))

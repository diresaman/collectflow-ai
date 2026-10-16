from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.invoices.models import AutomationRun

from .orchestrator import AutomationAlreadyRunning, AutomationOrchestrator
from .serializers import AutomationRunDetailSerializer


class RunAutomationView(APIView):
    """Runs the shared AutomationOrchestrator synchronously (manual trigger). Never sends email."""

    def post(self, request: Request) -> Response:
        try:
            run = AutomationOrchestrator().run(trigger=AutomationRun.Trigger.MANUAL)
        except AutomationAlreadyRunning as exc:
            return Response(
                {
                    "detail": "Another automation run is in progress. Wait for it to finish.",
                    "code": "automation_running",
                    "run_id": exc.run_id,
                },
                status=status.HTTP_409_CONFLICT,
            )
        data = AutomationRunDetailSerializer(run).data
        http_status = status.HTTP_502_BAD_GATEWAY if run.status == AutomationRun.Status.FAILED else status.HTTP_200_OK
        return Response(data, status=http_status)


class AutomationRunDetailView(APIView):
    def get(self, request: Request, pk: int) -> Response:
        run = AutomationRun.objects.prefetch_related("items").filter(pk=pk).first()
        if run is None:
            return Response({"detail": "Automation run not found.", "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(AutomationRunDetailSerializer(run).data)

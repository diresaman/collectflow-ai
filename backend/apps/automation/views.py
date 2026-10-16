from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.invoices.models import AutomationRun

from . import workflow
from .models import AutomationRunItem
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


# Interactive workflow -----------------------------------------------------------
# Read-only views of persisted state plus two workflow-only actions. Approve, reject, edit and
# send stay on the existing follow-up endpoints.

def _workflow_run(pk: int) -> AutomationRun | None:
    return AutomationRun.objects.filter(pk=pk, kind=AutomationRun.Kind.AUTOMATION).first()


def _not_found() -> Response:
    return Response({"detail": "Workflow not found.", "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)


def _conflict(error: workflow.WorkflowConflict) -> Response:
    return Response({"detail": error.message, "code": error.code}, status=status.HTTP_409_CONFLICT)


class CurrentWorkflowView(APIView):
    def get(self, request: Request) -> Response:
        run = workflow.current_run()
        return Response({"workflow": workflow.build(run) if run else None})


class WorkflowDetailView(APIView):
    def get(self, request: Request, pk: int) -> Response:
        run = _workflow_run(pk)
        return Response(workflow.build(run)) if run else _not_found()


class WorkflowStepsView(APIView):
    def get(self, request: Request, pk: int) -> Response:
        run = _workflow_run(pk)
        if run is None:
            return _not_found()
        data = workflow.build(run)
        return Response({"state": data["state"], "current_step": data["current_step"], "steps": data["steps"]})


class WorkflowPendingActionsView(APIView):
    def get(self, request: Request, pk: int) -> Response:
        run = _workflow_run(pk)
        return Response(workflow.build(run)["pending_actions"]) if run else _not_found()


class FinalizeWorkflowView(APIView):
    def post(self, request: Request, pk: int) -> Response:
        run = _workflow_run(pk)
        if run is None:
            return _not_found()
        try:
            run = workflow.finalize(run)
        except workflow.WorkflowConflict as error:
            return _conflict(error)
        return Response(workflow.build(run))


class LeaveUnsentView(APIView):
    def post(self, request: Request, pk: int, item_id: int) -> Response:
        run = _workflow_run(pk)
        if run is None:
            return _not_found()
        try:
            run = workflow.leave_unsent(run, item_id)
        except AutomationRunItem.DoesNotExist:
            return Response({"detail": "Item not found in this workflow.", "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        except workflow.WorkflowConflict as error:
            return _conflict(error)
        return Response(workflow.build(run))

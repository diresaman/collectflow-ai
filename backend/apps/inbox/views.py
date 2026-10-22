import uuid

from django.db.models import QuerySet
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import Task
from .serializers import TaskSerializer


class TaskListView(generics.ListAPIView):
    """GET /api/tasks/?open=true&invoice=<id>"""

    serializer_class = TaskSerializer

    def get_queryset(self) -> QuerySet[Task]:
        queryset = Task.objects.select_related("invoice", "client", "step", "done_by")
        params = self.request.query_params
        if params.get("open") == "true":
            queryset = queryset.filter(done_at__isnull=True)
        if params.get("invoice"):
            try:
                queryset = queryset.filter(invoice_id=uuid.UUID(params["invoice"]))
            except ValueError:
                return queryset.none()
        return queryset


class TaskDoneView(APIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        try:
            task = services.mark_done(pk, actor=request.user)
        except Task.DoesNotExist:
            return Response({"code": "not_found", "detail": "Task not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(TaskSerializer(task).data)

import uuid
from typing import Any

from django.db.models import Count, Exists, OuterRef, Q, QuerySet
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.followups.services import FollowUpError
from apps.invoices.models import ChaseHold

from . import services, triage
from .models import InboundMessage, Task
from .serializers import ReplyDetailSerializer, ReplyListSerializer, TaskSerializer


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


# Customer replies (M10) ---------------------------------------------------------------------------


def _error(error: FollowUpError) -> Response:
    return Response({"code": error.code, "detail": error.message}, status=error.status_code)


def _reply_not_found() -> Response:
    return Response({"code": "not_found", "detail": "Reply not found."}, status=status.HTTP_404_NOT_FOUND)


def _body(request: Request) -> dict[str, Any]:
    return request.data if isinstance(request.data, dict) else {}


def _replies() -> QuerySet[InboundMessage]:
    active = ChaseHold.objects.filter(reply_id=OuterRef("pk"), released_at__isnull=True)
    return InboundMessage.objects.select_related("invoice", "client").annotate(has_active_hold=Exists(active))


def _detail(pk: uuid.UUID) -> Response:
    reply = (_replies().select_related("replied_to__follow_up__step")
             .prefetch_related("classifications__actor", "tasks").filter(pk=pk).first())
    return Response(ReplyDetailSerializer(reply).data) if reply else _reply_not_found()


class ReplyListView(generics.ListAPIView):
    """GET /api/replies/?triage=true | ?state=ACTIONED | ?invoice=<id>"""

    serializer_class = ReplyListSerializer

    def get_queryset(self) -> QuerySet[InboundMessage]:
        queryset = _replies()
        params = self.request.query_params
        if params.get("triage") == "true":
            queryset = queryset.filter(state__in=[InboundMessage.State.TRIAGE, InboundMessage.State.RECEIVED])
        if params.get("state") in InboundMessage.State.values:
            queryset = queryset.filter(state=params["state"])
        if params.get("invoice"):
            try:
                queryset = queryset.filter(Q(invoice_id=uuid.UUID(params["invoice"]))
                                           | Q(thread_invoice_ids__contains=[params["invoice"]]))
            except ValueError:
                return queryset.none()
        return queryset.order_by("-received_at")


class ReplySummaryView(APIView):
    """GET /api/replies/summary/: counts for the navigation badge."""

    def get(self, request: Request) -> Response:
        counts = dict(InboundMessage.objects.values_list("state").annotate(n=Count("pk")))
        return Response({
            "needs_review": counts.get(InboundMessage.State.TRIAGE, 0) + counts.get(InboundMessage.State.RECEIVED, 0),
            "by_state": counts,
            "invoices_on_hold": ChaseHold.objects.filter(released_at__isnull=True).values("invoice").distinct().count(),
        })


class ReplyDetailView(APIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        return _detail(pk)


class _ReplyAction(APIView):
    def respond(self, pk: uuid.UUID, action: Any) -> Response:
        try:
            action()
        except InboundMessage.DoesNotExist:
            return _reply_not_found()
        except FollowUpError as error:
            return _error(error)
        return _detail(pk)


class ReplyClassifyView(_ReplyAction):
    """POST {label, extracted?, note?}: a person classifies, confirms or corrects the reply."""

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        data = _body(request)
        return self.respond(pk, lambda: triage.classify(
            pk, label=data.get("label"), extracted=data.get("extracted"), note=data.get("note", ""), actor=request.user
        ))


class ReplyReleaseHoldView(_ReplyAction):
    """POST {note}: resume chasing (an APPROVER for promise, dispute or payment-claim holds)."""

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        data = _body(request)
        return self.respond(pk, lambda: triage.release_hold(pk, note=data.get("note", ""), actor=request.user))


class ReplyRetryView(APIView):
    """POST: queue one more AI classification attempt. 202 with the job ID."""

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        try:
            job = triage.retry_classification(pk, actor=request.user)
        except InboundMessage.DoesNotExist:
            return _reply_not_found()
        except FollowUpError as error:
            return _error(error)
        return Response({"job_id": str(job.pk), "status": job.status}, status=status.HTTP_202_ACCEPTED)

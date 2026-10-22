import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Count, Exists, OuterRef, Q, QuerySet
from rest_framework import generics, status
from rest_framework.exceptions import ParseError, UnsupportedMediaType
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.followups.services import FollowUpError
from apps.invoices.models import ChaseHold, Invoice

from . import services, triage
from .models import InboundMessage, Task
from .serializers import ReplyDetailSerializer, ReplyListSerializer, TaskSerializer


def _task_queryset() -> QuerySet[Task]:
    return Task.objects.select_related("invoice", "client", "step", "done_by", "assignee", "created_by", "cancelled_by")


class TaskListView(generics.ListCreateAPIView):
    """
    GET /api/tasks/?open=true&invoice=<id>&assignee=me|<user id>&status=OPEN|DONE|CANCELLED
    POST /api/tasks/ {invoice_id, title, due_at?, assignee_id?, detail?}: a manual follow-up task.
    """

    serializer_class = TaskSerializer

    def get_queryset(self) -> QuerySet[Task]:
        queryset = _task_queryset()
        params = self.request.query_params
        if params.get("open") == "true":
            queryset = queryset.filter(status=Task.Status.OPEN)
        if params.get("status") in Task.Status.values:
            queryset = queryset.filter(status=params["status"])
        if params.get("assignee") == "me":
            queryset = queryset.filter(assignee=self.request.user)
        elif (params.get("assignee") or "").isdigit():
            queryset = queryset.filter(assignee_id=int(params["assignee"]))
        if params.get("invoice"):
            try:
                queryset = queryset.filter(invoice_id=uuid.UUID(params["invoice"]))
            except ValueError:
                return queryset.none()
        return queryset

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        data = _body(request)
        try:
            task = services.create(data.get("invoice_id"), title=data.get("title"), due_at=data.get("due_at"),
                                   assignee_id=data.get("assignee_id"), detail=data.get("detail", ""),
                                   actor=request.user)
        except (Invoice.DoesNotExist, ValidationError, ValueError):
            return Response({"code": "not_found", "detail": "Invoice not found."}, status=status.HTTP_404_NOT_FOUND)
        except FollowUpError as error:
            return _error(error)
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)


class _TaskAction(APIView):
    def respond(self, action: Any) -> Response:
        try:
            task = action()
        except Task.DoesNotExist:
            return Response({"code": "not_found", "detail": "Task not found."}, status=status.HTTP_404_NOT_FOUND)
        except FollowUpError as error:
            return _error(error)
        return Response(TaskSerializer(_task_queryset().get(pk=task.pk)).data)


class TaskDoneView(_TaskAction):
    """POST {note?}: mark done (idempotent)."""

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        data = _body(request)
        return self.respond(lambda: services.mark_done(pk, actor=request.user, note=data.get("note", "")))


class TaskCancelView(_TaskAction):
    """POST {note}: cancel an open task (not a sequence call task)."""

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        data = _body(request)
        return self.respond(lambda: services.cancel(pk, actor=request.user, note=data.get("note")))


class TaskAssignView(_TaskAction):
    """POST {assignee_id | null}."""

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        data = _body(request)
        return self.respond(lambda: services.assign(pk, assignee_id=data.get("assignee_id"), actor=request.user))


# Customer replies (M10) ---------------------------------------------------------------------------


def _error(error: FollowUpError) -> Response:
    return Response({"code": error.code, "detail": error.message}, status=error.status_code)


def _reply_not_found() -> Response:
    return Response({"code": "not_found", "detail": "Reply not found."}, status=status.HTTP_404_NOT_FOUND)


def _body(request: Request) -> dict[str, Any]:
    """The JSON body, or {} when there is none or it is not JSON (e.g. a bare POST to mark a task done)."""
    try:
        data = request.data
    except (ParseError, UnsupportedMediaType):
        return {}
    return data if isinstance(data, dict) else {}


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

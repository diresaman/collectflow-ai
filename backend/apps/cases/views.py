import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from rest_framework import generics, status
from rest_framework.exceptions import ParseError, UnsupportedMediaType
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.followups.services import FollowUpError
from apps.invoices.models import Invoice

from . import disputes, promises
from .models import DisputeCase, PromiseToPay
from .serializers import DisputeSerializer, PromiseSerializer


def _error(error: FollowUpError) -> Response:
    return Response({"code": error.code, "detail": error.message}, status=error.status_code)


def _not_found(what: str) -> Response:
    return Response({"code": "not_found", "detail": f"{what} not found."}, status=status.HTTP_404_NOT_FOUND)


def _body(request: Request) -> dict[str, Any]:
    """The JSON body, or {} when there is none or it is not JSON (e.g. a bare POST to mark a task done)."""
    try:
        data = request.data
    except (ParseError, UnsupportedMediaType):
        return {}
    return data if isinstance(data, dict) else {}


def _filter(queryset: QuerySet[Any], request: Request, statuses: list[str]) -> QuerySet[Any]:
    params = request.query_params
    if params.get("status") in statuses:
        queryset = queryset.filter(status=params["status"])
    if params.get("invoice"):
        try:
            queryset = queryset.filter(invoice_id=uuid.UUID(params["invoice"]))
        except ValueError:
            return queryset.none()
    if params.get("reply"):
        try:
            queryset = queryset.filter(reply_id=uuid.UUID(params["reply"]))
        except ValueError:
            return queryset.none()
    return queryset


def _promises() -> QuerySet[PromiseToPay]:
    return PromiseToPay.objects.select_related("invoice", "client", "owner", "resolved_by")


def _disputes() -> QuerySet[DisputeCase]:
    return DisputeCase.objects.select_related("invoice", "client", "owner", "opened_by", "resolved_by").prefetch_related(
        "notes__author")


class PromiseListView(generics.ListCreateAPIView):
    """GET ?invoice=&status=&reply=. POST {invoice_id, promised_date, amount?, reply_id?, note?} (operator)."""

    serializer_class = PromiseSerializer

    def get_queryset(self) -> QuerySet[PromiseToPay]:
        return _filter(_promises(), self.request, PromiseToPay.Status.values)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        data = _body(request)
        try:
            promise = promises.record(data.get("invoice_id"), promised_date=data.get("promised_date"),
                                      amount=data.get("amount"), reply_id=data.get("reply_id"), note=data.get("note", ""),
                                      actor=request.user)
        except (Invoice.DoesNotExist, ValidationError, ValueError):
            return _not_found("Invoice")
        except FollowUpError as error:
            return _error(error)
        return Response(PromiseSerializer(_promises().get(pk=promise.pk)).data, status=status.HTTP_201_CREATED)


class PromiseCancelView(APIView):
    """POST {note}: an approver cancels an open promise (chasing resumes if nothing else holds the invoice)."""

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        try:
            promise = promises.cancel(pk, note=_body(request).get("note"), actor=request.user)
        except PromiseToPay.DoesNotExist:
            return _not_found("Promise")
        except FollowUpError as error:
            return _error(error)
        return Response({**PromiseSerializer(_promises().get(pk=promise.pk)).data,
                         "chasing": disputes.chasing_status(promise.invoice)})


class DisputeListView(generics.ListCreateAPIView):
    """GET ?invoice=&status=&reply=. POST {invoice_id, reason, description, reply_id?, owner_id?} (operator)."""

    serializer_class = DisputeSerializer

    def get_queryset(self) -> QuerySet[DisputeCase]:
        return _filter(_disputes(), self.request, DisputeCase.Status.values)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        data = _body(request)
        try:
            case = disputes.open_case(data.get("invoice_id"), reason=data.get("reason"),
                                      description=data.get("description"), reply_id=data.get("reply_id"),
                                      owner_id=data.get("owner_id"), actor=request.user)
        except (Invoice.DoesNotExist, ValidationError, ValueError):
            return _not_found("Invoice")
        except FollowUpError as error:
            return _error(error)
        return Response(DisputeSerializer(_disputes().get(pk=case.pk)).data, status=status.HTTP_201_CREATED)


class DisputeDetailView(APIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        case = _disputes().filter(pk=pk).first()
        return Response(DisputeSerializer(case).data) if case else _not_found("Dispute")


class _DisputeAction(APIView):
    def respond(self, pk: uuid.UUID, action: Any, extra: Any = None) -> Response:
        try:
            result = action()
        except DisputeCase.DoesNotExist:
            return _not_found("Dispute")
        except FollowUpError as error:
            return _error(error)
        body = DisputeSerializer(_disputes().get(pk=pk)).data
        return Response({**body, **(extra(result) if extra else {})})


class DisputeAssignView(_DisputeAction):
    """POST {owner_id | null} (operator)."""

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        return self.respond(pk, lambda: disputes.assign(pk, owner_id=_body(request).get("owner_id"), actor=request.user))


class DisputeNoteView(_DisputeAction):
    """POST {note} (operator): an internal note."""

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        return self.respond(pk, lambda: disputes.add_note(pk, note=_body(request).get("note"), actor=request.user))


class DisputeResolveView(_DisputeAction):
    """POST {outcome: VALID | INVALID, resolution} (approver). Returns whether chasing resumes, and why not."""

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        data = _body(request)
        return self.respond(
            pk, lambda: disputes.resolve(pk, outcome=data.get("outcome"), resolution=data.get("resolution"),
                                         actor=request.user),
            extra=lambda result: {"chasing": result[1]},
        )

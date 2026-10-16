import uuid
from typing import Any

from django.db.models import QuerySet
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.invoices.models import Invoice

from . import services
from .models import FollowUp
from .serializers import FollowUpSerializer


def error_response(error: services.FollowUpError) -> Response:
    return Response({"detail": error.message, "code": error.code, **error.extra}, status=error.status_code)


def not_found(what: str) -> Response:
    return Response({"detail": f"{what} not found.", "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)


class AnalyzeInvoiceView(APIView):
    """Generates an AI reminder draft for an overdue invoice."""

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        regenerate = request.data.get("regenerate", False) if isinstance(request.data, dict) else False
        if not isinstance(regenerate, bool):
            return Response(
                {"detail": "regenerate must be true or false.", "code": "invalid_input"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            follow_up = services.FollowUpGenerationService().generate(pk, regenerate=regenerate)
        except Invoice.DoesNotExist:
            return not_found("Invoice")
        except services.FollowUpError as error:
            return error_response(error)
        return Response(FollowUpSerializer(follow_up).data, status=status.HTTP_201_CREATED)


class FollowUpListView(generics.ListAPIView):
    serializer_class = FollowUpSerializer

    def get_queryset(self) -> QuerySet[FollowUp]:
        queryset = FollowUp.objects.select_related("invoice")
        invoice_id = self.request.query_params.get("invoice")
        if invoice_id:
            try:
                queryset = queryset.filter(invoice_id=uuid.UUID(invoice_id))
            except ValueError:
                return queryset.none()
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())
        return queryset


class FollowUpDetailView(APIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        follow_up = FollowUp.objects.select_related("invoice").filter(pk=pk).first()
        if follow_up is None:
            return not_found("Follow-up")
        return Response(FollowUpSerializer(follow_up).data)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        if not isinstance(request.data, dict):
            return Response(
                {"detail": "Expected a JSON object.", "code": "invalid_input"}, status=status.HTTP_400_BAD_REQUEST
            )
        return _run(lambda: services.update_draft(pk, dict(request.data)))


def _run(action: Any) -> Response:
    try:
        follow_up = action()
    except FollowUp.DoesNotExist:
        return not_found("Follow-up")
    except services.FollowUpError as error:
        return error_response(error)
    return Response(FollowUpSerializer(follow_up).data)


class ApproveFollowUpView(APIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        return _run(lambda: services.approve(pk))


class RejectFollowUpView(APIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        return _run(lambda: services.reject(pk))

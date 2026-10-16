import uuid
from typing import Any

from django.db.models import QuerySet
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.invoices.models import Invoice

from django.conf import settings

from integrations.gmail import GmailClient

from . import services
from .delivery import FollowUpDeliveryService
from .models import FollowUp
from .serializers import DeliveryAttemptSerializer, FollowUpSerializer


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


# Delivery ---------------------------------------------------------------------

DELIVERY_RESPONSES = {
    # status: (HTTP status, code, detail)
    FollowUp.Status.SENT: (status.HTTP_200_OK, "sent", "Gmail accepted the email."),
    FollowUp.Status.FAILED: (
        status.HTTP_502_BAD_GATEWAY,
        "delivery_failed",
        "Gmail did not accept the email. It was not sent.",
    ),
    # 202 rather than 5xx: the outcome is unresolved and must not be retried automatically.
    FollowUp.Status.DELIVERY_UNKNOWN: (
        status.HTTP_202_ACCEPTED,
        "delivery_unknown",
        "It is unknown whether Gmail accepted the email. Do not resend. Check the sender's Gmail "
        "Sent folder, then reconcile with: python manage.py reconcile_delivery",
    ),
}


class SendFollowUpView(APIView):
    """Submits an APPROVED follow-up to Gmail. Never retries."""

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        try:
            outcome = FollowUpDeliveryService().send(pk)
        except FollowUp.DoesNotExist:
            return not_found("Follow-up")
        except services.FollowUpError as error:
            return error_response(error)

        http_status, code, detail = DELIVERY_RESPONSES[outcome.follow_up.status]
        if outcome.attempt.error_summary and code != "sent":
            detail = f"{detail} ({outcome.attempt.error_summary})"
        return Response(
            {
                "code": code,
                "detail": detail,
                "requires_reconciliation": code == "delivery_unknown",
                "follow_up": FollowUpSerializer(outcome.follow_up).data,
                "delivery_attempt": DeliveryAttemptSerializer(outcome.attempt).data,
            },
            status=http_status,
        )


class DeliveryAttemptListView(APIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        follow_up = FollowUp.objects.filter(pk=pk).first()
        if follow_up is None:
            return not_found("Follow-up")
        return Response(DeliveryAttemptSerializer(follow_up.delivery_attempts.all(), many=True).data)


class GmailStatusView(APIView):
    """Reports whether Gmail sending is configured and authorized. Never sends or reads mail."""

    def get(self, request: Request) -> Response:
        gmail_status = GmailClient.from_settings().check_ready()
        return Response(
            {
                "configured": gmail_status.configured,
                "ready": gmail_status.ready,
                "sender_email": gmail_status.sender_email,
                "authorized_email": gmail_status.authorized_email,
                "detail": gmail_status.detail,
                "allowed_recipients": settings.GMAIL_ALLOWED_RECIPIENTS,
                "recipient_allowlist_enforced": bool(settings.GMAIL_ALLOWED_RECIPIENTS) or settings.DEBUG,
            }
        )

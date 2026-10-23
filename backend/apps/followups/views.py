import uuid
from typing import Any

from django.conf import settings
from django.db.models import Q, QuerySet
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.automation.models import OutboxJob
from apps.invoices.models import Invoice
from integrations.gmail import GmailClient

from . import services
from .delivery import DELIVER_JOB_KIND, FollowUpDeliveryService
from .models import FollowUp
from .serializers import DeliveryAttemptSerializer, FollowUpSerializer


def error_response(error: services.FollowUpError) -> Response:
    return Response({"detail": error.message, "code": error.code, **error.extra}, status=error.status_code)


def not_found(what: str) -> Response:
    return Response({"detail": f"{what} not found.", "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)


class AnalyzeInvoiceView(APIView):
    """Queues an AI reminder draft for an overdue invoice: 202 with the job ID to poll."""

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        regenerate = request.data.get("regenerate", False) if isinstance(request.data, dict) else False
        if not isinstance(regenerate, bool):
            return Response(
                {"detail": "regenerate must be true or false.", "code": "invalid_input"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            job = services.request_generation(pk, regenerate=regenerate, actor=request.user)
        except Invoice.DoesNotExist:
            return not_found("Invoice")
        except services.FollowUpError as error:
            return error_response(error)
        return Response({"job_id": str(job.pk)}, status=status.HTTP_202_ACCEPTED)


class FollowUpListView(generics.ListAPIView):
    serializer_class = FollowUpSerializer

    def get_queryset(self) -> QuerySet[FollowUp]:
        queryset = FollowUp.objects.select_related("invoice", "step", "last_edited_by", "approved_by")
        invoice_id = self.request.query_params.get("invoice")
        if invoice_id:
            try:
                wanted = uuid.UUID(invoice_id)
                # An invoice's reminders, plus any consolidated statement listing it (M14).
                queryset = queryset.filter(
                    Q(invoice_id=wanted) | Q(kind=FollowUp.Kind.STATEMENT, statement_lines__invoice_id=wanted)
                ).distinct()
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
        return Response(FollowUpSerializer(follow_up, context={"request": request}).data)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        if not isinstance(request.data, dict):
            return Response(
                {"detail": "Expected a JSON object.", "code": "invalid_input"}, status=status.HTTP_400_BAD_REQUEST
            )
        return _run(lambda: services.update_draft(pk, dict(request.data), actor=request.user), request)


def _run(action: Any, request: Request) -> Response:
    try:
        follow_up = action()
    except FollowUp.DoesNotExist:
        return not_found("Follow-up")
    except services.FollowUpError as error:
        return error_response(error)
    return Response(FollowUpSerializer(follow_up, context={"request": request}).data)


class ApproveFollowUpView(APIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        return _run(lambda: services.approve(pk, actor=request.user), request)


class RejectFollowUpView(APIView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        return _run(lambda: services.reject(pk, actor=request.user), request)


# Delivery ---------------------------------------------------------------------


class SendFollowUpView(APIView):
    """
    Reserves an APPROVED follow-up (APPROVED -> SENDING) and queues one delivery job, which is
    never retried. 202 with the attempt and job IDs; refusals (409 / 400 / 503) change nothing.
    """

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        try:
            attempt = FollowUpDeliveryService().request(pk, actor=request.user)
        except FollowUp.DoesNotExist:
            return not_found("Follow-up")
        except services.FollowUpError as error:
            return error_response(error)

        follow_up = FollowUp.objects.select_related("invoice").get(pk=pk)
        job = OutboxJob.objects.get(idempotency_key=f"{DELIVER_JOB_KIND}:{attempt.pk}")
        return Response(
            {
                "code": "queued",
                "detail": "Queued for delivery through Gmail. Do not resend while it is in progress.",
                "job_id": str(job.pk),
                "follow_up": FollowUpSerializer(follow_up, context={"request": request}).data,
                "delivery_attempt": DeliveryAttemptSerializer(attempt).data,
            },
            status=status.HTTP_202_ACCEPTED,
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


class ClientStatementView(APIView):
    """
    GET /api/clients/{id}/statement/: what a consolidated statement would list now (no AI, no writes).
    POST: a person asks for the statement draft; 202 with the job ID (the worker drafts it).
    """

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        from apps.invoices.models import Client

        from .statements import preview

        client = Client.objects.filter(pk=pk).first()
        if client is None:
            return not_found("Client")
        return Response(preview(client))

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        from apps.invoices.models import Client

        from . import statements

        try:
            job = statements.request(pk, actor=request.user)
        except Client.DoesNotExist:
            return not_found("Client")
        except services.FollowUpError as error:
            return error_response(error)
        return Response({"job_id": str(job.pk)}, status=status.HTTP_202_ACCEPTED)

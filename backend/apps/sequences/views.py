import uuid
from typing import Any

from django.db.models import QuerySet
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsApprover, ReadOperatorWriteAdmin
from apps.followups.services import FollowUpError

from . import services
from .models import Sequence, SequenceEnrollment
from .serializers import EnrollmentSerializer, SequenceSerializer


def _error(error: FollowUpError) -> Response:
    return Response({"code": error.code, "detail": error.message}, status=error.status_code)


def _not_found(what: str) -> Response:
    return Response({"code": "not_found", "detail": f"{what} not found."}, status=status.HTTP_404_NOT_FOUND)


def _body(request: Request) -> dict[str, Any]:
    return request.data if isinstance(request.data, dict) else {}


class SequenceListView(APIView):
    """GET: all versions with steps and enrollment counts. POST (ADMIN): a new version."""

    permission_classes = [ReadOperatorWriteAdmin]

    def get(self, request: Request) -> Response:
        sequences = Sequence.objects.prefetch_related("steps")
        return Response(SequenceSerializer(sequences, many=True).data)

    def post(self, request: Request) -> Response:
        data = _body(request)
        try:
            sequence = services.create_version(
                name=data.get("name", ""), steps=data.get("steps"), make_default=bool(data.get("make_default")),
                actor=request.user,
            )
        except FollowUpError as error:
            return _error(error)
        return Response(SequenceSerializer(sequence).data, status=status.HTTP_201_CREATED)


class SequenceDetailView(APIView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        sequence = Sequence.objects.prefetch_related("steps").filter(pk=pk).first()
        return Response(SequenceSerializer(sequence).data) if sequence else _not_found("Sequence")


class EnrollmentListView(generics.ListAPIView):
    serializer_class = EnrollmentSerializer

    def get_queryset(self) -> QuerySet[SequenceEnrollment]:
        queryset = SequenceEnrollment.objects.select_related("invoice", "sequence").prefetch_related("sequence__steps")
        params = self.request.query_params
        if params.get("status"):
            queryset = queryset.filter(status=params["status"].upper())
        if params.get("review_required") == "true":
            queryset = queryset.filter(review_required=True)
        if params.get("invoice"):
            try:
                queryset = queryset.filter(invoice_id=uuid.UUID(params["invoice"]))
            except ValueError:
                return queryset.none()
        return queryset


class _EnrollmentAction(APIView):
    def act(self, request: Request, pk: uuid.UUID) -> SequenceEnrollment:
        raise NotImplementedError

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        try:
            enrollment = self.act(request, pk)
        except SequenceEnrollment.DoesNotExist:
            return _not_found("Enrollment")
        except FollowUpError as error:
            return _error(error)
        enrollment = SequenceEnrollment.objects.select_related("invoice", "sequence").get(pk=enrollment.pk)
        return Response(EnrollmentSerializer(enrollment).data)


class PauseEnrollmentView(_EnrollmentAction):
    def act(self, request: Request, pk: uuid.UUID) -> SequenceEnrollment:
        return services.pause(pk, reason=str(_body(request).get("reason", "")), actor=request.user)


class ResumeEnrollmentView(_EnrollmentAction):
    def act(self, request: Request, pk: uuid.UUID) -> SequenceEnrollment:
        return services.resume(pk, actor=request.user)


class ReviewEnrollmentView(_EnrollmentAction):
    """APPROVER: confirm or correct the position of an invoice whose earlier reminders don't match the sequence."""

    permission_classes = [IsApprover]

    def act(self, request: Request, pk: uuid.UUID) -> SequenceEnrollment:
        data = _body(request)
        return services.review(pk, last_completed_step=data.get("last_completed_step"),  # type: ignore[arg-type]
                               note=str(data.get("note", "")), actor=request.user)

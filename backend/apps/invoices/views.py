import datetime as dt

from django.conf import settings
from django.db.models import Prefetch, QuerySet, Sum
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AutomationRun, ChaseHold, Invoice
from .serializers import AutomationRunSerializer, InvoiceSerializer
from .sheet_parser import EXPECTED_HEADERS
from .sync import start_sync

RECENT_RUNS_LIMIT = 20


class SyncView(APIView):
    """Records a sync run and queues it for the worker: 202 with the run and job IDs."""

    def post(self, request: Request) -> Response:
        run, job = start_sync(actor=request.user)
        return Response(
            {"run_id": run.pk, "job_id": str(job.pk), "run": AutomationRunSerializer(run).data},
            status=status.HTTP_202_ACCEPTED,
        )


INVOICES = Invoice.objects.prefetch_related(
    "enrollments__sequence__steps",
    "promises",
    "disputes",
    Prefetch("chase_holds", queryset=ChaseHold.objects.filter(released_at__isnull=True), to_attr="active_chase_holds"),
)


class InvoiceListView(generics.ListAPIView):
    queryset = INVOICES
    serializer_class = InvoiceSerializer


class InvoiceDetailView(generics.RetrieveAPIView):
    queryset = INVOICES
    serializer_class = InvoiceSerializer


class AutomationRunListView(generics.ListAPIView):
    serializer_class = AutomationRunSerializer

    def get_queryset(self) -> QuerySet[AutomationRun]:
        queryset = AutomationRun.objects.all()
        kind = self.request.query_params.get("kind")
        if kind:
            queryset = queryset.filter(kind=kind.upper())
        return queryset[:RECENT_RUNS_LIMIT]


def _amounts_by_currency(invoices: QuerySet[Invoice]) -> list[dict[str, str]]:
    # Amounts are only ever summed within a single currency.
    rows = invoices.values("currency").annotate(total=Sum("amount")).order_by("currency")
    return [{"currency": row["currency"], "amount": f"{row['total']:.2f}"} for row in rows]


def _ai_budget() -> dict[str, object]:
    from apps.followups import ai_budget

    now = timezone.now()
    budget = ai_budget.status(now)
    return {
        "used_24h": budget.used_24h,
        "limit": budget.limit,
        "cooldown_until": budget.cooldown_until if budget.cooldown_until and budget.cooldown_until > now else None,
        # Why automatic drafting is paused right now ('' when it may run).
        "automatic_drafting_blocked": budget.blocked_reason(now),
    }


class DashboardView(APIView):
    def get(self, request: Request) -> Response:
        unpaid = Invoice.objects.filter(status=Invoice.Status.UNPAID)
        overdue = unpaid.filter(due_date__lt=timezone.localdate())
        last_sync = (
            AutomationRun.objects.filter(kind=AutomationRun.Kind.SYNC, status=AutomationRun.Status.SUCCEEDED)
            .values_list("completed_at", flat=True).first()
        )
        # v2 §11: sequence ticks keep using the last good sync; the UI warns once it is this old.
        stale = last_sync is None or timezone.now() - last_sync > dt.timedelta(hours=settings.STALE_DATA_HOURS)

        return Response(
            {
                "total_invoices": Invoice.objects.count(),
                "unpaid_invoices": unpaid.count(),
                "overdue_invoices": overdue.count(),
                "outstanding_amounts_by_currency": _amounts_by_currency(unpaid),
                "overdue_amounts_by_currency": _amounts_by_currency(overdue),
                "expected_columns": list(EXPECTED_HEADERS),
                "last_successful_sync_at": last_sync,
                "stale": stale,
                "stale_after_hours": settings.STALE_DATA_HOURS,
                "ai_budget": _ai_budget(),
            }
        )

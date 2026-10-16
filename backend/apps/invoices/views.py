from django.db.models import QuerySet, Sum
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AutomationRun, Invoice
from .serializers import AutomationRunSerializer, InvoiceSerializer
from .sheet_parser import EXPECTED_HEADERS
from .sync import sync_invoices

RECENT_RUNS_LIMIT = 20


class SyncView(APIView):
    """Runs a synchronous Google Sheets sync and returns the resulting AutomationRun."""

    def post(self, request: Request) -> Response:
        run = sync_invoices(trigger=AutomationRun.Trigger.MANUAL)
        data = AutomationRunSerializer(run).data

        if run.status == AutomationRun.Status.SUCCEEDED:
            return Response(data, status=status.HTTP_200_OK)
        if run.errors:
            # The sheet was read but contained invalid rows; nothing was applied.
            return Response(data, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        # Retrieval from Google (or saving) failed.
        return Response(data, status=status.HTTP_502_BAD_GATEWAY)


class InvoiceListView(generics.ListAPIView):
    queryset = Invoice.objects.all()
    serializer_class = InvoiceSerializer


class InvoiceDetailView(generics.RetrieveAPIView):
    queryset = Invoice.objects.all()
    serializer_class = InvoiceSerializer


class AutomationRunListView(generics.ListAPIView):
    serializer_class = AutomationRunSerializer

    def get_queryset(self) -> QuerySet[AutomationRun]:
        return AutomationRun.objects.all()[:RECENT_RUNS_LIMIT]


def _amounts_by_currency(invoices: QuerySet[Invoice]) -> list[dict[str, str]]:
    # Amounts are only ever summed within a single currency.
    rows = invoices.values("currency").annotate(total=Sum("amount")).order_by("currency")
    return [{"currency": row["currency"], "amount": f"{row['total']:.2f}"} for row in rows]


class DashboardView(APIView):
    def get(self, request: Request) -> Response:
        unpaid = Invoice.objects.filter(status=Invoice.Status.UNPAID)
        overdue = unpaid.filter(due_date__lt=timezone.localdate())

        return Response(
            {
                "total_invoices": Invoice.objects.count(),
                "unpaid_invoices": unpaid.count(),
                "overdue_invoices": overdue.count(),
                "outstanding_amounts_by_currency": _amounts_by_currency(unpaid),
                "overdue_amounts_by_currency": _amounts_by_currency(overdue),
                "expected_columns": list(EXPECTED_HEADERS),
            }
        )

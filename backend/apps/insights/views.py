from typing import Any

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cases.models import DisputeCase, PromiseToPay
from apps.followups.eligibility import ineligibility
from apps.inbox.models import InboundMessage, Task
from apps.invoices.models import Client, Invoice

from . import jobs, worklist


def _metrics(client: Client) -> dict[str, Any]:
    factors = client.risk_factors or {}
    return {
        "id": str(client.pk), "display_name": client.display_name, "email": client.email,
        "risk_score": client.risk_score, "low_confidence": client.risk_low_confidence,
        "factors": factors.get("factors", []), "top_factors": worklist.top_factors(client),
        "outstanding": factors.get("outstanding", {}), "oldest_days_overdue": factors.get("oldest_days_overdue"),
        "avg_days_late": client.avg_days_late, "promise_kept_rate": client.promise_kept_rate,
        "response_rate": client.response_rate, "computed_at": client.metrics_computed_at,
        "link": worklist.client_link(client.pk),
    }


class WorklistView(APIView):
    """GET /api/worklist/: clients ranked by risk x outstanding within each currency (M13). Read-only."""

    def get(self, request: Request) -> Response:
        return Response(worklist.build(request.user))


class ClientListView(APIView):
    """GET /api/clients/: every client with its stored score and metrics, highest score first."""

    def get(self, request: Request) -> Response:
        clients = sorted(Client.objects.all(), key=lambda c: (-(c.risk_score or -1), c.email))
        return Response([_metrics(c) for c in clients])


class ClientDetailView(APIView):
    """GET /api/clients/{id}/: score breakdown, metrics, invoices, promises, disputes, replies and tasks."""

    def get(self, request: Request, pk: Any) -> Response:
        client = Client.objects.filter(pk=pk).first()
        if client is None:
            return Response({"code": "not_found", "detail": "Client not found."}, status=status.HTTP_404_NOT_FOUND)
        invoices = Invoice.objects.filter(client=client).order_by("due_date", "external_id")
        open_promises = {p.invoice_id: p for p in PromiseToPay.objects.filter(client=client, status=PromiseToPay.Status.OPEN)}
        open_disputes = {d.invoice_id: d for d in DisputeCase.objects.filter(client=client, status=DisputeCase.Status.OPEN)}
        data = _metrics(client)
        # Identity is the normalized email (v2 §5); every name the sheet used for it is listed, never merged further.
        data["invoice_names"] = sorted({i.client_name for i in invoices})
        data["invoices"] = [{
            "id": str(i.pk), "external_id": i.external_id, "amount": str(i.amount), "currency": i.currency,
            "due_date": i.due_date, "status": i.status, "in_sheet": i.in_sheet, "link": f"/?invoice={i.pk}",
            "blockers": [message for code, message in ineligibility(i) if code not in ("not_overdue", "not_unpaid")]
            if i.status == Invoice.Status.UNPAID else [],
            "open_promise_id": str(open_promises[i.pk].pk) if i.pk in open_promises else None,
            "open_dispute_id": str(open_disputes[i.pk].pk) if i.pk in open_disputes else None,
        } for i in invoices]
        data["promises"] = [{
            "id": str(p.pk), "invoice_external_id": p.invoice.external_id, "status": p.status,
            "promised_date": p.promised_date, "created_at": p.created_at, "resolved_at": p.resolved_at,
            "link": f"/?invoice={p.invoice_id}",
        } for p in PromiseToPay.objects.filter(client=client).select_related("invoice").order_by("-created_at")]
        data["disputes"] = [{
            "id": str(d.pk), "invoice_external_id": d.invoice.external_id, "status": d.status, "reason": d.reason,
            "created_at": d.created_at, "resolved_at": d.resolved_at, "link": f"/disputes?case={d.pk}",
        } for d in DisputeCase.objects.filter(client=client).select_related("invoice").order_by("-created_at")]
        data["replies"] = [{
            "id": str(r.pk), "invoice_external_id": r.invoice.external_id, "received_at": r.received_at,
            "state": r.state, "label": r.label, "link": f"/inbox?reply={r.pk}",
        } for r in InboundMessage.objects.filter(client=client).select_related("invoice").order_by("-received_at")[:20]]
        data["open_tasks"] = [{
            "id": str(t.pk), "title": t.title, "due_at": t.due_at,
            "invoice_external_id": t.invoice.external_id if t.invoice else None,
            "link": f"/tasks?task={t.pk}",
        } for t in Task.objects.filter(client=client, status=Task.Status.OPEN).select_related("invoice").order_by("due_at", "id")]
        return Response(data)


class RecomputeView(APIView):
    """POST /api/clients/recompute/: queue a recompute (the worker runs it). 202 with the job ID."""

    def post(self, request: Request) -> Response:
        job = jobs.request_recompute(trigger="manual", created_by=request.user)
        return Response({"job_id": str(job.pk), "status": job.status}, status=status.HTTP_202_ACCEPTED)

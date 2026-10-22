from typing import Any

from rest_framework import serializers

from .models import AutomationRun, ChaseHold, Invoice


def active_holds_summary(invoice: Invoice) -> list[dict[str, Any]]:
    """Active chase holds (M10): why chasing is stopped, and the reply to review."""
    holds = getattr(invoice, "active_chase_holds", None)
    if holds is None:
        holds = list(ChaseHold.objects.filter(invoice=invoice, released_at__isnull=True))
    return [{"id": str(h.pk), "reason": h.reason, "reason_display": h.get_reason_display(), "protected": h.protected,
             "source": h.source, "release_rule": h.release_rule,
             "reply_id": str(h.reply_id) if h.reply_id else None,
             "promise_id": str(h.promise_id) if h.promise_id else None,
             "dispute_id": str(h.dispute_id) if h.dispute_id else None,
             "detail": h.detail, "created_at": h.created_at}
            for h in sorted(holds, key=lambda h: h.created_at)]


def open_case_summary(invoice: Invoice) -> dict[str, Any]:
    """The invoice's OPEN promise and dispute (M11), read through reverse relations (prefetched in lists)."""
    promise = next((p for p in invoice.promises.all() if p.status == "OPEN"), None)  # type: ignore[attr-defined]
    dispute = next((d for d in invoice.disputes.all() if d.status == "OPEN"), None)  # type: ignore[attr-defined]
    return {
        "open_promise": {"id": str(promise.pk), "promised_date": promise.promised_date, "amount": str(promise.amount),
                         "currency": promise.currency} if promise else None,
        "open_dispute": {"id": str(dispute.pk), "reason": dispute.reason,
                         "reason_display": dispute.get_reason_display()} if dispute else None,
    }


class InvoiceSerializer(serializers.ModelSerializer):
    is_overdue = serializers.BooleanField(read_only=True)
    enrollment = serializers.SerializerMethodField()
    holds = serializers.SerializerMethodField()
    open_promise = serializers.SerializerMethodField()
    open_dispute = serializers.SerializerMethodField()

    def get_holds(self, invoice: Invoice) -> list[dict[str, Any]]:
        return active_holds_summary(invoice)

    def get_open_promise(self, invoice: Invoice) -> dict[str, Any] | None:
        return open_case_summary(invoice)["open_promise"]

    def get_open_dispute(self, invoice: Invoice) -> dict[str, Any] | None:
        return open_case_summary(invoice)["open_dispute"]

    def get_enrollment(self, invoice: Invoice) -> dict[str, Any] | None:
        """The invoice's latest sequence enrollment, summarised (None if never enrolled)."""
        enrollments = sorted(invoice.enrollments.all(), key=lambda e: e.created_at, reverse=True)
        if not enrollments:
            return None
        e = enrollments[0]
        total = len(e.sequence.steps.all())
        return {
            "id": str(e.pk), "status": e.status, "last_completed_step": e.last_completed_step,
            "pending_step": e.pending_step, "next_step": e.next_step, "total_steps": total,
            "next_action_at": e.next_action_at, "blocked_reason": e.blocked_reason, "pause_reason": e.pause_reason,
            "retry_after": e.retry_after,
            "exit_reason": e.exit_reason, "review_required": e.review_required, "review_reason": e.review_reason,
        }
    days_overdue = serializers.IntegerField(read_only=True)
    client_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "external_id",
            "client_id",
            "client_name",
            "client_email",
            "amount",
            "currency",
            "due_date",
            "status",
            "is_overdue",
            "days_overdue",
            "in_sheet",
            "last_synced_at",
            "enrollment",
            "holds",
            "open_promise",
            "open_dispute",
        ]
        read_only_fields = fields


class AutomationRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationRun
        fields = [
            "id",
            "kind",
            "trigger",
            "status",
            "started_at",
            "completed_at",
            "records_processed",
            "created_count",
            "updated_count",
            "unchanged_count",
            "rejected_count",
            "error_summary",
            "errors",
            "sync_run",
            "invoices_evaluated",
            "invoices_overdue",
            "drafts_generated",
            "invoices_skipped",
            "generation_failures",
            "finalized_at",
        ]
        read_only_fields = fields

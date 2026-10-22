from typing import Any

from rest_framework import serializers

from .models import AutomationRun, Invoice


class InvoiceSerializer(serializers.ModelSerializer):
    is_overdue = serializers.BooleanField(read_only=True)
    enrollment = serializers.SerializerMethodField()

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
